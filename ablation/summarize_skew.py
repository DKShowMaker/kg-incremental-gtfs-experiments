#!/usr/bin/env python3
"""Summarize the fixed-workload partition-skew experiment matrix."""
import argparse
import csv
import json
import statistics
from pathlib import Path


P_CODES = ("000", "025", "050", "075", "100")
SEEDS = (1, 2, 3, 4, 5)


def read_log(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    return values


def mean_sd(values):
    mean = statistics.mean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return mean, sd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", default="results")
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--first-seed", type=int, default=1,
                        help="seed excluded from steady-state summaries")
    args = parser.parse_args()
    if args.k < 1:
        raise ValueError("k must be positive")

    results = Path(args.results_dir)
    raw = []
    for code in P_CODES:
        p = int(code) / 100
        for seed in SEEDS:
            tag = f"skew_k{args.k}_p{code}_s{seed}"
            single_path = results / f"time_incremental_{tag}.log"
            distributed_path = results / f"time_distributed_{tag}.log"
            if not single_path.is_file() or not distributed_path.is_file():
                raise FileNotFoundError(f"missing paired timing logs for {tag}")
            single = read_log(single_path)
            distributed = read_log(distributed_path)
            serial_elapsed = float(single["elapsed_seconds"])
            distributed_elapsed = float(distributed["elapsed_seconds"])
            delta_rows = int(single["delta_rows"])
            partition_rows = int(distributed["partition_rows"])
            if delta_rows != partition_rows:
                raise ValueError(f"{tag}: serial delta_rows={delta_rows}, "
                                 f"distributed partition_rows={partition_rows}")
            counts = [int(distributed[f"partition_{i}_rows"])
                      for i in range(args.k)]
            if sum(counts) != delta_rows:
                raise ValueError(f"{tag}: per-partition rows do not sum to ΔD")
            raw.append({
                "p_code": code,
                "p": p,
                "k": args.k,
                "seed": seed,
                "included_in_summary": seed != args.first_seed,
                "delta_rows": delta_rows,
                "partition_rows": json.dumps(counts, separators=(",", ":")),
                "theoretical_s": 1 + (args.k - 1) * p,
                "measured_s": args.k * counts[0] / delta_rows if delta_rows else 0.0,
                "mapped_ms": json.dumps(
                    [int(distributed[f"mapped_ms_p{i}"]) for i in range(args.k)],
                    separators=(",", ":")),
                "mapped_finished_at_ms": json.dumps(
                    [int(distributed[f"mapped_finished_at_ms_p{i}"])
                     for i in range(args.k)], separators=(",", ":")),
                "mapped_workers": json.dumps(
                    [distributed[f"mapped_worker_p{i}"] for i in range(args.k)],
                    separators=(",", ":")),
                "mapped_duration_spread_ms": int(distributed["mapped_duration_spread_ms"]),
                "mapped_finish_spread_ms": int(distributed["mapped_finish_spread_ms"]),
                "single_thread_seconds": serial_elapsed,
                "distributed_seconds": distributed_elapsed,
                "speedup": serial_elapsed / distributed_elapsed,
                "tag": tag,
            })

    all_delta_rows = {row["delta_rows"] for row in raw}
    if len(all_delta_rows) != 1:
        raise ValueError(f"ΔD row count varies across conditions: {sorted(all_delta_rows)}")

    raw_path = results / f"skew_raw_k{args.k}.csv"
    with raw_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(raw[0]))
        writer.writeheader()
        writer.writerows(raw)

    summary = []
    for code in P_CODES:
        steady = [row for row in raw
                  if row["p_code"] == code and row["seed"] != args.first_seed]
        p = int(code) / 100
        summary_row = {"p_code": code, "p": p, "k": args.k,
                       "theoretical_s": 1 + (args.k - 1) * p,
                       "steady_seeds": len(steady), "delta_rows": next(iter(all_delta_rows))}
        for metric in ("measured_s", "mapped_duration_spread_ms",
                       "mapped_finish_spread_ms", "single_thread_seconds",
                       "distributed_seconds", "speedup"):
            mean, sd = mean_sd([float(row[metric]) for row in steady])
            summary_row[f"{metric}_mean"] = mean
            summary_row[f"{metric}_sd"] = sd
        summary.append(summary_row)

    summary_path = results / f"skew_summary_k{args.k}.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)

    print(f"ΔD固定为 {next(iter(all_delta_rows))} 行；seed{args.first_seed}按约定不计入稳态均值。")
    print("p    理论s    实测s(mean±sd)    映射时长差ms    完成时刻差ms    单线程s    分布式s    加速比")
    for row in summary:
        print(f"{row['p']:.2f}  "
              f"{row['theoretical_s']:.3f}  "
              f"{row['measured_s_mean']:.3f}±{row['measured_s_sd']:.3f}  "
              f"{row['mapped_duration_spread_ms_mean']:.0f}±"
              f"{row['mapped_duration_spread_ms_sd']:.0f}  "
              f"{row['mapped_finish_spread_ms_mean']:.0f}±"
              f"{row['mapped_finish_spread_ms_sd']:.0f}  "
              f"{row['single_thread_seconds_mean']:.2f}  "
              f"{row['distributed_seconds_mean']:.2f}  "
              f"{row['speedup_mean']:.3f}±{row['speedup_sd']:.3f}")
    print(f"逐轮结果：{raw_path}")
    print(f"汇总结果：{summary_path}")


if __name__ == "__main__":
    main()
