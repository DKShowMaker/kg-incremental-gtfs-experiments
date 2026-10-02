import redis.clients.jedis.Jedis;

import java.io.File;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.security.Permission;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 分布式增量更新实验的常驻worker。
 * 主循环：BRPOPLPUSH任务队列->tasks:processing（防崩溃丢任务）->
 * 读实体key列表并记下当前版本号(HMGET) -> 同一JVM内直接调Main.main()映射本分区
 * （第0步实测可安全重复，见dist/src/MainExitProbe.java）->
 * EVALSHA单个Lua脚本原子提交（全部key比对通过才统一HINCRBY，否则整体失败）->
 * 成功LPUSH结果队列并从processing移除；失败/冲突则抖动退避后整任务放回队列，
 * 超过MAX_ATTEMPTS进死信列表。
 *
 * 可靠性机制：
 *  - exit-guard（SecurityManager）：RMLMapper错误路径会System.exit(1)带崩宿主JVM，
 *    guard把退出企图拦截成可捕获的SecurityException（第0步补充探针
 *    ExitGuardProbe实测有效；需-Djava.security.manager=allow，JDK 24起SM整体移除，
 *    升级后此兜底失效，退化为输入预检+容器重启）。
 *  - 超时重投：后台守护线程扫描tasks:processing，claimed_at超过STALE_MS的任务
 *    视为worker已死，放回队列重投（attempts+1）。重复处理的正确性由版本号OCC保证；
 *    输出文件写竞争在STALE_MS远大于单任务实际耗时的前提下可忽略（见README局限）。
 */
public class Worker {

    static final String REDIS_HOST = env("REDIS_HOST", "redis");
    static final String MODE = env("CONSISTENCY_MODE", "occ"); // occ=乐观版本号 | lock=全局锁
    static final String LOCK_KEY = "commit:global_lock";
    static final long LOCK_WAIT_CAP_MS = 60_000;
    static final int REDIS_PORT = Integer.parseInt(env("REDIS_PORT", "6379"));
    static final String TASK_QUEUE = "tasks:queue";
    static final String PROCESSING = "tasks:processing";
    static final String RESULT_QUEUE = "results:done";
    static final String DEAD_QUEUE = "tasks:dead";
    static final String VERSIONS = "versions";
    static final long STALE_MS = Long.parseLong(env("STALE_MS", "120000"));
    static final int MAX_ATTEMPTS = 3;
    static final Set<String> NUMERIC_FIELDS = Set.of("attempts", "claimed_at",
            "lock_wait_ms", "lock_contention", "mapped_ms", "mapped_finished_at_ms",
            "barrier_k");

    /**
     * 原子提交脚本。KEYS[1]=versions哈希名，ARGV=[k1,v1,k2,v2,...]。
     * 第一遍校验全部key当前版本（缺失视为"0"），任一不匹配立即返回0；
     * 全部匹配后第二遍统一HINCRBY。整个脚本在Redis服务端原子执行，
     * 不存在拆成"先读、再判断、再写"三步之间的竞态窗口。
     */
    static final String COMMIT_LUA =
            "local i = 1 "
            + "while ARGV[i] do "
            + "  local cur = redis.call('HGET', KEYS[1], ARGV[i]) "
            + "  if cur == false then cur = '0' end "
            + "  if cur ~= ARGV[i + 1] then return 0 end "
            + "  i = i + 2 "
            + "end "
            + "i = 1 "
            + "while ARGV[i] do "
            + "  redis.call('HINCRBY', KEYS[1], ARGV[i], 1) "
            + "  i = i + 2 "
            + "end "
            + "return 1";

    /** 拦截System.exit：把进程死亡变成可捕获的SecurityException。其余权限全放行。 */
    static void installExitGuard(String workerId) {
        try {
            System.setSecurityManager(new SecurityManager() {
                @Override public void checkExit(int status) {
                    throw new SecurityException("exit-guard拦截System.exit(" + status + ")");
                }
                @Override public void checkPermission(Permission perm) { /* 全放行 */ }
            });
            log(workerId, "exit-guard已安装");
        } catch (Throwable e) {
            log(workerId, "WARN: exit-guard安装失败，退化为输入预检+容器重启兜底: " + e);
        }
    }

