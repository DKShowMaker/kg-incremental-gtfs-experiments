#!/usr/bin/env python3
"""
把RML映射文件里数据源引用（xxx:source "文件名"）改写成指向指定目录下的
对应文件，这样同一份映射文件可以复用于before/after/delta/stale等不同快照，
不用为每份数据复制一份映射文件手改路径。

前缀写成 [A-Za-z0-9]+:source 而不是写死 rml:source，是因为不同来源/不同
翻译工具生成的映射文件，rml前缀名不一定叫rml（之前遇到过自动生成成ns2这种），
这样不管前缀叫什么名字都能匹配到。

用法：
  python3 normalize_mapping.py --mapping mapping.ttl --data-dir data/after_seed1 \
      --output work/mapping_seed1.ttl
"""
import argparse
import re
from pathlib import Path

SOURCE_RE = re.compile(r'([A-Za-z0-9]+:source\s+")(?:/data/|data/)?([^"]+?)(")')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    mapping_path = Path(args.mapping)
    data_dir = Path(args.data_dir).resolve()
    output_path = Path(args.output)

    if not mapping_path.is_file():
        raise FileNotFoundError(f"映射文件不存在: {mapping_path}")
    if not data_dir.is_dir():
        raise FileNotFoundError(f"数据目录不存在: {data_dir}")

    content = mapping_path.read_text(encoding="utf-8")

    def resolve_path(data_dir, filename):
        exact = data_dir / filename
        if exact.is_file():
            return exact
        stem = Path(filename).stem.lower()
        candidates = [p for p in sorted(data_dir.iterdir()) if p.is_file() and p.stem.lower() == stem]
        if len(candidates) > 1:
            raise FileNotFoundError(f"{filename} 在{data_dir}下有多个候选文件，无法自动确定: {candidates}")
        if not candidates:
            raise FileNotFoundError(f"映射引用了{filename}，{data_dir}下找不到匹配文件")
        src = candidates[0]
        # RMLMapper按扩展名识别数据源内容类型，.txt等扩展名不被支持（报
        # "Unrecognised content type: text/plain"），镜像成.csv软链接再引用。
        # 镜像放隐藏子目录，避免污染数据目录、避免和*.txt glob/stem匹配产生歧义。
        supported = {".csv", ".tsv", ".json", ".xml", ".n3", ".nt", ".nq", ".ttl",
                     ".jsonld", ".html", ".htm", ".xlsx"}
        if src.suffix.lower() not in supported:
            mirror_dir = data_dir / ".csv_mirrors"
            mirror_dir.mkdir(exist_ok=True)
            mirrored = mirror_dir / f"{src.stem}.csv"
            # 每次都重建镜像链接，自愈任何陈旧状态：change_generator的copytree
            # 会把源目录里的符号链接解引用成普通文件副本带进新快照（实测发生过：
            # 快照里的镜像变成before数据的拷贝，RMLMapper读到旧数据且无任何报错）。
            if mirrored.is_symlink() or mirrored.exists():
                mirrored.unlink()
            mirrored.symlink_to(src.resolve())
            return mirrored
        return src

    def repl(match):
        filename = Path(match.group(2)).name
        csv_path = resolve_path(data_dir, filename)
        return f'{match.group(1)}{csv_path.as_posix()}{match.group(3)}'

    new_content, n_replaced = SOURCE_RE.subn(repl, content)
    if n_replaced == 0:
        raise ValueError("没有找到可替换的source路径，检查映射文件里source的写法"
                          "是不是跟脚本里的正则匹配不上")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(new_content, encoding="utf-8")
    print(f"[done] 写入 {output_path}；替换了 {n_replaced} 处source路径")


if __name__ == "__main__":
    main()
