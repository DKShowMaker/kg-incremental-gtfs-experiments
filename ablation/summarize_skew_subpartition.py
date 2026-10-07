#!/usr/bin/env python3
"""Summarize paired unsplit/split timings for a fixed skew-input cohort."""
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
    return (statistics.mean(values),
            statistics.stdev(values) if len(values) > 1 else 0.0)


def load_condition(results, cohort, code, seed, mode, k):
    tag = f"{cohort}_p{code}_s{seed}_{mode}"
    log = read_log(results / f"time_distributed_{tag}.log")
    rows = int(log["partition_rows"])
    counts = [int(log[f"partition_{i}_rows"]) for i in range(k)]
    if sum(counts) != rows:
        raise ValueError(f"{tag}: partition counts do not sum to total")
    tasks = json.loads(log["task_metrics_json"])
    if sum(task["rows"] for task in tasks) != rows:
        raise ValueError(f"{tag}: task rows do not sum to total")
    return {
        "tag": tag,
        "elapsed_s": float(log["elapsed_seconds"]),
        "rows": rows,
        "unique_rows": rows - int(log["conflict_dup_rows"]),
        "duplicate_rows": int(log["conflict_dup_rows"]),
        "conflict_rate": float(log.get("conflict_rate", "0")),
        "total_retries": int(log["total_retries"]),
        "barrier_participants": int(log.get("barrier_participants", "0")),
        "counts": counts,
        "measured_skew": k * counts[0] / rows if rows else 0.0,
        "task_count": int(log["task_count"]),
        "worker_load_spread_ms": int(log["mapped_worker_load_spread_ms"]),
        "mapped_finish_spread_ms": int(log["mapped_finish_spread_ms"]),
        "task_metrics": tasks,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--cohort", required=True)
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--threshold", type=float, default=1.7)
    parser.add_argument("--factor", type=int, default=4)
    parser.add_argument("--conflict-rate", type=float, default=0.0)
    parser.add_argument("--first-seed", type=int, default=1)
    args = parser.parse_args()
    if args.k < 1 or args.factor < 2:
        raise ValueError("k must be positive and factor must be at least 2")

    results = Path(args.results_dir)
    raw = []
    for code in P_CODES:
        for seed in SEEDS:
            base = load_condition(results, args.cohort, code, seed, "unsplit", args.k)
            split = load_condition(results, args.cohort, code, seed, "split", args.k)
            if base["rows"] != split["rows"] or base["counts"] != split["counts"]:
                raise ValueError(f"p={code}, seed={seed}: split changed the delta partition")
            if (base["unique_rows"] != split["unique_rows"]
                    or base["duplicate_rows"] != split["duplicate_rows"]
                    or base["conflict_rate"] != split["conflict_rate"]):
                raise ValueError(f"p={code}, seed={seed}: split changed conflict injection")
            for partition_id, count in enumerate(split["counts"]):
                children = [task for task in split["task_metrics"]
                            if task["partition_id"] == partition_id
                            and task["subpartition_id"] is not None]
                skew = args.k * count / split["rows"] if split["rows"] else 0.0
                if skew > args.threshold and count > 1 and len(children) < 2:
                    raise ValueError(f"p={code}, seed={seed}: expected partition {partition_id} split")
                if (skew <= args.threshold or count <= 1) and children:
                    raise ValueError(f"p={code}, seed={seed}: unexpected partition {partition_id} split")
                if children and sum(task["rows"] for task in children) != count:
                    raise ValueError(f"p={code}, seed={seed}: child rows do not cover partition {partition_id}")
            raw.append({
                "p_code": code,
                "p": int(code) / 100,
                "k": args.k,
                "seed": seed,
                "included_in_summary": seed != args.first_seed,
                "delta_rows": base["rows"],
                "unique_delta_rows": base["unique_rows"],
                "injected_duplicate_rows": base["duplicate_rows"],
                "conflict_rate": base["conflict_rate"],
                "measured_s": base["measured_skew"],
                "unsplit_task_count": base["task_count"],
                "split_task_count": split["task_count"],
                "unsplit_seconds": base["elapsed_s"],
                "split_seconds": split["elapsed_s"],
                "paired_speedup_unsplit_over_split": base["elapsed_s"] / split["elapsed_s"],
                "latency_reduction_pct": 100 * (base["elapsed_s"] - split["elapsed_s"]) / base["elapsed_s"],
                "unsplit_worker_load_spread_ms": base["worker_load_spread_ms"],
                "split_worker_load_spread_ms": split["worker_load_spread_ms"],
                "unsplit_total_retries": base["total_retries"],
                "split_total_retries": split["total_retries"],
                "unsplit_barrier_participants": base["barrier_participants"],
                "split_barrier_participants": split["barrier_participants"],
                "unsplit_mapped_finish_spread_ms": base["mapped_finish_spread_ms"],
                "split_mapped_finish_spread_ms": split["mapped_finish_spread_ms"],
                "mapped_delta_verified": True,
                "unsplit_tag": base["tag"],
                "split_tag": split["tag"],
            })

    row_counts = {row["delta_rows"] for row in raw}
    if len(row_counts) != 1:
        raise ValueError(f"delta row count varies across conditions: {sorted(row_counts)}")
    unique_row_counts = {row["unique_delta_rows"] for row in raw}
    duplicate_row_counts = {row["injected_duplicate_rows"] for row in raw}
    if len(unique_row_counts) != 1 or len(duplicate_row_counts) != 1:
        raise ValueError("unique delta or injected duplicate row count varies across conditions")
    expected_duplicates = round(next(iter(unique_row_counts)) * args.conflict_rate)
    if duplicate_row_counts != {expected_duplicates}:
        raise ValueError(
            f"injected duplicate rows {duplicate_row_counts} != expected {expected_duplicates}")

    raw_path = results / f"skew_subpartition_raw_k{args.k}_{args.cohort}.csv"
    with raw_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(raw[0]))
        writer.writeheader()
        writer.writerows(raw)

    summary = []
    metrics = (
        "unsplit_seconds", "split_seconds", "paired_speedup_unsplit_over_split",
        "latency_reduction_pct", "unsplit_worker_load_spread_ms",
        "split_worker_load_spread_ms", "unsplit_mapped_finish_spread_ms",
        "split_mapped_finish_spread_ms", "unsplit_total_retries",
        "split_total_retries",
    )
    for code in P_CODES:
        steady = [row for row in raw
                  if row["p_code"] == code and row["seed"] != args.first_seed]
        row = {"p_code": code, "p": int(code) / 100, "k": args.k,
               "delta_rows": next(iter(row_counts)),
               "unique_delta_rows": next(iter(unique_row_counts)),
               "injected_duplicate_rows": expected_duplicates,
               "conflict_rate": args.conflict_rate,
               "measured_s_mean": statistics.mean(x["measured_s"] for x in steady),
               "unsplit_tasks": statistics.mean(x["unsplit_task_count"] for x in steady),
               "split_tasks": statistics.mean(x["split_task_count"] for x in steady),
               "steady_seeds": len(steady)}
        for metric in metrics:
            avg, sd = mean_sd([float(x[metric]) for x in steady])
            row[f"{metric}_mean"] = avg
            row[f"{metric}_sd"] = sd
        summary.append(row)

    summary_path = results / f"skew_subpartition_summary_k{args.k}_{args.cohort}.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)

    print(f"唯一ΔD固定为{next(iter(unique_row_counts))}行，冲突副本{expected_duplicates}行，"
          f"任务映射总行数{next(iter(row_counts))}；seed{args.first_seed}不计入稳态均值。")
    print("p     s       tasks(before/after)  unsplit(s)  split(s)  speedup  retries(before/after)")
    for row in summary:
        print(f"{row['p']:.2f}  {row['measured_s_mean']:.3f}  "
              f"{row['unsplit_tasks']:.1f}/{row['split_tasks']:.1f}  "
              f"{row['unsplit_seconds_mean']:.3f}±{row['unsplit_seconds_sd']:.3f}  "
              f"{row['split_seconds_mean']:.3f}±{row['split_seconds_sd']:.3f}  "
              f"{row['paired_speedup_unsplit_over_split_mean']:.3f}±"
              f"{row['paired_speedup_unsplit_over_split_sd']:.3f}×  "
              f"{row['unsplit_total_retries_mean']:.1f}→"
              f"{row['split_total_retries_mean']:.1f}")
    print(f"Raw paired data: {raw_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