    public static void main(String[] args) throws Exception {
        String workerId = System.getenv().getOrDefault("HOSTNAME", "worker");
        installExitGuard(workerId);
        try (Jedis jedis = new Jedis(REDIS_HOST, REDIS_PORT)) {
            String sha = jedis.scriptLoad(COMMIT_LUA);
            log(workerId, "connected redis=" + REDIS_HOST + ":" + REDIS_PORT
                    + ", commit_script_sha=" + sha);

            Thread staleWatcher = new Thread(() -> {
                // 必须独立连接：Jedis实例非线程安全，共享会让两个线程的RESP应答串线
                try (Jedis wj = new Jedis(REDIS_HOST, REDIS_PORT)) {
                    watchStale(wj, workerId);
                }
            });
            staleWatcher.setDaemon(true);
            staleWatcher.start();

            while (true) {
                // BRPOPLPUSH：领取即转入processing列表，崩溃后任务仍可见、可被重投
                String raw = jedis.brpoplpush(TASK_QUEUE, PROCESSING, 0);
                if (raw == null) continue;
                // 领取即盖claimed_at时间戳（超时重投判据）；此后一切重试/死信都操作盖章串，
                // 保证processing列表不残留泄漏条目
                Map<String, String> t0 = parseTask(raw);
                t0.put("claimed_at", String.valueOf(System.currentTimeMillis()));
                String stamped = toJson(t0);
                jedis.lrem(PROCESSING, 1, raw);
                jedis.lpush(PROCESSING, stamped);
                try {
                    handle(jedis, sha, stamped, workerId);
                } catch (Throwable e) {
                    log(workerId, "task error: " + e);
                    requeue(jedis, stamped, workerId);
                }
            }
        }
    }

