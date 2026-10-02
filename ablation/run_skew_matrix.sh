#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

K=${K:-4}
BEFORE=data/urbanos_before
BASELINE=results/baseline_graph_urbanos.nq
MAPPING=delta_mapping_base.ttl
STOPTIME_TEMPLATE="http://transport.linkeddata.es/madrid/metro/stoptimes/{trip_id}-{stop_id}-{arrival_time}"
RMLMAPPER_JAR=${RMLMAPPER_JAR:-rmlmapper.jar}
P_CODES=(000 025 050 075 100)
P_VALUES=(0 0.25 0.50 0.75 1.00)
SEEDS=(1 2 3 4 5)

if [[ ! "$K" =~ ^[1-9][0-9]*$ ]]; then
    echo "K must be a positive integer" >&2
    exit 2
fi
for required in "$BEFORE/stop_times.txt" "$BASELINE" "$MAPPING" "$RMLMAPPER_JAR"; do
    if [[ ! -f "$required" ]]; then
        echo "missing required file: $required" >&2
        exit 2
    fi
done

mkdir -p results
for i in "${!P_CODES[@]}"; do
    code=${P_CODES[$i]}
    p=${P_VALUES[$i]}
    for seed in "${SEEDS[@]}"; do
        tag="skew_k${K}_p${code}_s${seed}"
        after="data/urbanos_${tag}"
        if [[ -d "$after" ]]; then
            if [[ ! -f "$after/.partition_skew.json" || \
                  ! -f "results/changelog_${tag}.csv" || \
                  ! -f "results/generation_${tag}.log" ]]; then
                echo "incomplete existing generation for $tag: $after" >&2
                exit 2
            fi
            echo "[skew] reuse generated input $tag"
        else
            echo "[skew] generate $tag (p=$p, k=$K)"
            python3 change_generator.py \
                --input-dir "$BEFORE" \
                --output-dir "$after" \
                --target-file stop_times.txt \
                --key-cols trip_id,stop_sequence \
                --update-cols arrival_time,departure_time \
                --change-ratio 0.10 \
                --seed "$seed" \
                --partition-skew-p "$p" \
                --partition-k "$K" \
                --log "results/changelog_${tag}.csv" \
                | tee "results/generation_${tag}.log"
        fi

        if [[ -f "results/time_incremental_${tag}.log" && \
              -f "results/kg_incremental_${tag}.nq" ]]; then
            echo "[skew] reuse single-thread result $tag"
        else
            echo "[skew] single-thread $tag"
            bash run_incremental_update.sh \
                mapping.ttl "$BEFORE" "$after" "$tag" results \
                "$STOPTIME_TEMPLATE" "$RMLMAPPER_JAR" "$MAPPING" "$BASELINE"
        fi

        if [[ -f "results/time_distributed_${tag}.log" && \
              -f "results/kg_distributed_${tag}.nq" ]]; then
            echo "[skew] reuse distributed result $tag"
        else
            echo "[skew] distributed $tag"
            python3 run_distributed_update.py \
                --before "$BEFORE" --after "$after" --tag "$tag" --k "$K" \
                --baseline "$BASELINE" --mapping "$MAPPING"
        fi
    done
done

python3 ablation/summarize_skew.py --results-dir results --k "$K"
