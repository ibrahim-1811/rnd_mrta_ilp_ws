#!/usr/bin/env bash
# Group D, per teacher: one shard of mp1 rollouts on one teacher corpus run. Alternative
# assignments are forced from the identical replayed state, and the run's own strategy
# continues every branch (no --continuations). Each strategy writes its own root:
#   runs/teachers/rollouts/o<orders>/<strategy>/r<N>_s<S>_shard<k>of<n>/
#SBATCH --job-name=teacher-rollouts
#SBATCH --cpus-per-task=1
#SBATCH --mem=6G
#SBATCH --time=12:00:00
#SBATCH --output=runs/teachers/logs/rollouts_%A_%a.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?submit from the repository root}"
QUOTA=${QUOTA:-8}
MAX_ACTIONS=${MAX_ACTIONS:-6}
read -r STRATEGY ROBOTS SEED ORDERS SHARD SHARDS < <(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" scripts/slurm/teacher_rollouts.tsv)
RUN="runs/teachers/o${ORDERS}/${STRATEGY}/r${ROBOTS}_s${SEED}/${STRATEGY}/r${ROBOTS}_s${SEED}"
export PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management:src/ilp_learning
pixi run python -m ilp_learning.m6_rollouts "$RUN" --out "runs/teachers/rollouts/o${ORDERS}/${STRATEGY}" \
  --arm mp1 --sample stratified --quota "$QUOTA" --max-actions "$MAX_ACTIONS" \
  --shard "${SHARD}/${SHARDS}"
