#!/usr/bin/env bash
# 全量重构基线：对一份完整数据集，从头执行RML映射，记录耗时/内存/三元组数。
# 用法：./run_full_reconstruction.sh <mapping_file> <data_dir> <round_tag> <results_dir> [rmlmapper_jar]
set -euo pipefail

if [ "$#" -lt 4 ] || [ "$#" -gt 5 ]; then
    echo "usage: $0 <mapping_file> <data_dir> <round_tag> <results_dir> [rmlmapper_jar]" >&2
    exit 1
fi

MAPPING_FILE=$1
DATA_DIR=$2
ROUND_TAG=$3
RESULTS_DIR=$4
RMLMAPPER_JAR=${5:-rmlmapper.jar}

mkdir -p "$RESULTS_DIR"

MAPPING_FOR_ROUND="$RESULTS_DIR/mapping_${ROUND_TAG}.ttl"
OUTPUT_FILE="$RESULTS_DIR/kg_${ROUND_TAG}.nq"
TIME_LOG="$RESULTS_DIR/time_${ROUND_TAG}.log"
RUN_LOG="$RESULTS_DIR/rmlmapper_${ROUND_TAG}.log"
TRIPLE_LOG="$RESULTS_DIR/triples_${ROUND_TAG}.txt"

python3 normalize_mapping.py \
    --mapping "$MAPPING_FILE" \
    --data-dir "$DATA_DIR" \
    --output "$MAPPING_FOR_ROUND"

/usr/bin/time -v -o "$TIME_LOG" \
    java -jar "$RMLMAPPER_JAR" \
    -m "$MAPPING_FOR_ROUND" \
    -o "$OUTPUT_FILE" \
    -s nquads \
    > "$RUN_LOG" 2>&1

wc -l < "$OUTPUT_FILE" > "$TRIPLE_LOG"

echo "output graph: $OUTPUT_FILE"
echo "time/memory log: $TIME_LOG"
echo "triple/quad lines: $(cat "$TRIPLE_LOG")"
