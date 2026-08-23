#!/usr/bin/env python3
"""
对一个GTFS CSV文件独立生成一轮变更（Insert/Update/Delete/Composite）。
独立seed设计：每次都从--input-dir这份未变更的基准数据出发，不链式依赖上一轮结果。

用法：
  python3 change_generator.py --input-dir data/before --output-dir data/after_seed1 \
      --target-file stop_times.txt --key-cols trip_id,stop_sequence \
      --update-cols arrival_time,departure_time --change-ratio 0.10 --seed 1 \
      --log results/changelog_seed1.csv

注意：
- --key-cols 用于生成变更日志里的old_key/new_key，方便人工核对，不是diff脚本的主键来源
  （compute_delta.py有自己独立配置的KEY_COLUMNS，两边如果都用GTFS官方组合键，应该一致）。
- Insert操作会把主键最后一列（通常是stop_sequence这类数值序号）替换成一个不会跟现有
  数据冲突的新值，保证生成的是真正的新记录，而不是复制一行只改时间、主键跟原行重复。
"""
import argparse
import csv
import random
import re
import shutil
from pathlib import Path

TIME_RE = re.compile(r"^\d+:[0-5]\d:[0-5]\d$")


def parse_cols(raw):
    return [c.strip() for c in raw.split(",") if c.strip()]


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


def shift_time(value, seconds):
    """返回偏移后的HH:MM:SS(H)字符串；如果value不是合法时间格式（例如频率制班次
    允许的空值），返回None，调用方据此跳过这个字段，不视为出错。"""
    if not value or not TIME_RE.match(value):
        return None
    h, m, s = map(int, value.split(":"))
    total = max(0, h * 3600 + m * 60 + s + seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def mutate_time_fields(row, update_cols, seconds):
    changed = []
    for col in update_cols:
        if col not in row:
            raise KeyError(f"更新列在目标CSV中不存在: {col}")
        new_val = shift_time(row[col], seconds)
        if new_val is None:
            continue
        row[col] = new_val
        changed.append(col)
    return changed


def make_new_key(row, key_cols, offset):
    """把主键最后一列换成一个新值，保证不和现有行的主键冲突。
    最后一列若是数字（如stop_sequence），直接加一个大偏移量；不是数字则拼后缀。"""
    row = dict(row)
    last_col = key_cols[-1]
    try:
        row[last_col] = str(int(row[last_col]) + 1_000_000 + offset)
    except (ValueError, TypeError):
        row[last_col] = f"{row[last_col]}_new{offset}"
    return row


def copy_tree(src, dst):
    if not src.is_dir():
        raise FileNotFoundError(f"输入目录不存在: {src}")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--target-file", default="stop_times.txt")
    parser.add_argument("--key-cols", required=True)
    parser.add_argument("--update-cols", default="arrival_time,departure_time")
    parser.add_argument("--change-ratio", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()

    if not 0 < args.change_ratio <= 1:
        raise ValueError("--change-ratio 必须在 (0, 1] 区间")

    random.seed(args.seed)
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    target_path = output_dir / args.target_file
    key_cols = parse_cols(args.key_cols)
    update_cols = parse_cols(args.update_cols)
    if not update_cols:
        raise ValueError("--update-cols 不能为空")

    copy_tree(input_dir, output_dir)
    rows, fieldnames = load_rows(target_path)
    if not rows:
        raise ValueError(f"{target_path} 没有数据行")

    missing = [c for c in key_cols if c not in fieldnames]
    if missing:
        raise KeyError(f"目标CSV缺少key列: {missing}")

    n_total = len(rows)
    n_change = max(1, round(n_total * args.change_ratio))
    n_insert = n_change // 4
    n_delete = n_change // 4
    n_update = n_change // 4
    n_composite = n_change - n_insert - n_delete - n_update

    if n_delete + n_update + n_composite > n_total:
        raise ValueError("目标文件行数太少，无法生成不重叠的变更操作")

    indices = list(range(n_total))
    random.shuffle(indices)
    delete_idx = set(indices[:n_delete])
    update_idx = set(indices[n_delete:n_delete + n_update])
    composite_idx = set(indices[n_delete + n_update:n_delete + n_update + n_composite])

    change_log = []
    new_rows = []

    for i, original in enumerate(rows):
        row = dict(original)
        old_key = row_key(row, key_cols)
        if i in delete_idx:
            change_log.append({"op": "delete", "file": args.target_file,
                                "old_key": old_key, "new_key": "", "changed_fields": ""})
            continue
        if i in update_idx:
            changed = mutate_time_fields(row, update_cols[:1], seconds=60 + args.seed)
            if changed:
                change_log.append({"op": "update", "file": args.target_file,
                                    "old_key": old_key, "new_key": row_key(row, key_cols),
                                    "changed_fields": ",".join(changed)})
        if i in composite_idx:
            changed = mutate_time_fields(row, update_cols, seconds=120 + args.seed)
            if changed:
                change_log.append({"op": "composite", "file": args.target_file,
                                    "old_key": old_key, "new_key": row_key(row, key_cols),
                                    "changed_fields": ",".join(changed)})
        new_rows.append(row)

    # 插入键唯一性保证：make_new_key的大偏移量不能保证不同insert之间不冲突
    # （两条base行主键最后一列恰好相差offset差值时会撞键，seed5实测发生过：
    # base 26/j=0和base 25/j=1都得到1500026）。撞键会导致compute_delta按主键
    # 建dict时静默丢行、增量与全量结果不一致。这里逐个尝试递增offset直到
    # 新键既不与原始数据冲突也不与已插入的新键冲突；循环不消耗随机数，
    # 不影响random流，无冲突的seed输出逐字节不变。
    existing_keys = {row_key(r, key_cols) for r in rows}
    used_new_keys = set()

    for j in range(n_insert):
        base_row = random.choice(rows)
        offset = args.seed * 100000 + j
        while True:
            base = make_new_key(base_row, key_cols, offset=offset)
            new_key = row_key(base, key_cols)
            if new_key not in existing_keys and new_key not in used_new_keys:
                break
            offset += 1
        used_new_keys.add(new_key)
        changed = mutate_time_fields(base, update_cols, seconds=3600 + args.seed + j * 60)
        new_rows.append(base)
        change_log.append({"op": "insert", "file": args.target_file,
                            "old_key": "", "new_key": row_key(base, key_cols),
                            "changed_fields": ",".join(changed)})

    save_rows(target_path, new_rows, fieldnames)

    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["op", "file", "old_key", "new_key", "changed_fields"])
        writer.writeheader()
        writer.writerows(change_log)

    print(f"[done] {args.target_file}: before={n_total}, insert={n_insert}, update={n_update}, "
          f"delete={n_delete}, composite={n_composite}, after={len(new_rows)}")


if __name__ == "__main__":
    main()
