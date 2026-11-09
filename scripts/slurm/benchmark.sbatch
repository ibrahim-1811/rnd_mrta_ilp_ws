#!/usr/bin/env bash
# Speed benchmark on a cluster node, before any campaign: one task per fleet size.
#   --array=0-3  ->  70 80 90 100 robots
#   ORDERS (default 1500), STRATEGY (default nearest_astar), ORACLE_DECISIONS (default 3)
# Each task times (1) one recorded corpus run exactly as teacher_corpus.sbatch makes it
# (features and teacher panel), and (2) the oracle on its first ORACLE_DECISIONS decisions,
# then writes runs/benchmark/o<ORDERS>/<partition>/r<N>/timing.txt (one folder per partition,
# so the same benchmark on hpc1 and on gpu nodes can be compared). Training seed 104 only.
#   source /work/mmemon2s/rnd_26/cluster_env.sh
#   cd /work/mmemon2s/rnd_26/rnd_mrta_ilp_research && mkdir -p runs/benchmark/logs
#   sbatch --partition=hpc1 --array=0-3 scripts/slurm/benchmark.sbatch
#SBATCH --job-name=mrta-benchmark
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=06:00:00
#SBATCH --output=runs/benchmark/logs/benchmark_%A_%a.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?submit from the repository root}"
ORDERS=${ORDERS:-1500}; STRATEGY=${STRATEGY:-nearest_astar}; ORACLE_DECISIONS=${ORACLE_DECISIONS:-3}
SIZES=(70 80 90 100); ROBOTS=${SIZES[$SLURM_ARRAY_TASK_ID]}
PART=${SLURM_JOB_PARTITION:-local}
OUT=runs/benchmark/o${ORDERS}/${PART}/r${ROBOTS}
mkdir -p "$OUT"
echo "node $(hostname)  cpu $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2)" | tee "$OUT/timing.txt"

start=$(date +%s)
/usr/bin/time -f "corpus run: %e s wall, %M kB peak" -a -o "$OUT/timing.txt" \
  pixi run python src/simulation_env/scripts/run_experiment.py \
    --name "benchmark/o${ORDERS}/${PART}/r${ROBOTS}/corpus" --strategies "$STRATEGY" --robots "$ROBOTS" \
    --seeds 104 --orders "$ORDERS" --workload-orders "$ORDERS" --quantum 3 --window fleet_size \
    --purpose induction --features --teacher-panel --jobs 1 --resume
RUN="runs/benchmark/o${ORDERS}/${PART}/r${ROBOTS}/corpus/${STRATEGY}/r${ROBOTS}_s104"
python3 -c "import json;m=json.load(open('$RUN/metrics.json'));print('decisions', m['decisions'], 'charge sessions', m.get('charge_sessions'), 'order makespan', round(m['order_makespan_s'],1))" | tee -a "$OUT/timing.txt"

export PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management:src/ilp_learning
/usr/bin/time -f "oracle, first ${ORACLE_DECISIONS} decisions (plus one teacher replay): %e s wall, %M kB peak" -a -o "$OUT/timing.txt" \
  pixi run python -m ilp_learning.m6_oracle "$RUN" --out "$OUT/oracle" --max-decisions "$ORACLE_DECISIONS"
echo "total $(( $(date +%s) - start )) s" | tee -a "$OUT/timing.txt"
cat "$OUT/timing.txt"
