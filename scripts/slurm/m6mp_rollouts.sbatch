#!/usr/bin/env bash
# Array 2: one shard of mp1 rollouts on one corpus run: stratified sample (quota per
# stratum + random controls), replay proven identical, clone-checked, H = 120 s.
# Each shard writes its own directory; the sample does not depend on the shard.
#SBATCH --job-name=m6mp-rollouts
#SBATCH --cpus-per-task=1
#SBATCH --mem=6G
#SBATCH --time=12:00:00
#SBATCH --output=runs/m6mp/logs/rollouts_%A_%a.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?submit from the repository root}"
QUOTA=${QUOTA:-8}
MAX_ACTIONS=${MAX_ACTIONS:-6}
read -r ROBOTS SEED SHARD SHARDS < <(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" scripts/slurm/rollouts.tsv)
RUN="runs/m6mp/corpus/r${ROBOTS}_s${SEED}/nearest_astar/r${ROBOTS}_s${SEED}"
export PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management:src/ilp_learning
pixi run python -m ilp_learning.m6_rollouts "$RUN" --out runs/m6mp/rollouts \
  --arm mp1 --sample stratified --quota "$QUOTA" --max-actions "$MAX_ACTIONS" \
  --shard "${SHARD}/${SHARDS}"
