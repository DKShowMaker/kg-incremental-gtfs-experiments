#!/usr/bin/env bash
# 单线程增量更新基线：diff -> 映射delta -> 用IRI模板算stale -> 应用到baseline图谱。
# 前提：results_dir下已经有baseline_graph.nq（对before_dir跑一次
#       run_full_reconstruction.sh，把输出重命名/复制成baseline_graph.nq）。
# 当前只处理stop_times.txt一个文件的stale删除，若change_generator.py以后扩展到
# 改动其他文件，这里需要对每个受影响文件重复一次compute_stale_iris.py（用--append），
# 本脚本没有做这个泛化，只覆盖目前实际在用的场景。
#
# 用法：
#   ./run_incremental_update.sh <mapping_file> <before_dir> <after_dir> <round_tag> \
#       <results_dir> <stoptime_iri_template> [rmlmapper_jar]
set -euo pipefail

if [ "$#" -lt 6 ]; then
    echo "usage: $0 <mapping_file> <before_dir> <after_dir> <round_tag> <results_dir> <stoptime_template> [rmlmapper_jar]" >&2
    exit 1
fi

MAPPING_FILE=$1
BEFORE_DIR=$2
AFTER_DIR=$3
ROUND_TAG=$4
RESULTS_DIR=$5
STOPTIME_TEMPLATE=$6
RMLMAPPER_JAR=${7:-rmlmapper.jar}

BASELINE_GRAPH="$RESULTS_DIR/baseline_graph.nq"
if [ ! -f "$BASELINE_GRAPH" ]; then
    echo "找不到 $BASELINE_GRAPH，需要先对before_dir跑一次run_full_reconstruction.sh并" >&2
    echo "把输出复制/重命名成baseline_graph.nq（这一步不计入增量方法耗时）" >&2
    exit 1
fi

WORK="$RESULTS_DIR/work_${ROUND_TAG}"
mkdir -p "$WORK"
TIME_LOG="$RESULTS_DIR/time_incremental_${ROUND_TAG}.log"

{
    START=$(date +%s.%N)

    python3 compute_delta.py \
        --before "$BEFORE_DIR" --after "$AFTER_DIR" \
        --delta-dir "$WORK/delta" --stale-dir "$WORK/stale"

    python3 normalize_mapping.py \
        --mapping "$MAPPING_FILE" --data-dir "$WORK/delta" \
        --output "$WORK/delta_mapping.ttl"
    java -jar "$RMLMAPPER_JAR" -m "$WORK/delta_mapping.ttl" -o "$WORK/delta_triples.nq" -s nquads

    if [ -f "$WORK/stale/stop_times.txt" ]; then
        python3 compute_stale_iris.py \
            --stale-file "$WORK/stale/stop_times.txt" \
            --template "$STOPTIME_TEMPLATE" \
            --output "$WORK/stale_iris.txt"
    else
        : > "$WORK/stale_iris.txt"
    fi

    python3 apply_delta_to_graph.py \
        --baseline "$BASELINE_GRAPH" \
        --stale-iris "$WORK/stale_iris.txt" \
        --delta "$WORK/delta_triples.nq" \
        --output "$RESULTS_DIR/kg_incremental_${ROUND_TAG}.nq"

    END=$(date +%s.%N)
    echo "elapsed_seconds: $(echo "$END - $START" | bc)"
} | tee "$TIME_LOG"

echo "result graph: $RESULTS_DIR/kg_incremental_${ROUND_TAG}.nq"
echo "time log: $TIME_LOG"
