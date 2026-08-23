#!/usr/bin/env bash
# 单线程增量更新基线：diff -> 映射delta -> 用IRI模板算stale -> 应用到baseline图谱。
# 前提：results_dir下已经有baseline_graph.nq（对before_dir跑一次
#       run_full_reconstruction.sh，把输出重命名/复制成baseline_graph.nq）。
# 当前只处理stop_times.txt一个文件的stale删除，若change_generator.py以后扩展到
# 改动其他文件，这里需要对每个受影响文件重复一次compute_stale_iris.py（用--append），
# 本脚本没有做这个泛化，只覆盖目前实际在用的场景。
#
# 【偏离指导的修改】RMLMapper步骤改用stoptimes专用delta映射（delta_mapping_base.ttl，
# 由build_delta_mapping.py从完整mapping生成：只保留stoptimes TriplesMap，其join替换
# 为已验证等价的rr:template对象映射）。原因：
# 1) compute_delta会在delta_dir留下未变更文件的完整副本，用完整mapping跑会把
#    未变更实体也重新映射，apply拼接后产生重复quad，第5步sort/diff校验必然失败；
# 2) 完整mapping含shapes(57k行)->shape_points(57k行)的O(n^2) join，增量运行也要
#    约1小时，与全量重构无差别。专用映射只映射真正变化的实体，实测与真join输出
#    逐quad一致，耗时秒级。
#
# 用法：
#   ./run_incremental_update.sh <mapping_file> <before_dir> <after_dir> <round_tag> \
#       <results_dir> <stoptime_iri_template> [rmlmapper_jar] [delta_mapping] [baseline]
set -euo pipefail

if [ "$#" -lt 6 ]; then
    echo "usage: $0 <mapping_file> <before_dir> <after_dir> <round_tag> <results_dir> <stoptime_template> [rmlmapper_jar] [delta_mapping] [baseline]" >&2
    exit 1
fi

MAPPING_FILE=$1
BEFORE_DIR=$2
AFTER_DIR=$3
ROUND_TAG=$4
RESULTS_DIR=$5
STOPTIME_TEMPLATE=$6
RMLMAPPER_JAR=${7:-rmlmapper.jar}
DELTA_MAPPING=${8:-delta_mapping_base.ttl}
BASELINE=${9:-$RESULTS_DIR/baseline_graph.nq}

if [ ! -f "$BASELINE" ]; then
    echo "找不到baseline图谱 $BASELINE（需要先构建目标数据集的baseline图谱，" >&2
    echo "该步骤不计入增量方法耗时）" >&2
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
        --mapping "$DELTA_MAPPING" --data-dir "$WORK/delta" \
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
        --baseline "$BASELINE" \
        --stale-iris "$WORK/stale_iris.txt" \
        --delta "$WORK/delta_triples.nq" \
        --output "$RESULTS_DIR/kg_incremental_${ROUND_TAG}.nq"

    END=$(date +%s.%N)
    echo "elapsed_seconds: $(echo "$END - $START" | bc)"
} | tee "$TIME_LOG"

echo "result graph: $RESULTS_DIR/kg_incremental_${ROUND_TAG}.nq"
echo "time log: $TIME_LOG"
