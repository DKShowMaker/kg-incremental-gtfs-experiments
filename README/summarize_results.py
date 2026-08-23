#!/usr/bin/env python3
"""
汇总run_full_reconstruction.sh产出的/usr/bin/time -v日志，按round_tag汇总，
输出均值±标准差。

用法：
  python3 summarize_results.py --results-dir results --tags seed1,seed2,seed3,seed4,seed5
"""
import argparse
import csv
import re
import statistics
from pathlib import Path

ELAPSED_PATTERN = re.compile(r"Elapsed \(wall clock\) time.*?:\s*([0-9:.]+)")
MEM_PATTERN = re.compile(r"Maximum resident set size \(kbytes\):\s*(\d+)")


def parse_elapsed(value):
    parts = value.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"无法解析耗时: {value}")


def parse_log(path):
    content = Path(path).read_text(encoding="utf-8")
    elapsed_match = ELAPSED_PATTERN.search(content)
    mem_match = MEM_PATTERN.search(content)
    if not elapsed_match or not mem_match:
        raise ValueError(f"无法从{path}解析耗时/内存")
    return parse_elapsed(elapsed_match.group(1)), int(mem_match.group(1)) / 1024


def read_triples(results_dir, tag):
    path = Path(results_dir) / f"triples_{tag}.txt"
    if not path.is_file():
        return None
    return int(path.read_text(encoding="utf-8").strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--tags", required=True, help="逗号分隔的round_tag列表，如seed1,seed2,...")
    args = parser.parse_args()

    rows = []
    for tag in args.tags.split(","):
        log_path = Path(args.results_dir) / f"time_{tag}.log"
        if not log_path.is_file():
            print(f"[警告] 找不到 {log_path}")
            continue
        elapsed_sec, mem_mb = parse_log(log_path)
        triples = read_triples(args.results_dir, tag)
        rows.append({"tag": tag, "time_s": elapsed_sec, "mem_mb": mem_mb, "triples": triples})

    if not rows:
        print("没有可汇总的结果")
        return

    times = [r["time_s"] for r in rows]
    mems = [r["mem_mb"] for r in rows]

    out_path = Path(args.results_dir) / "summary_full_reconstruction.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["tag", "time_s", "mem_mb", "triples"])
        writer.writeheader()
        writer.writerows(rows)

    print("各次结果：")
    for r in rows:
        print(f"  {r['tag']}: 耗时 {r['time_s']:.2f}s, 内存 {r['mem_mb']:.1f}MB, 三元组 {r['triples']}")

    print(f"\n全量重构：耗时 {statistics.mean(times):.2f}"
          f"±{statistics.stdev(times) if len(times) > 1 else 0.0:.2f}s，"
          f"内存 {statistics.mean(mems):.1f}"
          f"±{statistics.stdev(mems) if len(mems) > 1 else 0.0:.1f}MB（{len(rows)}次独立seed）")
    print(f"CSV汇总: {out_path}")


if __name__ == "__main__":
    main()
