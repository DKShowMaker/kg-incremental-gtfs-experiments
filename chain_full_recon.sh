#!/usr/bin/env bash
# 全量重构串行链：等待当前seed1结束后，依次跑baseline、seed2-5、verify。
# baseline只做一次，不计入增量方法耗时；verify用于第5步正确性校验。
set -uo pipefail
cd /home/ztr/KG

echo "[chain] 等待正在运行的seed1全量重构结束..."
while pgrep -f "rmlmapper.jar -m results/mapping_seed1.ttl" > /dev/null; do
    sleep 60
done
echo "[chain] seed1已结束 $(date '+%H:%M:%S')"

echo "[chain] 开始baseline $(date '+%H:%M:%S')"
bash run_full_reconstruction.sh mapping.ttl data/before baseline results rmlmapper.jar
cp results/kg_baseline.nq results/baseline_graph.nq
echo "[chain] baseline完成 $(date '+%H:%M:%S')"

for s in 2 3 4 5; do
    echo "[chain] 开始seed${s} $(date '+%H:%M:%S')"
    bash run_full_reconstruction.sh mapping.ttl data/after_seed${s} seed${s} results rmlmapper.jar
    echo "[chain] seed${s}完成 $(date '+%H:%M:%S')"
done

echo "[chain] 开始verify(seed1数据的全量重构，用于正确性校验) $(date '+%H:%M:%S')"
bash run_full_reconstruction.sh mapping.ttl data/after_seed1 verify results rmlmapper.jar
echo "[chain] verify完成 $(date '+%H:%M:%S')"

echo "ALL_FULL_RECON_DONE"
