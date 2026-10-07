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
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from partitioning import partition_for_trip_id

STOPTIME_TEMPLATE = ("http://transport.linkeddata.es/madrid/metro/"
                     "stoptimes/{trip_id}-{stop_id}-{arrival_time}")
# 宿主机与容器内一致的共享卷绝对路径（见docker-compose.yml注释）
SHARED_ROOT = Path("/home/ztr/KG/dist_work")


def conflict_barrier_participants(task_specs, worker_count):
    return min(worker_count, sum(spec["rows"] > 0 for spec in task_specs))


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


def partition_delta(delta_file, k, out_root, conflict_rate=0.0, rng_seed=42,
                    subpartition_threshold=None, subpartition_factor=None):
    """按md5(trip_id)%k把ΔD行分到k个分区目录，并生成每分区的实体key列表。
    conflict_rate>0时（消融实验）：随机选中该比例的行，额外复制一份进相邻分区
    ((home+1)%k)——两个worker的实体key列表因此重叠，提交阶段必然发生版本冲突。
    复制导致合并后的delta含重复quad，由merge阶段的按行去重保证最终图谱正确。
    设置subpartition_threshold后，倾斜度k*partition_rows/total超过阈值的
    分区会按行均匀拆成多个独立数据/key文件；子任务共享Redis版本号空间，
    但各自只读取本子块的实体key。"""
    import random
    rows, fields = [], []
    with open(delta_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    if subpartition_threshold is not None:
        logical_keys = {(row["trip_id"], row["stop_sequence"]) for row in rows}
        if len(logical_keys) != len(rows):
            raise ValueError("subpartitioning requires unique trip_id|stop_sequence keys")

    def home_of(r):
        return partition_for_trip_id(r["trip_id"], k)

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
    total_rows = sum(len(partition) for partition in assign)
    factor = k if subpartition_factor is None else subpartition_factor
    if subpartition_threshold is not None:
        if subpartition_threshold <= 0:
            raise ValueError("subpartition_threshold must be positive")
        if factor < 2:
            raise ValueError("subpartition_factor must be at least 2")

    def write_partition_files(indices, directory):
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / "stop_times.txt", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for idx in indices:
                w.writerow(rows[idx])
        with open(directory / "keys.txt", "w", encoding="utf-8") as f:
            for idx in indices:
                r = rows[idx]
                f.write(f"{r['trip_id']}|{r['stop_sequence']}\n")

    task_specs = []
    for i in range(k):
        pdir = Path(out_root) / f"partition_{i}"
        counts[i] = len(assign[i])
        skew = k * counts[i] / total_rows if total_rows else 0.0
        should_split = (subpartition_threshold is not None
                        and skew > subpartition_threshold
                        and counts[i] > 1)
        chunks = min(factor, counts[i]) if should_split else 1
        if chunks == 1:
            write_partition_files(assign[i], pdir)
            task_specs.append({"partition_id": i, "subpartition_id": None,
                               "data_dir": pdir, "rows": counts[i]})
            continue

        base, extra = divmod(counts[i], chunks)
        offset = 0
        for sub_id in range(chunks):
            size = base + (1 if sub_id < extra else 0)
            indices = assign[i][offset:offset + size]
            offset += size
            child_dir = pdir / f"subpartition_{sub_id}"
            write_partition_files(indices, child_dir)
            task_specs.append({"partition_id": i, "subpartition_id": sub_id,
                               "data_dir": child_dir, "rows": len(indices)})
        if offset != counts[i]:
            raise AssertionError(f"subpartition row count mismatch for partition {i}")

    assert sum(counts) == len(rows) + dup_rows, "分区行数校验失败"
    return counts, dup_rows, task_specs


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
    ap.add_argument("--subpartition-threshold", type=float, default=None,
                    help="拆分倾斜度k*分区行数/总行数超过此值的分区；不传则关闭")
    ap.add_argument("--subpartition-factor", type=int, default=None,
                    help="热点分区最多拆成多少个子任务（默认等于k）")
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--output-graph", default=None,
                    help="最终N-Quads输出路径（默认results/kg_distributed_<tag>.nq）")
    ap.add_argument("--template", default=STOPTIME_TEMPLATE)
    args = ap.parse_args()
    if args.k < 1:
        ap.error("--k must be positive")
    if not 0 <= args.conflict_rate <= 1:
        ap.error("--conflict-rate must be between 0 and 1")
    if args.subpartition_threshold is not None and args.subpartition_threshold <= 0:
        ap.error("--subpartition-threshold must be positive")
    if args.subpartition_factor is not None and args.subpartition_factor < 2:
        ap.error("--subpartition-factor must be at least 2")
    if (args.subpartition_threshold is not None and args.subpartition_factor is None
            and args.k < 2):
        ap.error("--k must be at least 2 when subpartitioning is enabled")

    host, _, port = args.redis.rpartition(":")
    work = Path(args.results_dir) / f"work_dist_{args.tag}"
    shared_tag = SHARED_ROOT / args.tag
    time_log = Path(args.results_dir) / f"time_distributed_{args.tag}.log"
    out_graph = (Path(args.output_graph) if args.output_graph else
                 Path(args.results_dir) / f"kg_distributed_{args.tag}.nq")

    if not Path(args.baseline).is_file():
        sys.exit(f"找不到baseline图谱 {args.baseline}")
    if not Path(args.mapping).is_file():
        sys.exit(f"找不到窄化mapping {args.mapping}")

    skew_manifest_path = Path(args.after) / ".partition_skew.json"
    skew_manifest = None
    if skew_manifest_path.is_file():
        skew_manifest = json.loads(skew_manifest_path.read_text(encoding="utf-8"))
        generated_k = int(skew_manifest["partition_k"])
        if generated_k != args.k:
            sys.exit(f"倾斜输入按k={generated_k}生成，调度器收到k={args.k}；请按新k重新生成")

    t0 = time.time()

    # 1) diff：复用现有compute_delta.py
    sh([sys.executable, "compute_delta.py", "--before", args.before,
        "--after", args.after, "--delta-dir", work / "delta",
        "--stale-dir", work / "stale"])
    t_diff = time.time() - t0

    # 2) 分区 + 实体key列表（写入共享卷，容器内同路径可见）
    counts, dup_rows, task_specs = partition_delta(
        work / "delta" / "stop_times.txt", args.k, shared_tag,
        args.conflict_rate, args.rng_seed,
        args.subpartition_threshold, args.subpartition_factor)
    if skew_manifest is not None:
        expected = int(skew_manifest["mapped_delta_rows_expected"])
        actual = sum(counts) - dup_rows
        if actual != expected:
            sys.exit(f"倾斜输入预期ΔD={expected}行，实际分区前ΔD={actual}行")
    t_partition = time.time() - t0 - t_diff
    print(f"[partition] k={args.k}, 各分区行数={counts}")

    # 3) 每分区各跑一次normalize_mapping（窄化mapping本体与分区无关）
    t_norm_start = time.time()
    tasks, task_by_id = [], {}
    barrier_k = conflict_barrier_participants(task_specs, args.k)
    for spec in task_specs:
        i = spec["partition_id"]
        sub_id = spec["subpartition_id"]
        task_id = (f"{args.tag}-p{i}" if sub_id is None
                   else f"{args.tag}-p{i}-s{sub_id}")
        pdir = Path(spec["data_dir"])
        sh([sys.executable, "normalize_mapping.py",
            "--mapping", args.mapping, "--data-dir", pdir,
            "--output", pdir / "mapping.ttl"])
        tk = {"id": task_id,
              "mapping": str(pdir / "mapping.ttl"),
              "output": str(pdir / "output.nq"),
              "keys": str(pdir / "keys.txt"),
              "attempts": 0}
        if args.conflict_rate > 0 and barrier_k > 1:
            # 冲突注入时启用提交栅栏：各worker映射完成后在Redis栅栏处会合，
            # 全员到齐才同时放行进入提交阶段——否则分区大小差异使提交瞬间
            # 天然错开数百毫秒，全局锁永远观测不到真实竞争（已实测踩坑）。
            tk["barrier"] = "1"
            tk["barrier_k"] = barrier_k
        tasks.append(tk)
        task_by_id[task_id] = spec

    t_normalize = time.time() - t_norm_start

    # 4) 清空上次的队列/结果/版本状态，推任务
    rc = RespClient(host, int(port))
    assert rc.cmd("PING") == "PONG"
    for key in ("tasks:queue", "results:done", "tasks:dead",
                f"abl:ready:{args.tag}", f"abl:go:{args.tag}"):
        rc.cmd("DEL", key)
    if not args.keep_redis_state:
        rc.cmd("DEL", "versions")
    dispatch_tasks = tasks
    if args.subpartition_threshold is not None:
        # Start work from different parent partitions before draining sibling chunks.
        dispatch_tasks = sorted(tasks, key=lambda t: (
            0 if task_by_id[t["id"]]["subpartition_id"] is None
            else task_by_id[t["id"]]["subpartition_id"],
            task_by_id[t["id"]]["partition_id"]))
    for t in dispatch_tasks:
        rc.cmd("LPUSH", "tasks:queue", json.dumps(t))
    t_dispatch = time.time() - t0 - t_diff - t_partition - t_normalize
    print(f"[dispatch] 已推送{len(tasks)}个任务到tasks:queue")

    # 5) 阻塞等全部任务完成（按id先到先得：超时重投的陈旧双跑汇报被忽略）
    done_ids, outputs = set(), {}
    tot_retries = tot_lockwait = tot_contention = 0
    mapped_ms_by_partition = {i: 0 for i in range(args.k)}
    mapped_finish_by_partition = {i: 0 for i in range(args.k)}
    mapped_workers_by_partition = {i: set() for i in range(args.k)}
    mapped_ms_by_worker = {}
    task_results = {}
    deadline = args.timeout
    while len(done_ids) < len(tasks):
        item = rc.cmd("BLPOP", "results:done", deadline, timeout=deadline + 5)
        if item is None:
            dead = rc.cmd("LRANGE", "tasks:dead", 0, -1)
            sys.exit(f"[FATAL] 等待完成汇报超时({deadline}s)；死信队列={len(dead)}条")
        done = json.loads(item[1])
        if done["id"] in outputs:
            print(f"[result] 忽略{done['id']}的重复汇报（重投竞态，先到先得）")
            continue
        spec = task_by_id.get(done["id"])
        if spec is None:
            sys.exit(f"[FATAL] 收到未知任务完成消息: {done['id']}")
        done_ids.add(done["id"])
        outputs[done["id"]] = done["output"]
        part_id = spec["partition_id"]
        mapped_ms = int(done.get("mapped_ms", 0))
        mapped_finish = int(done.get("mapped_finished_at_ms", 0))
        mapped_worker = done.get("mapped_worker_id", done.get("worker_id", "unknown"))
        mapped_ms_by_partition[part_id] += mapped_ms
        mapped_finish_by_partition[part_id] = max(
            mapped_finish_by_partition[part_id], mapped_finish)
        mapped_workers_by_partition[part_id].add(mapped_worker)
        mapped_ms_by_worker[mapped_worker] = mapped_ms_by_worker.get(mapped_worker, 0) + mapped_ms
        task_results[done["id"]] = {
            "id": done["id"], "partition_id": part_id,
            "subpartition_id": spec["subpartition_id"], "rows": spec["rows"],
            "mapped_ms": mapped_ms, "mapped_finished_at_ms": mapped_finish,
            "worker": mapped_worker,
        }
        tot_retries += int(done.get("attempts", 0))
        tot_lockwait += int(done.get("lock_wait_ms", 0))
        tot_contention += int(done.get("lock_contention", 0))
        print(f"[result] {done['id']} status={done.get('status')} "
              f"attempts={done.get('attempts','0')} "
              f"mapped={mapped_ms}ms worker={mapped_worker} "
              f"lock_wait={done.get('lock_wait_ms','0')}ms "
              f"({len(done_ids)}/{len(tasks)})")
    if rc.cmd("LLEN", "tasks:dead") > 0:
        sys.exit("[FATAL] 死信队列非空，存在未成功分区")
    t_wait = time.time() - t0 - t_diff - t_partition - t_normalize - t_dispatch

    # 6) 合并k份输出 + stale + apply
    merged = work / "merged_delta.nq"
    ordered_tasks = sorted(tasks, key=lambda t: (
        task_by_id[t["id"]]["partition_id"],
        -1 if task_by_id[t["id"]]["subpartition_id"] is None
        else task_by_id[t["id"]]["subpartition_id"]))
    if args.conflict_rate > 0:
        seen = set(); kept = 0
        with open(merged, "w", encoding="utf-8") as out:
            for task in ordered_tasks:
                with open(outputs[task["id"]], encoding="utf-8") as f:
                    for line in f:
                        if line not in seen:
                            seen.add(line); out.write(line); kept += 1
        print(f"[merge] 冲突注入模式: 按行去重后保留{kept}条 -> {merged}")
    else:
        with open(merged, "wb") as out:
            for task in ordered_tasks:
                with open(outputs[task["id"]], "rb") as f:
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
    mapped_duration_spread = (max(mapped_ms_by_partition.values())
                              - min(mapped_ms_by_partition.values()))
    mapped_finish_spread = (max(mapped_finish_by_partition.values())
                            - min(mapped_finish_by_partition.values()))
    worker_load_spread = (max(mapped_ms_by_worker.values())
                          - min(mapped_ms_by_worker.values())) if mapped_ms_by_worker else 0
    with open(time_log, "w", encoding="utf-8") as f:
        f.write(f"tag: {args.tag}\n")
        f.write(f"k: {args.k}\n")
        f.write(f"subpartition_threshold: {args.subpartition_threshold if args.subpartition_threshold is not None else 'off'}\n")
        f.write(f"subpartition_factor: {args.k if args.subpartition_factor is None else args.subpartition_factor}\n")
        f.write(f"task_count: {len(tasks)}\n")
        f.write(f"conflict_rate: {args.conflict_rate}\n")
        f.write(f"barrier_participants: {barrier_k if args.conflict_rate > 0 else 0}\n")
        f.write(f"partition_rows: {sum(counts)}\n")
        f.write(f"partition0_share_skew_s: {args.k * counts[0] / sum(counts) if sum(counts) else 0:.6f}\n")
        for i, count in enumerate(counts):
            f.write(f"partition_{i}_rows: {count}\n")
        for i in range(args.k):
            f.write(f"mapped_ms_p{i}: {mapped_ms_by_partition[i]}\n")
            f.write(f"mapped_finished_at_ms_p{i}: {mapped_finish_by_partition[i]}\n")
            f.write(f"mapped_worker_p{i}: {','.join(sorted(mapped_workers_by_partition[i]))}\n")
        f.write(f"mapped_duration_spread_ms: {mapped_duration_spread}\n")
        f.write(f"mapped_finish_spread_ms: {mapped_finish_spread}\n")
        f.write(f"mapped_duration_spread_scope: parent_sum_of_task_durations\n")
        f.write(f"mapped_worker_load_ms: {json.dumps(mapped_ms_by_worker, sort_keys=True)}\n")
        f.write(f"mapped_worker_load_spread_ms: {worker_load_spread}\n")
        f.write(f"task_metrics_json: {json.dumps(list(task_results.values()), separators=(',', ':'))}\n")
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
