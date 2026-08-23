#!/usr/bin/env bash
# 消融矩阵驱动：对一个模式(occ|lock)跑完整{冲突率×5seed}矩阵并逐一校验。
# 前置：栈已以对应模式重建且warmup完成。
# 用法: ./run_matrix.sh <occ|lock>
set -euo pipefail
MODE=$1
BASELINE=results/baseline_graph_urbanos.nq
TPL="http://transport.linkeddata.es/madrid/metro/stoptimes/{trip_id}-{stop_id}-{arrival_time}"
RATES=(0.0 0.05 0.20)

for c in "${RATES[@]}"; do
  cs=$(echo "$c" | tr -d '.')
  for s in 1 2 3 4 5; do
    tag="abl_${MODE}_c${cs}_s${s}"
    python3 run_distributed_update.py \
        --before data/urbanos_before --after data/u50_after_seed${s} \
        --tag "$tag" --k 4 --baseline "$BASELINE" \
        --mapping delta_mapping_base.ttl \
        --conflict-rate "$c" > /tmp/${tag}.log 2>&1 || { echo "FAIL $tag"; tail -3 /tmp/${tag}.log; exit 1; }
    if LC_ALL=C sort -S 1G --parallel=8 results/kg_distributed_${tag}.nq > tmp_sort_d.nq && \
       LC_ALL=C sort -S 1G --parallel=8 results/kg_incremental_u50s${s}.nq > tmp_sort_i.nq && \
       diff -q tmp_sort_d.nq tmp_sort_i.nq > /dev/null; then
      echo "PASS ${tag}: $(grep -oE 'elapsed_seconds: .*' results/time_distributed_${tag}.log)"
    else
      echo "VERIFY-FAIL ${tag}"; exit 1
    fi
  done
done
rm -f tmp_sort_d.nq tmp_sort_i.nq
echo "MATRIX ${MODE} DONE"
