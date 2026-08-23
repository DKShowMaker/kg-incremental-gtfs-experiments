#!/usr/bin/env python3
"""
基线线性化：遍历完整mapping的全部TriplesMap，把满足等价前提的join逐个替换为
等价的rr:template对象映射，不满足前提的join原样保留（并校验其保留成本可控）。
用于在大feed上构建baseline图谱——避免朴素O(n²) join使构建时间爆炸。

每个join的判定规则（数据驱动，任一不满足即KEEP该join）：
  REPLACE条件（全部满足）：
    R1 标准R2RML形式（rr:joinCondition(rr:child+rr:parent)+rr:parentTriplesMap）
    R2 父TM的subject模板变量都在子记录的CSV表头列中
    R3 引用完整性：子表中该列的非空去重值 ⊆ 父表中父列的值集（零悬空）
       ——同文件self-join天然满足（父记录即子记录本身）
  KEEP条件：替换不成立时，估算成本 child_rows×parent_rows 必须 < COST_LIMIT，
    否则直接终止（说明该映射无法线性化，需要人工处理）。

用法：
  python3 build_full_linear_mapping.py --mapping mapping.ttl \
      --data-dir data/urbanos_before --output baseline_linear_mapping.ttl
"""
import argparse
import csv
import re
import sys
from pathlib import Path

import rdflib
from rdflib import Namespace

RR = Namespace("http://www.w3.org/ns/r2rml#")
RML = Namespace("http://semweb.mmlab.be/ns/rml#")
QL = Namespace("http://semweb.mmlab.be/ns/ql#")

KNOWN_PREFIXES = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "owl": "http://www.w3.org/2002/07/owl#",
}
PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")
COST_LIMIT = 5_000_000


def load_graph(path):
    text = Path(path).read_text(encoding="utf-8")
    header = "".join(f"@prefix {p}: <{u}> .\n" for p, u in KNOWN_PREFIXES.items())
    g = rdflib.Graph()
    g.parse(data=header + text, format="turtle")
    return g


def tm_label(g, tm):
    return str(g.value(tm, rdflib.RDFS.label) or str(tm).split("/")[-1])


def resolve_data_file(data_dir, source_name):
    stem = Path(str(source_name)).stem.lower()
    cands = [p for p in sorted(Path(data_dir).iterdir())
             if p.is_file() and p.stem.lower() == stem]
    if len(cands) != 1:
        sys.exit(f"源{source_name}在{data_dir}下匹配到{len(cands)}个文件")
    return cands[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapping", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--max-keep-cost", type=int, default=COST_LIMIT)
    args = ap.parse_args()

    g = load_graph(args.mapping)

    # 收集每个TM的标签、源文件、行数、表头
    tms = list(g.subjects(rdflib.RDF.type, RR.TriplesMap))
    info = {}
    for tm in tms:
        src = g.value(g.value(tm, RML.logicalSource), RML.source)
        f = resolve_data_file(args.data_dir, src)
        with f.open(newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            nrows = sum(1 for _ in reader)
        info[tm] = {"label": tm_label(g, tm), "file": f,
                    "header": header, "rows": nrows}

    # 列值缓存（惰性加载，避免一次载入全部大列）
    colvals = {}
    def column(fname_header_file, col):
        key = (str(fname_header_file), col)
        if key not in colvals:
            with open(fname_header_file, newline="", encoding="utf-8") as fh:
                colvals[key] = {r[col] for r in csv.DictReader(fh) if (r.get(col) or "").strip()}
        return colvals[key]

    replaced, kept = [], []
    seen_oms = set()
    for tm in tms:
        ch = info[tm]
        for pom in g.objects(tm, RR.predicateObjectMap):
            for om in g.objects(pom, RR.objectMap):
                if om in seen_oms:
                    continue
                jc = g.value(om, RR.joinCondition)
                if jc is None:
                    continue
                seen_oms.add(om)
                ptm = g.value(om, RR.parentTriplesMap)
                ph = info[ptm]
                child_col = str(g.value(jc, RR.child))
                parent_col = str(g.value(jc, RR.parent))
                sm_p = g.value(ptm, RR.subjectMap)
                tpl_p = str(g.value(sm_p, RR.template))
                vars_p = PLACEHOLDER_RE.findall(tpl_p)

                pairname = f"{ch['label']}->{ph['label']}"
                missing_vars = [v for v in vars_p if v not in ch["header"]]
                danglings = None
                if not missing_vars:
                    cv = column(ch["file"], child_col)
                    pv = column(ph["file"], parent_col)
                    danglings = len({v for v in cv if v} - pv)

                if missing_vars:
                    verdict, why = "KEEP", f"父模板变量{missing_vars}不在子表头"
                elif danglings:
                    verdict, why = "KEEP", f"{danglings}个悬空引用"
                else:
                    verdict, why = "REPLACE", "前提全过"

                cost = ch["rows"] * ph["rows"]
                if verdict == "KEEP":
                    if cost > args.max_keep_cost:
                        sys.exit(f"[FATAL] {pairname}必须KEEP(原因:{why})但成本"
                                 f"{cost:,}>限值，映射无法线性化")
                    kept.append((pairname, why, cost))
                    continue

                pred = g.value(pom, RR.predicate)
                if pred is None:
                    pm = g.value(pom, RR.predicateMap)
                    pred = g.value(pm, RR.constant) if pm else None
                if pred is None:
                    sys.exit(f"{pairname}: 谓词不可解析")

                new_om = rdflib.BNode()
                g.add((new_om, rdflib.RDF.type, RR.ObjectMap))
                g.add((new_om, RR.template, rdflib.Literal(tpl_p)))
                g.add((new_om, RR.termType, RR.IRI))
                g.remove((pom, RR.objectMap, om))
                g.add((pom, RR.objectMap, new_om))
                for p2, o2 in list(g.predicate_objects(om)):
                    g.remove((om, p2, o2))
                replaced.append((pairname, cost))

    g.bind("rr", str(RR)); g.bind("rml", str(RML)); g.bind("ql", str(QL))
    g.serialize(destination=args.output, format="turtle")

    print(f"[done] 替换{len(replaced)}个join，保留{len(kept)}个：")
    for pn, cost in replaced:
        print(f"  REPLACE {pn}")
    for pn, why, cost in kept:
        print(f"  KEEP    {pn:35s} ({why}, 评估成本{cost:,})")
    print(f"输出: {args.output}")


if __name__ == "__main__":
    main()
