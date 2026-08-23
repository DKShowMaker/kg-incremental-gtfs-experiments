#!/usr/bin/env python3
"""
baseline图谱 - stale_iris对应的所有quad + delta_triples = updated图谱。

按subject过滤：stale_iris列表里任何一个IRI作为subject的quad，全部从baseline
里移除，再把新映射出的delta三元组/四元组并进去。

前提：mapping文件中不使用blank node作为subject（用
  grep -c "BlankNode" <mapping文件>
确认结果为0）。是0，字符串层面按subject过滤等价于真正的RDF语义操作；不是0，
blank node标识符每次生成可能不同，这种按subject字符串过滤的方式会失效，需要
换成别的比对方式（不在本脚本处理范围内）。

用法：
  python3 apply_delta_to_graph.py --baseline results/baseline_graph.nq \
      --stale-iris results/work_seed1/stale_iris.txt \
      --delta results/work_seed1/delta_triples.nq \
      --output results/kg_incremental_seed1.nq
"""
import argparse


def get_subject(nquad_line):
    # N-Quads每行格式为 "subject predicate object [graph] ."，subject必然是
    # <IRI>或_:blanknode，不含未转义空格，按第一个空格切分是安全的。
    return nquad_line.split(" ", 1)[0]


def load_lines(path):
    with open(path, encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f if line.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--stale-iris", required=True,
                         help="待删除的subject IRI列表，每行一个，形如<http://...>")
    parser.add_argument("--delta", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    baseline_lines = load_lines(args.baseline)
    stale_iris = set(load_lines(args.stale_iris))
    delta_lines = load_lines(args.delta)

    kept = [line for line in baseline_lines if get_subject(line) not in stale_iris]
    removed_count = len(baseline_lines) - len(kept)

    updated = kept + delta_lines
    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(updated) + "\n")

    print(f"baseline={len(baseline_lines)}, removed(按subject)={removed_count}, "
          f"delta_added={len(delta_lines)}, updated={len(updated)}")


if __name__ == "__main__":
    main()
