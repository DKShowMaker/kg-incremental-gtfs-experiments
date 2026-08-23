#!/usr/bin/env python3
"""
T'实验映射构建：完整mapping的13个TriplesMap全部保留、其余11个join原样不动，
仅把 shapes→shape_points 这一个O(n²) join替换为等价的rr:template对象映射。
用于测量T'——若RMLMapper对shapes不用朴素二次方join，全量重构大概要多久。

【与stoptimes案例的关键差异】该join是一对多引用：每条SHAPES.csv记录（作为Shape
实体）要链接到同shape_id的全部K条记录（作为ShapePoint），单条rr:template在子记录
上只能发出1个对象，无法逐记录复现K个，因此不能照搬stoptimes的替换方式。

等价性论证（图级别，依赖RDF集合语义+RMLMapper对输出的去重）：
  J(join) = {(Shape(S), pred, Point(r_j)) : ∀r_i,r_j∈同文件, r_j.shape_id == S}
  T(模板) = {(Shape(shape_id(r)), pred, Point(r)) : ∀r}
  T⊆J：取r_i=r_j即可；
  J⊆T：r_i发出的三元组(Shape(S),pred,Point(r_j))与r_j自己发出的完全相同——
      因为subject只由shape_id决定。
∴ 按RDF集合语义 J≡T。该论证依赖以下前提，本脚本全部强制校验，任一不满足即退出：
  P1 shapes的subjectMap模板变量恰为{shape_id}；
  P2 shapes与shape_points两个TM的逻辑源是同一个文件；
  P3 join为标准R2RML形式且child=parent=shape_id；
  P4 父TM的subject模板变量都在SHAPES.csv表头列中（子记录同文件必然携带）；
  P5 该POM谓词可解析；且shapes TM除本join外不含其他join。

用法：
  python3 build_tprime_mapping.py --mapping mapping.ttl --data-dir data/before \
      --output tprime_mapping_base.ttl
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

KNOWN_PREFIXES = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "owl": "http://www.w3.org/2002/07/owl#",
}

PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")


def load_graph(path):
    text = Path(path).read_text(encoding="utf-8")
    header = "".join(f"@prefix {p}: <{u}> .\n" for p, u in KNOWN_PREFIXES.items())
    g = rdflib.Graph()
    g.parse(data=header + text, format="turtle")
    return g


def tm_by_label(g, label):
    tms = [t for t in set(g.subjects(rdflib.RDF.type, RR.TriplesMap))
           if str(g.value(t, rdflib.RDFS.label)) == label]
    if len(tms) != 1:
        sys.exit(f"label={label} 匹配到{len(tms)}个TriplesMap，需要恰好1个")
    return tms[0]


def resolve_data_file(data_dir, source_name):
    stem = Path(source_name).stem.lower()
    candidates = [p for p in sorted(Path(data_dir).iterdir())
                  if p.is_file() and p.stem.lower() == stem]
    if len(candidates) != 1:
        sys.exit(f"{source_name} 在{data_dir}下匹配到{len(candidates)}个文件")
    return candidates[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--data-dir", required=True,
                        help="用于读取CSV表头做P4校验（如 data/before）")
    parser.add_argument("--child-label", default="shapes")
    parser.add_argument("--parent-label", default="shape_points")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    g = load_graph(args.mapping)
    child_tm = tm_by_label(g, args.child_label)
    parent_tm = tm_by_label(g, args.parent_label)

    # P1：shapes的subject只由{shape_id}决定（图级别等价论证的核心前提）
    sm_c = g.value(child_tm, RR.subjectMap)
    tpl_child = str(g.value(sm_c, RR.template))
    vars_child = PLACEHOLDER_RE.findall(tpl_child)
    if sorted(vars_child) != ["shape_id"]:
        sys.exit(f"P1失败: {args.child_label} subject模板变量为{vars_child}，"
                 f"不是仅{{shape_id}}，图级别等价不成立")

    # P2：两个TM必须读同一个文件
    src_c = str(g.value(g.value(child_tm, RML.logicalSource), RML.source))
    src_p = str(g.value(g.value(parent_tm, RML.logicalSource), RML.source))
    if Path(src_p).stem.lower() != Path(src_c).stem.lower():
        sys.exit(f"P2失败: 两TM逻辑源不同（{src_c} vs {src_p}），"
                 f"父记录不一定出现在子记录中，J⊆T不成立")

    # P4：父TM subject模板变量必须在数据文件表头中（子记录同文件必然携带）
    csv_path = resolve_data_file(args.data_dir, src_c)
    with csv_path.open(newline="", encoding="utf-8") as f:
        header_cols = next(csv.reader(f))
    tpl_parent = str(g.value(g.value(parent_tm, RR.subjectMap), RR.template))
    vars_parent = PLACEHOLDER_RE.findall(tpl_parent)
    missing = [v for v in vars_parent if v not in header_cols]
    if missing:
        sys.exit(f"P4失败: 父模板变量{missing}不在{csv_path.name}表头{header_cols}中")

    # P3/P5：定位目标join并验证标准形式；要求child TM恰好只有这一个join
    joins = []
    for pom in g.objects(child_tm, RR.predicateObjectMap):
        for om in g.objects(pom, RR.objectMap):
            jc = g.value(om, RR.joinCondition)
            if jc is None:
                continue
            ptm = g.value(om, RR.parentTriplesMap)
            child_col = str(g.value(jc, RR.child))
            parent_col = str(g.value(jc, RR.parent))
            if ptm != parent_tm:
                sys.exit(f"P5失败: {args.child_label}存在指向其他父TM"
                         f"({ptm})的join，超出本脚本处理范围")
            if (child_col, parent_col) != ("shape_id", "shape_id"):
                sys.exit(f"P3失败: join列为{child_col}/{parent_col}，预期shape_id/shape_id")
            pred = g.value(pom, RR.predicate)
            if pred is None:
                pm = g.value(pom, RR.predicateMap)
                pred = g.value(pm, RR.constant) if pm else None
            if pred is None:
                sys.exit("P5失败: 谓词不可解析")
            joins.append((pom, om))

    if len(joins) != 1:
        sys.exit(f"预期{args.child_label}恰好有1个join，实际{len(joins)}个")

    pom, om = joins[0]
    print(f"[前提] 全部通过：subject模板={tpl_child}")
    print(f"[前提] 同一逻辑源={src_c}，表头列={header_cols}")
    print(f"[前提] 父模板={tpl_parent}")

    # 替换：新objectMap用父TM的subject模板（变量都在子记录列中）
    new_om = rdflib.BNode()
    g.add((new_om, rdflib.RDF.type, RR.ObjectMap))
    g.add((new_om, RR.template, rdflib.Literal(tpl_parent)))
    g.add((new_om, RR.termType, RR.IRI))
    g.remove((pom, RR.objectMap, om))
    g.add((pom, RR.objectMap, new_om))
    for p2, o2 in list(g.predicate_objects(om)):
        g.remove((om, p2, o2))

    g.bind("rr", str(RR))
    g.bind("rml", str(RML))
    g.bind("ql", "http://semweb.mmlab.be/ns/ql#")
    g.serialize(destination=args.output, format="turtle")

    n_tm = len(set(g.subjects(rdflib.RDF.type, RR.TriplesMap)))
    n_join = len(set(g.subjects(RR.joinCondition, None)))
    print(f"[done] 输出 {args.output}；TriplesMap={n_tm}(应13)，剩余join={n_join}(应11)")


if __name__ == "__main__":
    main()
