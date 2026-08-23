#!/usr/bin/env python3
"""
根据stale_dir里的旧行 + rr:template模板字符串，直接拼出待删除记录的主语IRI，
不需要重新跑一遍RMLMapper去问它这些旧行的IRI是什么（RMLMapper是确定性的，
同一行输入永远映射出同一个IRI，模板知道了就能直接算）。

模板通过find_iri_template.py查出来。

用法：
  python3 compute_stale_iris.py --stale-file results/work_seed1/stale/stop_times.txt \
      --template "http://transport.linkeddata.es/madrid/metro/stoptimes/{trip_id}-{stop_id}-{arrival_time}" \
      --output results/work_seed1/stale_iris.txt
"""
import argparse
import csv
import re

PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")


def iri_encode(value):
    """按RMLMapper的实际行为对模板变量值做百分号编码：RFC3986非保留字符
    （字母/数字/-._~）保持原样，其余字符编码为%XX（大写十六进制、UTF-8）。
    实测依据：CRTM数据中时间列的':'在baseline图谱subject里是'%3A'，
    '_'/'-'/'.'保持原样。不编码的话拼出的IRI和图谱里的对不上，删除会全部落空。
    """
    unreserved = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    out = []
    for byte in str(value).encode("utf-8"):
        ch = chr(byte)
        if ch in unreserved:
            out.append(ch)
        else:
            out.append(f"%{byte:02X}")
    return "".join(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stale-file", required=True)
    parser.add_argument("--template", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--append", action="store_true",
                         help="追加写入而不是覆盖（处理多个文件对应多个模板时使用）")
    args = parser.parse_args()

    placeholders = PLACEHOLDER_RE.findall(args.template)

    with open(args.stale_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if rows:
        missing_cols = [p for p in placeholders if p not in rows[0]]
        if missing_cols:
            raise KeyError(f"模板里的占位符在CSV中找不到对应列: {missing_cols}")

    encoded_rows = [{k: iri_encode(v) for k, v in row.items()} for row in rows]
    iris = [f"<{args.template.format(**row)}>" for row in encoded_rows]

    mode = "a" if args.append else "w"
    with open(args.output, mode, encoding="utf-8") as f:
        if iris:
            f.write("\n".join(iris) + "\n")

    print(f"[done] {len(iris)} 条待删除IRI写入 {args.output}")


if __name__ == "__main__":
    main()
