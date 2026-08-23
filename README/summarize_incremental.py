#!/usr/bin/env python3
"""
汇总run_incremental_update.sh产出的耗时日志（elapsed_seconds: X 格式），
按seed汇总，输出均值±标准差。

用法：
  python3 summarize_incremental.py --results-dir results --tags seed1,seed2,seed3,seed4,seed5
"""
import argparse
import statistics
from pathlib import Path


def parse_elapsed(path):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("elapsed_seconds:"):
            return float(line.split(":", 1)[1].strip())
    raise ValueError(f"未找到elapsed_seconds字段: {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--tags", required=True, help="逗号分隔的round_tag列表，如seed1,seed2,...")
    args = parser.parse_args()

    times = []
    for tag in args.tags.split(","):
        log_path = Path(args.results_dir) / f"time_incremental_{tag}.log"
        if not log_path.is_file():
            print(f"[警告] 找不到 {log_path}")
            continue
        times.append(parse_elapsed(log_path))

    if not times:
        print("没有可汇总的结果")
        return

    mean = statistics.mean(times)
    std = statistics.stdev(times) if len(times) > 1 else 0.0
    print(f"单线程增量更新：{mean:.2f}±{std:.2f}s（{len(times)}次独立seed）")
    print("各次耗时:", [f"{t:.2f}" for t in times])


if __name__ == "__main__":
    main()
