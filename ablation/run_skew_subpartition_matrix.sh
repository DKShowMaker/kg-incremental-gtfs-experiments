#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

K=${K:-4}
THRESHOLD=${SUBPARTITION_THRESHOLD:-1.7}
FACTOR=${SUBPARTITION_FACTOR:-$K}
CONFLICT_RATE=${CONFLICT_RATE:-0}
COHORT=${COHORT:-$(date +%Y%m%d_%H%M%S)}
BEFORE=data/urbanos_before
BASELINE=results/baseline_graph_urbanos.nq
MAPPING=delta_mapping_base.ttl
RMLMAPPER_JAR=${RMLMAPPER_JAR:-rmlmapper.jar}
P_CODES=(000 025 050 075 100)
SEEDS=(1 2 3 4 5)
OUT_DIR="results/skew_subpartition_k${K}_${COHORT}"

if [[ ! "$K" =~ ^[1-9][0-9]*$ || ! "$FACTOR" =~ ^[0-9]+$ ]] || (( FACTOR < 2 )); then
    echo "K must be positive and SUBPARTITION_FACTOR must be at least 2" >&2
    exit 2
fi
if [[ ! "$COHORT" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "COHORT may contain only letters, digits, underscore, and hyphen" >&2
    exit 2
fi
for required in "$BEFORE/stop_times.txt" "$BASELINE" "$MAPPING" "$RMLMAPPER_JAR"; do
    if [[ ! -f "$required" ]]; then
        echo "missing required file: $required" >&2
        exit 2
    fi
done
for code in "${P_CODES[@]}"; do
    for seed in "${SEEDS[@]}"; do
        after="data/urbanos_skew_k${K}_p${code}_s${seed}"
        if [[ ! -f "$after/.partition_skew.json" ]]; then
            echo "missing skew input manifest: $after/.partition_skew.json" >&2
            exit 2
        fi
    done
done
if [[ -e "$OUT_DIR" ]]; then
    echo "output cohort already exists: $OUT_DIR" >&2
    exit 2
fi
mkdir -p "$OUT_DIR"
exec > >(tee "$OUT_DIR/batch.log") 2>&1

STARTED_AT=$(date --iso-8601=seconds)
{
    echo "# Skew Subpartition Cohort"
    echo
    echo "- Cohort: $COHORT"
    echo "- Started: $STARTED_AT"
    echo "- k: $K"
    echo "- Threshold: $THRESHOLD"
    echo "- Split factor: $FACTOR"
    echo "- Injected conflict rate: $CONFLICT_RATE"
    echo "- Conflict RNG seed: 42"
    echo "- Git HEAD: $(git rev-parse HEAD)"
    echo "- Worktree: $(if [[ -z "$(git status --porcelain)" ]]; then echo clean; else echo modified; fi)"
    echo "- Runtime: $(uname -a)"
    echo "- Logical CPUs: $(nproc)"
    echo "- Memory:"
    free -h
    echo "- Python: $(python3 --version 2>&1)"
    echo "- Java: $(java -version 2>&1 | head -n 1)"
    echo "- Docker: $(docker version --format '{{.Server.Version}}')"
    echo "- Compose: $(docker compose version --short)"
    echo "- Worker image: $(docker image inspect kg-worker --format '{{.Id}}')"
    echo "- Running services:"
    docker compose ps
    echo "- Input hashes:"
    sha256sum "$BEFORE/stop_times.txt" "$BASELINE" "$MAPPING" "$RMLMAPPER_JAR"
    echo "- Seed snapshot hashes:"
    for code in "${P_CODES[@]}"; do
        for seed in "${SEEDS[@]}"; do
            file="data/urbanos_skew_k${K}_p${code}_s${seed}/stop_times.txt"
            sha256sum "$file"
        done
    done
} > "$OUT_DIR/manifest.md"

run_update() {
    local tag=$1
    local split_mode=$2
    local args=(python3 run_distributed_update.py
        --before "$BEFORE" --after "data/urbanos_skew_k${K}_p${P_CODES[$p_index]}_s${seed}"
        --tag "$tag" --k "$K" --baseline "$BASELINE" --mapping "$MAPPING"
        --conflict-rate "$CONFLICT_RATE" --rng-seed 42
        --results-dir "$OUT_DIR" --output-graph "$OUT_DIR/current_graph_check.nq")
    if [[ "$split_mode" == 1 ]]; then
        args+=(--subpartition-threshold "$THRESHOLD" --subpartition-factor "$FACTOR")
    fi
    "${args[@]}"
}

for seed in "${SEEDS[@]}"; do
    rotation=$((seed - 1))
    for offset in 0 1 2 3 4; do
        p_index=$(((rotation + offset) % ${#P_CODES[@]}))
        code=${P_CODES[$p_index]}
        stem="${COHORT}_p${code}_s${seed}"
        base_tag="${stem}_unsplit"
        split_tag="${stem}_split"

        if (( (seed + p_index) % 2 == 0 )); then
            run_update "$base_tag" 0
            run_update "$split_tag" 1
        else
            run_update "$split_tag" 1
            run_update "$base_tag" 0
        fi

        base_delta="$OUT_DIR/work_dist_${base_tag}/merged_delta.nq"
        split_delta="$OUT_DIR/work_dist_${split_tag}/merged_delta.nq"
        if ! diff -q <(LC_ALL=C sort "$base_delta") \
                     <(LC_ALL=C sort "$split_delta") >/dev/null; then
            echo "mapped delta mismatch for p=$code seed=$seed" >&2
            exit 1
        fi
        if [[ "$code" == 000 && "$seed" == 5 ]]; then
            if ! diff -q <(LC_ALL=C sort "results/kg_incremental_skew_k${K}_p000_s5.nq") \
                         <(LC_ALL=C sort "$OUT_DIR/current_graph_check.nq") >/dev/null; then
                echo "p=0 seed=5 graph differs from the existing serial oracle" >&2
                exit 1
            fi
        fi
        echo "[subpartition] verified delta p=$code seed=$seed"
    done
done

python3 ablation/summarize_skew_subpartition.py \
    --results-dir "$OUT_DIR" --cohort "$COHORT" --k "$K" \
    --threshold "$THRESHOLD" --factor "$FACTOR" \
    --conflict-rate "$CONFLICT_RATE"
echo "- Finished: $(date --iso-8601=seconds)" >> "$OUT_DIR/manifest.md"
echo "- Code hashes:" >> "$OUT_DIR/manifest.md"
sha256sum run_distributed_update.py ablation/run_skew_subpartition_matrix.sh \
    ablation/summarize_skew_subpartition.py partitioning.py dist/src/Worker.java \
    >> "$OUT_DIR/manifest.md"
echo "[subpartition] complete: $OUT_DIR"
