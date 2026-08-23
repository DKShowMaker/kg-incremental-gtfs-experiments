#!/usr/bin/env python3
"""
对比before/after两份GTFS数据快照，按主键计算ΔD：
- delta_dir：新增的行 + 更新后的行（要重新映射、插入图谱）
- stale_dir：被删除的行 + 更新前的旧行（要从图谱中移除）

适用前提：mapping文件中这些文件之间没有join（用
  grep -c "joinCondition" <mapping文件> 和 grep -c "parentTriplesMap" <mapping文件>
确认结果都为0）。有join的话，这里的逐文件独立diff会漏掉"父行变了、子行没变但
引用了旧值"这种间接受影响的情况，需要额外加扇出计算，本脚本没有实现。

用法：
  python3 compute_delta.py --before data/before --after data/after_seed1 \
      --delta-dir results/work_seed1/delta --stale-dir results/work_seed1/stale
"""
import argparse
import csv
import shutil
from collections import Counter
from pathlib import Path

# GTFS官方规范给出的各文件天然组合键。用之前建议先用实际数据核对唯一性
# （脚本运行时会自动打印警告，但不会阻止执行——不唯一的key对应的diff结果
# 需要人工判断是否可信，见README里的相关说明）。
KEY_COLUMNS = {
    "agency.txt": ["agency_id"],
    "routes.txt": ["route_id"],
    "trips.txt": ["trip_id"],
    "stops.txt": ["stop_id"],
    "stop_times.txt": ["trip_id", "stop_sequence"],
    "calendar.txt": ["service_id"],
    "calendar_dates.txt": ["service_id", "date"],
    "shapes.txt": ["shape_id", "shape_pt_sequence"],
    "frequencies.txt": ["trip_id", "start_time"],
    "feed_info.txt": [],
}


def load_rows(path):
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader), reader.fieldnames or []


def save_rows(path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def row_key(row, key_cols):
    return "|".join(str(row.get(c, "")) for c in key_cols)


def check_key_uniqueness(rows, key_cols, label):
    counts = Counter(row_key(r, key_cols) for r in rows)
    dup = {k: c for k, c in counts.items() if c > 1}
    if dup:
        print(f"[警告] {label}: {len(dup)}组key不唯一（涉及{sum(dup.values())}行），"
              f"这些key对应的diff结果可能不准确，建议人工抽查")


def diff_file(before_path, after_path, key_cols):
    before_rows, fieldnames = load_rows(before_path)
    after_rows, _ = load_rows(after_path)
    check_key_uniqueness(before_rows, key_cols, f"{before_path.name}(before)")
    check_key_uniqueness(after_rows, key_cols, f"{after_path.name}(after)")

    before_map = {row_key(r, key_cols): r for r in before_rows}
    after_map = {row_key(r, key_cols): r for r in after_rows}

    inserted = [after_map[k] for k in after_map if k not in before_map]
    deleted_keys = [k for k in before_map if k not in after_map]
    updated, updated_old_keys = [], []
    for k in after_map:
        if k in before_map and after_map[k] != before_map[k]:
            updated.append(after_map[k])
            updated_old_keys.append(k)

    removed_keys = deleted_keys + updated_old_keys
    stale_rows = [before_map[k] for k in removed_keys]
    return inserted, updated, stale_rows, fieldnames


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--delta-dir", required=True)
    parser.add_argument("--stale-dir", required=True)
    args = parser.parse_args()

    before_dir, after_dir = Path(args.before), Path(args.after)
    delta_dir, stale_dir = Path(args.delta_dir), Path(args.stale_dir)
    delta_dir.mkdir(parents=True, exist_ok=True)
    stale_dir.mkdir(parents=True, exist_ok=True)

    for f in after_dir.glob("*.txt"):
        shutil.copy(f, delta_dir / f.name)

    for after_file in sorted(after_dir.glob("*.txt")):
        name = after_file.name
        before_file = before_dir / name
        key_cols = KEY_COLUMNS.get(name)
        if not before_file.is_file() or not key_cols:
            print(f"[跳过] {name}（无对照文件或未配置主键，delta_dir中保留原样）")
            continue

        inserted, updated, stale_rows, fieldnames = diff_file(before_file, after_file, key_cols)

        delta_rows = inserted + updated
        if delta_rows:
            save_rows(delta_dir / name, delta_rows, fieldnames)
        if stale_rows:
            save_rows(stale_dir / name, stale_rows, fieldnames)

        print(f"{name}: insert={len(inserted)}, update={len(updated)}, stale(待删除旧行)={len(stale_rows)}")

    print(f"\n[完成] delta写入 {delta_dir}，stale写入 {stale_dir}")


if __name__ == "__main__":
    main()
