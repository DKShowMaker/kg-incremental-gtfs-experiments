#!/usr/bin/env python3
"""
在RML/R2RML映射文件里，顺着
  LogicalSource(source=某文件) -> TriplesMap -> SubjectMap -> rr:template
这条引用链，查出某个源文件对应实体的IRI模板。这个模板之后给
compute_stale_iris.py用，不用每次都重新跑RMLMapper去问它某一行的IRI是什么。

用法：
  python3 find_iri_template.py --mapping mapping.ttl --source-file stop_times.txt

如果解析时报"Prefix 'xxx:' not bound"：
  说明映射文件里用了某个没有@prefix声明的前缀。只对下面KNOWN_PREFIXES里这种
  确凿无疑的标准命名空间（rdf/rdfs/xsd/owl）自动补声明；如果报错的是别的、
  不认识的前缀，不要瞎猜URI去填一个占位值（之前踩过这个坑，会导致该前缀相关
  的内容被静默解析错、不报错但结果不对），去mapping文件里搜这个前缀名出现的
  上下文，人工确认它应该绑定到哪个真实命名空间，再加进KNOWN_PREFIXES里。
"""
import argparse
import re
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


def load_graph_with_missing_prefixes(path):
    text = Path(path).read_text(encoding="utf-8")
    header_lines = []
    for prefix, uri in KNOWN_PREFIXES.items():
        already_declared = re.search(rf"@prefix\s+{prefix}:\s*<", text)
        used_in_body = re.search(rf"(?<![\w:]){prefix}:", text)
        if used_in_body and not already_declared:
            header_lines.append(f"@prefix {prefix}: <{uri}> .")
    g = rdflib.Graph()
    g.parse(data="\n".join(header_lines) + "\n" + text, format="turtle")
    return g


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--source-file", required=True, help="如 stop_times.txt")
    args = parser.parse_args()

    g = load_graph_with_missing_prefixes(args.mapping)

    found = False
    for ls in g.subjects(RML.source, None):
        src = g.value(ls, RML.source)
        if src is None or args.source_file not in str(src):
            continue
        print(f"[LogicalSource] {ls}  source={src}")
        for tm in g.subjects(RML.logicalSource, ls):
            print(f"  [TriplesMap] {tm}")
            sm = g.value(tm, RR.subjectMap)
            if sm is None:
                print("    (没有直接的rr:subjectMap，需人工查看这个TriplesMap的完整定义)")
                continue
            tpl = g.value(sm, RR.template)
            print(f"    [SubjectMap] {sm}")
            print(f"    [template]   {tpl}")
            found = True

    if not found:
        print("没有自动匹配到，检查--source-file的值是否和映射文件里source的值完全一致"
              "（大小写、路径前缀等）")


if __name__ == "__main__":
    main()
