#!/usr/bin/env python3
"""
分布式增量更新实验调度器（跑在宿主机，纯Python，零第三方依赖）。

数据流（对应README分布式实验一节）：
  compute_delta.py产出ΔD
    -> 按trip_id哈希(md5取模)分区成k份：每份独立数据目录+实体key列表文件
    -> 窄化mapping与分区无关：全局复用一份delta_mapping_base.ttl；
       但normalize_mapping.py要对每个分区各跑一次（指向各自数据目录）
    -> 任务{mapping路径,输出路径,keys路径,attempts}推入Redis队列
       （消息只传文件路径不搬数据；路径在宿主机与容器内保持一致，
        因为compose把dist_work挂载到相同的绝对路径）
    -> 空闲worker领任务：RMLMapper子进程映射 -> Lua原子提交版本号
    -> 调度器收齐k个完成汇报 -> 合并k份输出
    -> compute_stale_iris.py(与单线程版完全相同) -> apply_delta_to_graph.py套用baseline

计时口径与run_incremental_update.sh对齐：从diff(compute_delta)开始到apply结束。
实体key采用逻辑键trip_id|stop_sequence：跨update稳定，同一记录的并发更新能正确撞版本号。

用法：
  python3 run_distributed_update.py \
      --before data/before --after data/after_seed1 --tag seed1 --k 4 \
      --baseline results/baseline_graph.nq --mapping delta_mapping_base.ttl \
      [--redis localhost:6379] [--timeout 900] [--keep-redis-state]
"""
import argparse
import csv
import hashlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

STOPTIME_TEMPLATE = ("http://transport.linkeddata.es/madrid/metro/"
                     "stoptimes/{trip_id}-{stop_id}-{arrival_time}")
# 宿主机与容器内一致的共享卷绝对路径（见docker-compose.yml注释）
SHARED_ROOT = Path("/home/ztr/KG/dist_work")


class RespClient:
    """最小RESP-2客户端，只覆盖本实验需要的命令，避免引入pip依赖。"""

    def __init__(self, host, port, timeout=10):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buf = b""

    def cmd(self, *args, timeout=None):
        old = self.sock.gettimeout()
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            parts = [b"*" + str(len(args)).encode() + b"\r\n"]
            for a in args:
                a = str(a).encode()
                parts.append(b"$" + str(len(a)).encode() + b"\r\n" + a + b"\r\n")
            self.sock.sendall(b"".join(parts))
            return self._reply()
        finally:
            self.sock.settimeout(old)

    def _read_line(self):
        while b"\r\n" not in self.buf:
            d = self.sock.recv(65536)
            if not d:
                raise ConnectionError("redis连接关闭")
            self.buf += d
        line, self.buf = self.buf.split(b"\r\n", 1)
        return line

    def _read_exact(self, n):
        while len(self.buf) < n:
            d = self.sock.recv(65536)
            if not d:
                raise ConnectionError("redis连接关闭")
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _reply(self):
        t = self._read_exact(1)
        line = self._read_line()
        if t == b"+":
            return line.decode()
        if t == b"-":
            raise RuntimeError(f"redis错误: {line.decode()}")
        if t == b":":
            return int(line)
        if t == b"$":
            n = int(line)
            if n == -1:
                return None
            data = self._read_exact(n)
            self._read_exact(2)
            return data.decode()
        if t == b"*":
            n = int(line)
            if n == -1:
                return None
            return [self._reply() for _ in range(n)]
        raise RuntimeError(f"未知RESP类型 {t}")


