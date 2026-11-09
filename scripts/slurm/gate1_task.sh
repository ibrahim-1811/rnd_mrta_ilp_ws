#!/usr/bin/env bash
# One Gate 1 task (line INDEX of scripts/slurm/gate1.tsv, 0-based): record the teacher's run on a
# validation seed, then run the oracle on it. Outputs stay under runs/gate1/.
#   scripts/slurm/gate1_task.sh INDEX
# The cluster limits job arrays to 5 tasks, so tasks are packed into multi-core jobs:
#   seq 0 59 | xargs -P 60 -I{} scripts/slurm/gate1_task.sh {}
set -uo pipefail
cd "$(dirname "$0")/../.."
INDEX=${1:?task index}
read -r OBJECTIVE ORDERS TEACHER ROBOTS SEED HORIZON < <(sed -n "$((INDEX + 1))p" scripts/slurm/gate1.tsv)
TAG="o${ORDERS}/${OBJECTIVE}/${TEACHER}/r${ROBOTS}_s${SEED}"
LOG="runs/gate1/logs/${TAG//\//_}.log"
mkdir -p runs/gate1/logs
{
  echo "task $INDEX: $OBJECTIVE, $ORDERS orders, $TEACHER, $ROBOTS robots, seed $SEED, start $(date +%T) on $(hostname)"
  pixi run python src/simulation_env/scripts/run_experiment.py \
    --name "gate1/runs/o${ORDERS}/${TEACHER}/r${ROBOTS}_s${SEED}" --strategies "$TEACHER" \
    --robots "$ROBOTS" --seeds "$SEED" --orders "$ORDERS" --workload-orders "$ORDERS" \
    --quantum 3 --window fleet_size --purpose backup --jobs 1 --resume || exit 1
  RUN="runs/gate1/runs/o${ORDERS}/${TEACHER}/r${ROBOTS}_s${SEED}/${TEACHER}/r${ROBOTS}_s${SEED}"
  export PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management:src/ilp_learning
  pixi run python -m ilp_learning.m6_oracle "$RUN" --out "runs/gate1/${TAG}" \
    --objective "$OBJECTIVE" --horizon "$HORIZON"
  echo "task $INDEX: exit $? end $(date +%T)"
} > "$LOG" 2>&1
tail -1 "$LOG"
