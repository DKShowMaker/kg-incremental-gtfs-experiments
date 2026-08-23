#!/usr/bin/env python3
"""
从完整mapping.ttl中提取目标实体（默认stoptimes）的TriplesMap，生成增量更新专用的
delta映射：
- 只保留该TriplesMap的定义（不展开parentTriplesMap指向的父TM——父TM只是join引用
  的目标，不需要被执行）
- 把它的join对象映射替换为等价的rr:template对象映射。等价性前提（均需实测验证）：
    1) join是标准R2RML形式：rr:joinCondition(rr:child+rr:parent)+rr:parentTriplesMap
    2) 父TM的subject模板变量仅由join列决定
    3) 数据引用完整（无悬空引用，否则join不发三元组而模板会发）
- 替换出的模板对象映射显式声明rr:termType rr:IRI

用法：
  python3 build_delta_mapping.py --mapping mapping.ttl --output delta_mapping_base.ttl
"""
import argparse
import re
import sys

import rdflib
from rdflib import Namespace

RR = Namespace("http://www.w3.org/ns/r2rml#")
RML = Namespace("http://semweb.mmlab.be/ns/rml#")

KNOWN_PREFIXES = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "owl": "http://www.w3.org/2002/07/owl#",
}

# 沿这些谓词BFS收集子图；parentTriplesMap只保留引用三元组本身，不展开父TM
STRUCT_PREDS = [RML.logicalSource, RR.subjectMap, RR.predicateObjectMap,
                RR.objectMap, RR.predicateMap, RR.graphMap, RR.joinCondition]


def load_graph(path):
    text = open(path, encoding="utf-8").read()
    header = "".join(f"@prefix {p}: <{u}> .\n" for p, u in KNOWN_PREFIXES.items())
    g = rdflib.Graph()
    g.parse(data=header + text, format="turtle")
    return g


def collect_subgraph(g, root):
    keep = set()
    frontier, seen = [root], set()
    while frontier:
        n = frontier.pop()
        if n in seen:
            continue
        seen.add(n)
        for p, o in g.predicate_objects(n):
            keep.add((n, p, o))
            if p in STRUCT_PREDS and not isinstance(o, rdflib.Literal):
                frontier.append(o)
    return keep


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--target-label", default="stoptimes",
                        help="目标TriplesMap的rdfs:label")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    g = load_graph(args.mapping)

    tms = [tm for tm in set(g.subjects(rdflib.RDF.type, RR.TriplesMap))
           if str(g.value(tm, rdflib.RDFS.label)) == args.target_label]
    if len(tms) != 1:
        sys.exit(f"label={args.target_label} 匹配到{len(tms)}个TriplesMap，需要恰好1个")
    tm = tms[0]

    out = rdflib.Graph()
    for s, p, o in collect_subgraph(g, tm):
        out.add((s, p, o))

    # 遍历目标TM的每个pom的objectMap，把join替换成等价模板
    replaced = 0
    tms_in_out = list(out.subjects(rdflib.RDF.type, RR.TriplesMap))
    for tm_node in tms_in_out:
        for pom in list(out.objects(tm_node, RR.predicateObjectMap)):
            for om in list(out.objects(pom, RR.objectMap)):
                jc = out.value(om, RR.joinCondition)
                if jc is None:
                    continue
                ptm = out.value(om, RR.parentTriplesMap)
                child = out.value(jc, RR.child)
                parent_col = out.value(jc, RR.parent)
                # 父TM定义不在out里（BFS不展开parentTriplesMap），模板要从原图g查
                sm = g.value(ptm, RR.subjectMap)
                tpl = g.value(sm, RR.template)
                if tpl is None:
                    sys.exit(f"父TM {ptm} 没有subjectMap template，无法做等价替换")
                tpl_str = str(tpl)
                for var in re.findall(r"\{([^}]+)\}", tpl_str):
                    if var not in {str(child), str(parent_col)}:
                        sys.exit(f"模板变量{{{var}}}不在join列({child}/{parent_col})中，"
                                 f"等价替换不成立，终止")
                pred = out.value(pom, RR.predicate)
                if pred is None:
                    pm = out.value(pom, RR.predicateMap)
                    pred = out.value(pm, RR.constant) if pm else None
                if pred is None:
                    sys.exit("join所在的predicateObjectMap没有可解析的谓词")

                new_om = rdflib.BNode()
                out.add((new_om, rdflib.RDF.type, RR.ObjectMap))
                out.add((new_om, RR.template, rdflib.Literal(tpl_str)))
                out.add((new_om, RR.termType, RR.IRI))
                out.remove((pom, RR.objectMap, om))
                out.add((pom, RR.objectMap, new_om))
                # 清掉旧join om的子树（已无引用）
                for p2, o2 in list(out.predicate_objects(om)):
                    out.remove((om, p2, o2))
                replaced += 1

    out.bind("rr", str(RR))
    out.bind("rml", str(RML))
    out.bind("ql", "http://semweb.mmlab.be/ns/ql#")
    out.serialize(destination=args.output, format="turtle")
    print(f"[done] 输出 {args.output}；{len(out)}条三元组；替换了{replaced}个join")


if __name__ == "__main__":
    main()