def sh(cmd, **kw):
    print("[sh]", " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], check=True, **kw)
    return r


def partition_delta(delta_file, k, out_root, conflict_rate=0.0, rng_seed=42):
    """按md5(trip_id)%k把ΔD行分到k个分区目录，并生成每分区的实体key列表。
    conflict_rate>0时（消融实验）：随机选中该比例的行，额外复制一份进相邻分区
    ((home+1)%k)——两个worker的实体key列表因此重叠，提交阶段必然发生版本冲突。
    复制导致合并后的delta含重复quad，由merge阶段的按行去重保证最终图谱正确。"""
    import random
    rows, fields = [], []
    with open(delta_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)

    def home_of(r):
        return int(hashlib.md5(r["trip_id"].encode()).hexdigest(), 16) % k

    assign = [[] for _ in range(k)]
    for idx, r in enumerate(rows):
        assign[home_of(r)].append(idx)
    dup_rows = 0
    if conflict_rate > 0:
        rng = random.Random(rng_seed)
        cand = list(range(len(rows)))
        rng.shuffle(cand)
        n_dup = int(round(len(rows) * conflict_rate))
        for idx in cand[:n_dup]:
            nxt = (home_of(rows[idx]) + 1) % k
            if nxt != home_of(rows[idx]):
                assign[nxt].append(idx)
                dup_rows += 1

    counts = [0] * k
    for i in range(k):
        pdir = Path(out_root) / f"partition_{i}"
        pdir.mkdir(parents=True, exist_ok=True)
        with open(pdir / "stop_times.txt", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for idx in assign[i]:
                w.writerow(rows[idx])
                counts[i] += 1
        with open(pdir / "keys.txt", "w", encoding="utf-8") as f:
            for idx in assign[i]:
                r = rows[idx]
                f.write(f"{r['trip_id']}|{r['stop_sequence']}\n")
    assert sum(counts) == len(rows) + dup_rows, "分区行数校验失败"
    return counts, dup_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--mapping", default="delta_mapping_base.ttl",
                    help="窄化mapping（build_delta_mapping.py产物，与分区无关）")
    ap.add_argument("--redis", default="localhost:6379")
    ap.add_argument("--timeout", type=int, default=900,
                    help="等待全部分区完成的超时秒数")
    ap.add_argument("--keep-redis-state", action="store_true",
                    help="保留versions哈希（默认每次实验开始前清空，保证可重复）")
    ap.add_argument("--conflict-rate", type=float, default=0.0,
                    help="消融实验用：把该比例的ΔD行额外复制进相邻分区，人为制造跨worker版本冲突(0~1)")
    ap.add_argument("--rng-seed", type=int, default=42,
                    help="冲突注入的随机种子（A0/A2两轮必须一致以保证可比）")
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--template", default=STOPTIME_TEMPLATE)
    args = ap.parse_args()

    host, _, port = args.redis.rpartition(":")
    work = Path(args.results_dir) / f"work_dist_{args.tag}"
    shared_tag = SHARED_ROOT / args.tag
    time_log = Path(args.results_dir) / f"time_distributed_{args.tag}.log"
    out_graph = Path(args.results_dir) / f"kg_distributed_{args.tag}.nq"

    if not Path(args.baseline).is_file():
        sys.exit(f"找不到baseline图谱 {args.baseline}")
    if not Path(args.mapping).is_file():
        sys.exit(f"找不到窄化mapping {args.mapping}")

    t0 = time.time()

    # 1) diff：复用现有compute_delta.py
    sh([sys.executable, "compute_delta.py", "--before", args.before,
        "--after", args.after, "--delta-dir", work / "delta",
        "--stale-dir", work / "stale"])
    t_diff = time.time() - t0

    # 2) 分区 + 实体key列表（写入共享卷，容器内同路径可见）
    counts, dup_rows = partition_delta(work / "delta" / "stop_times.txt",
                                       args.k, shared_tag,
                                       args.conflict_rate, args.rng_seed)
    t_partition = time.time() - t0 - t_diff
    print(f"[partition] k={args.k}, 各分区行数={counts}")

    # 3) 每分区各跑一次normalize_mapping（窄化mapping本体与分区无关）
    t_norm_start = time.time()
    tasks = []
    for i in range(args.k):
        pdir = shared_tag / f"partition_{i}"
        sh([sys.executable, "normalize_mapping.py",
            "--mapping", args.mapping, "--data-dir", pdir,
            "--output", pdir / "mapping.ttl"])
        tk = {"id": f"{args.tag}-p{i}",
              "mapping": str(pdir / "mapping.ttl"),
              "output": str(pdir / "output.nq"),
              "keys": str(pdir / "keys.txt"),
              "attempts": 0}
        if args.conflict_rate > 0:
            # 冲突注入时启用提交栅栏：各worker映射完成后在Redis栅栏处会合，
            # 全员到齐才同时放行进入提交阶段——否则分区大小差异使提交瞬间
            # 天然错开数百毫秒，全局锁永远观测不到真实竞争（已实测踩坑）。
            tk["barrier"] = "1"
            tk["barrier_k"] = args.k
        tasks.append(tk)

    t_normalize = time.time() - t_norm_start

    # 4) 清空上次的队列/结果/版本状态，推任务
    rc = RespClient(host, int(port))
    assert rc.cmd("PING") == "PONG"
    for key in ("tasks:queue", "results:done", "tasks:dead",
                f"abl:ready:{args.tag}", f"abl:go:{args.tag}"):
        rc.cmd("DEL", key)
    if not args.keep_redis_state:
        rc.cmd("DEL", "versions")
    for t in tasks:
        rc.cmd("LPUSH", "tasks:queue", json.dumps(t))
    t_dispatch = time.time() - t0 - t_diff - t_partition - t_normalize
    print(f"[dispatch] 已推送{len(tasks)}个任务到tasks:queue")

    # 5) 阻塞等k个完成汇报（按id先到先得：超时重投的陈旧双跑汇报被忽略）
    done_ids, outputs = set(), {}
    tot_retries = tot_lockwait = tot_contention = 0
    deadline = args.timeout
    while len(done_ids) < args.k:
        item = rc.cmd("BLPOP", "results:done", deadline, timeout=deadline + 5)
        if item is None:
            dead = rc.cmd("LRANGE", "tasks:dead", 0, -1)
            sys.exit(f"[FATAL] 等待完成汇报超时({deadline}s)；死信队列={len(dead)}条")
        done = json.loads(item[1])
        if done["id"] in outputs:
            print(f"[result] 忽略{done['id']}的重复汇报（重投竞态，先到先得）")
            continue
        done_ids.add(done["id"])
        outputs[done["id"]] = done["output"]
        tot_retries += int(done.get("attempts", 0))
        tot_lockwait += int(done.get("lock_wait_ms", 0))
        tot_contention += int(done.get("lock_contention", 0))
        print(f"[result] {done['id']} status={done.get('status')} "
              f"attempts={done.get('attempts','0')} "
              f"lock_wait={done.get('lock_wait_ms','0')}ms "
              f"({len(done_ids)}/{args.k})")
    if rc.cmd("LLEN", "tasks:dead") > 0:
        sys.exit("[FATAL] 死信队列非空，存在未成功分区")
    t_wait = time.time() - t0 - t_diff - t_partition - t_normalize - t_dispatch

    # 6) 合并k份输出 + stale + apply
    merged = work / "merged_delta.nq"
    if args.conflict_rate > 0:
        seen = set(); kept = 0
        with open(merged, "w", encoding="utf-8") as out:
            for i in range(args.k):
                with open(outputs[f"{args.tag}-p{i}"], encoding="utf-8") as f:
                    for line in f:
                        if line not in seen:
                            seen.add(line); out.write(line); kept += 1
        print(f"[merge] 冲突注入模式: 按行去重后保留{kept}条 -> {merged}")
    else:
        with open(merged, "wb") as out:
            for i in range(args.k):
                with open(outputs[f"{args.tag}-p{i}"], "rb") as f:
                    while True:
                        chunk = f.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
        print(f"[merge] 合并完成 -> {merged}")

    # 7) stale IRI计算（与单线程版完全相同）
    stale_file = work / "stale" / "stop_times.txt"
    if stale_file.is_file():
        sh([sys.executable, "compute_stale_iris.py",
            "--stale-file", stale_file, "--template", args.template,
            "--output", work / "stale_iris.txt"])
    else:
        (work / "stale_iris.txt").write_text("")

    # 8) 应用到baseline图谱
    sh([sys.executable, "apply_delta_to_graph.py",
        "--baseline", args.baseline, "--stale-iris", work / "stale_iris.txt",
        "--delta", merged, "--output", out_graph])

    elapsed = time.time() - t0
    t_tail = elapsed - t_diff - t_partition - t_normalize - t_dispatch - t_wait
    with open(time_log, "w", encoding="utf-8") as f:
        f.write(f"tag: {args.tag}\n")
        f.write(f"k: {args.k}\n")
        f.write(f"partition_rows: {sum(counts)}\n")
        f.write(f"phase_compute_delta_s: {t_diff:.3f}\n")
        f.write(f"phase_partition_s: {t_partition:.3f}\n")
        f.write(f"phase_normalize_s: {t_normalize:.3f}\n")
        f.write(f"phase_dispatch_s: {t_dispatch:.3f}\n")
        f.write(f"phase_wait_workers_s: {t_wait:.3f}\n")
        f.write(f"phase_merge_stale_apply_s: {t_tail:.3f}\n")
        f.write(f"conflict_dup_rows: {dup_rows}\n")
        f.write(f"total_retries: {tot_retries}\n")
        f.write(f"lock_wait_ms_total: {tot_lockwait}\n")
        f.write(f"lock_contention_total: {tot_contention}\n")
        f.write(f"elapsed_seconds: {elapsed:.3f}\n")
    print(f"\n[done] 分布式增量更新完成: {out_graph}")
    print(f"[done] 端到端耗时 {elapsed:.2f}s -> {time_log}")


if __name__ == "__main__":
    main()