    static void handle(Jedis jedis, String sha, String json, String workerId) throws Exception {
        Map<String, String> t = parseTask(json); // json已由主循环盖过claimed_at
        String id = t.get("id");
        String stamped = json;

        int attempts = Integer.parseInt(t.getOrDefault("attempts", "0"));
        if (attempts >= MAX_ATTEMPTS) {
            log(workerId, id + ": exceeded max attempts -> dead");
            jedis.lrem(PROCESSING, 1, stamped);
            jedis.lpush(DEAD_QUEUE, json);
            return;
        }
        List<String> keys = readKeys(t.get("keys"));
        if (keys.isEmpty()) {
            log(workerId, id + ": empty keys file, finish directly");
            Files.write(Paths.get(t.get("output")), new byte[0]);
            t.put("mapped_ms", "0");
            t.put("mapped_finished_at_ms", String.valueOf(System.currentTimeMillis()));
            t.put("mapped_worker_id", workerId);
            t.put("worker_id", workerId);
            finish(jedis, t, stamped);
            return;
        }

        // 映射是输入的纯函数：重试(attempts>0)且输出已存在时跳过重新映射，
        // 只重做提交阶段——避免把整个分区的重算成本记到一次冲突头上。
        boolean needMap = !(Integer.parseInt(t.getOrDefault("attempts", "0")) > 0
                && new File(t.get("output")).isFile());

        String[] rmlArgs = new String[]{
                "-m", t.get("mapping"), "-o", t.get("output"), "-s", "nquads"};
        boolean isLock = MODE.equals("lock");

        // occ模式：版本号快照保持在映射之前（保留"宽检测窗口"的原始语义）
        List<String> expected = null;
        if (!isLock) {
            expected = jedis.hmget(VERSIONS, keys.toArray(new String[0]));
        }

        // 映射（输入的纯函数）：重试且输出已存在时跳过重复计算
        if (needMap) {
            if (!new File(t.get("mapping")).isFile()) {
                throw new java.io.IOException("mapping不存在: " + t.get("mapping"));
            }
            long mapStart = System.nanoTime();
            try {
                be.ugent.rml.cli.Main.main(rmlArgs);
            } catch (Throwable e) {
                throw new RuntimeException("RMLMapper抛出异常: " + e, e);
            }
            if (!new File(t.get("output")).isFile()) {
                throw new java.io.IOException("RMLMapper未产生输出: " + t.get("output"));
            }
            t.put("mapped_ms", String.valueOf((System.nanoTime() - mapStart) / 1_000_000));
            t.put("mapped_finished_at_ms", String.valueOf(System.currentTimeMillis()));
            t.put("mapped_worker_id", workerId);
            String mappedStamped = toJson(t);
            jedis.lrem(PROCESSING, 1, stamped);
            jedis.lpush(PROCESSING, mappedStamped);
            stamped = mappedStamped;
        } else {
            t.putIfAbsent("mapped_ms", "0");
            t.putIfAbsent("mapped_finished_at_ms", String.valueOf(System.currentTimeMillis()));
            t.putIfAbsent("mapped_worker_id", workerId);
        }
        t.put("worker_id", workerId);

        // 消融实验提交栅栏：必须位于映射之后、提交之前——各分区映射时长不同，
        // 若栅栏放在映射前，放行后仍会因映射耗时差异再次错开提交瞬间
        // （首轮实测踩坑：栅栏同时放行后LOCK-TRY仍散布1.16s）。
        // 全员到齐才同时放行进入提交阶段；重试直接通过(go标志已在)。
        if ("1".equals(t.getOrDefault("barrier", "0"))
                && "0".equals(t.getOrDefault("attempts", "0"))) {
            String runId = id.contains("-p") ? id.substring(0, id.lastIndexOf("-p")) : id;
            long rd = jedis.incr("abl:ready:" + runId);
            int bk = Integer.parseInt(t.getOrDefault("barrier_k", "4"));
            if (rd < bk) {
                long dl = System.currentTimeMillis() + 30_000;
                while (!jedis.exists("abl:go:" + runId) && System.currentTimeMillis() < dl) {
                    Thread.sleep(2);
                }
            } else {
                jedis.set("abl:go:" + runId, String.valueOf(System.currentTimeMillis()));
            }
            log(workerId, "BARRIER-PASSED " + System.currentTimeMillis() + " " + id);
        }

        if (isLock) {
            long waitStart = System.currentTimeMillis();
            int contention = 0;
            log(workerId, "LOCK-TRY " + waitStart + " " + id);
            while (true) {
                long tTry = System.currentTimeMillis();
                String got = jedis.set(LOCK_KEY, workerId,
                        redis.clients.jedis.params.SetParams.setParams().nx().ex(30));
                if ("OK".equals(got)) {
                    long gotAt = System.currentTimeMillis();
                    log(workerId, "LOCK-GOT " + gotAt + " " + id
                            + " wait=" + (gotAt - waitStart) + "ms contention=" + contention);
                    break;
                }
                contention++;
                log(workerId, "LOCK-FAIL " + tTry + " " + id + " n=" + contention);
                if (System.currentTimeMillis() - waitStart > LOCK_WAIT_CAP_MS)
                    throw new RuntimeException("全局锁等待超时");
                Thread.sleep(5 + new Random().nextInt(10));
            }
            long waited = System.currentTimeMillis() - waitStart;
            try {
                jedis.incr("commit:count"); // 锁内提交记账（模拟临界区写图谱的动作）
            } finally {
                jedis.del(LOCK_KEY);
            }
            log(workerId, id + ": lock-committed, wait=" + waited
                    + "ms contention=" + contention + ", entities=" + keys.size());
            Map<String, String> done = new LinkedHashMap<>(t);
            done.putIfAbsent("status", "ok");
            done.put("lock_wait_ms", String.valueOf(waited));
            done.put("lock_contention", String.valueOf(contention));
            jedis.lpush(RESULT_QUEUE, toJson(done));
            jedis.lrem(PROCESSING, 1, stamped);
            return;
        }

        // 【第0步实测结论，见dist/src/MainExitProbe.java与ExitGuardProbe.java】
        // 提交：单Lua脚本原子完成全部key的比对+递增
        List<String> argv = new ArrayList<>();
        for (int i = 0; i < keys.size(); i++) {
            argv.add(keys.get(i));
            String exp = expected.get(i) == null ? "0" : expected.get(i);
            argv.add(exp);
        }
        Object ok = jedis.evalsha(sha, Collections.singletonList(VERSIONS), argv);
        if (Long.valueOf(1L).equals(ok)) {
            log(workerId, id + ": committed, entities=" + keys.size()
                    + ", attempts=" + t.getOrDefault("attempts", "0"));
            finish(jedis, t, stamped);
        } else {
            log(workerId, id + ": version conflict -> requeue");
            requeue(jedis, stamped, workerId);
        }
    }

    static List<String> readKeys(String path) throws Exception {
        List<String> keys = new ArrayList<>();
        for (String line : Files.readAllLines(Paths.get(path))) {
            String k = line.trim();
            if (!k.isEmpty()) keys.add(k);
        }
        return keys;
    }

    static void finish(Jedis jedis, Map<String, String> t, String stamped) {
        Map<String, String> done = new LinkedHashMap<>(t);
        done.putIfAbsent("status", "ok");
        jedis.lpush(RESULT_QUEUE, toJson(done));
        jedis.lrem(PROCESSING, 1, stamped);
    }

    /** 抖动退避后整任务放回队列；attempts达到上限进死信列表。任何路径都先离开processing。 */
    static void requeue(Jedis jedis, String stamped, String workerId) throws Exception {
        Map<String, String> t = parseTask(stamped);
        int attempts = Integer.parseInt(t.getOrDefault("attempts", "0")) + 1;
        jedis.lrem(PROCESSING, 1, stamped);
        if (attempts >= MAX_ATTEMPTS) {
            log(workerId, t.get("id") + ": give up after attempt#" + attempts + " -> dead");
            jedis.lpush(DEAD_QUEUE, toJson(t));
            return;
        }
        t.put("attempts", String.valueOf(attempts));
        Thread.sleep(200 + new Random().nextInt(400));
        jedis.lpush(TASK_QUEUE, toJson(t));
    }

    /** 后台守护线程：claimed_at超过STALE_MS仍在processing中的任务视为owner已死，重投。 */
    static void watchStale(Jedis jedis, String workerId) {
        while (true) {
            try {
                Thread.sleep(10_000);
                long now = System.currentTimeMillis();
                for (String j : jedis.lrange(PROCESSING, 0, -1)) {
                    Map<String, String> t = parseTask(j);
                    long claimed = Long.parseLong(t.getOrDefault("claimed_at", "0"));
                    if (claimed <= 0 || now - claimed < STALE_MS) continue;
                    int attempts = Integer.parseInt(t.getOrDefault("attempts", "0")) + 1;
                    if (jedis.lrem(PROCESSING, 1, j) != 1) continue; // 别的watcher已处理
                    if (attempts >= MAX_ATTEMPTS) {
                        log(workerId, t.get("id") + ": stale redelivery超限 -> dead");
                        jedis.lpush(DEAD_QUEUE, toJson(t));
                        continue;
                    }
                    t.put("attempts", String.valueOf(attempts));
                    log(workerId, t.get("id") + ": claimed_at超过" + STALE_MS
                            + "ms视为owner已死 -> 重投(attempt#" + attempts + ")");
                    jedis.lpush(TASK_QUEUE, toJson(t));
                }
            } catch (InterruptedException e) {
                return;
            } catch (Exception e) {
                log(workerId, "stale-watcher error: " + e);
            }
        }
    }

    /**
     * 最小JSON解析：字段值均为受控ASCII路径/id（不含引号与反斜杠），
     * 正则提取即可，避免为worker引入额外依赖。数字字段单独处理。
     */
    static Map<String, String> parseTask(String json) {
        Map<String, String> m = new LinkedHashMap<>();
        Matcher strFields = Pattern.compile("\"([a-z_]+)\"\\s*:\\s*\"([^\"]*)\"").matcher(json);
        while (strFields.find()) {
            m.put(strFields.group(1), strFields.group(2));
        }
        Matcher nums = Pattern.compile("\"([a-z_]+)\"\\s*:\\s*(\\d+)").matcher(json);
        while (nums.find()) {
            m.put(nums.group(1), nums.group(2));
        }
        m.putIfAbsent("attempts", "0");
        return m;
    }

    static String toJson(Map<String, String> m) {
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, String> e : m.entrySet()) {
            if (!first) sb.append(',');
            first = false;
            boolean numeric = NUMERIC_FIELDS.contains(e.getKey());
            sb.append('"').append(e.getKey()).append("\":");
            if (!numeric) sb.append('"');
            sb.append(e.getValue());
            if (!numeric) sb.append('"');
        }
        return sb.append('}').toString();
    }

    static String env(String k, String d) {
        String v = System.getenv(k);
        return v == null ? d : v;
    }

    static synchronized void log(String workerId, String msg) {
        System.out.println(java.time.LocalTime.now() + " [" + workerId + "] " + msg);
    }
}
