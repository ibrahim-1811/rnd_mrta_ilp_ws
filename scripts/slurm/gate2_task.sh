#!/usr/bin/env bash
# One Gate 2 task (line INDEX of scripts/slurm/gate2.tsv, 0-based): the oracle on one
# induction run, written as a trainable run folder (run.json, decisions, outcomes,
# features, teacher_labels, oracle.json) under runs/gate2/o<orders>/<objective>/.
# The teacher run comes from the teacher corpus (runs/teachers/o<orders>/...) when it
# exists, else it is recorded here first. A task whose oracle.json exists is skipped.
#   scripts/slurm/gate2_task.sh INDEX            (GATE2_TSV=other.tsv to use another list)
set -uo pipefail
cd "$(dirname "$0")/../.."
INDEX=${1:?task index}
TSV=${GATE2_TSV:-scripts/slurm/gate2.tsv}
read -r OBJECTIVE ORDERS TEACHER ROBOTS SEED HORIZON < <(sed -n "$((INDEX + 1))p" "$TSV")
[ -n "${HORIZON:-}" ] || { echo "task $INDEX: no line $((INDEX + 1)) in $TSV"; exit 2; }
TAG="o${ORDERS}/${OBJECTIVE}/${TEACHER}/r${ROBOTS}_s${SEED}"
OUT="runs/gate2/${TAG}"
LOG="runs/gate2/logs/${TAG//\//_}.log"
mkdir -p runs/gate2/logs
if [ -f "$OUT/oracle.json" ]; then
  echo "task $INDEX: $TAG done already"
  exit 0
fi
(
  echo "task $INDEX: $OBJECTIVE, $ORDERS orders, $TEACHER, $ROBOTS robots, seed $SEED, start $(date +%T) on $(hostname)"
  RUN="runs/teachers/o${ORDERS}/${TEACHER}/r${ROBOTS}_s${SEED}/${TEACHER}/r${ROBOTS}_s${SEED}"
  if [ ! -f "$RUN/run.json" ]; then
    pixi run python src/simulation_env/scripts/run_experiment.py \
      --name "gate2/runs/o${ORDERS}/${TEACHER}/r${ROBOTS}_s${SEED}" --strategies "$TEACHER" \
      --robots "$ROBOTS" --seeds "$SEED" --orders "$ORDERS" --workload-orders "$ORDERS" \
      --quantum 3 --window fleet_size --purpose induction --jobs 1 --resume || exit 1
    RUN="runs/gate2/runs/o${ORDERS}/${TEACHER}/r${ROBOTS}_s${SEED}/${TEACHER}/r${ROBOTS}_s${SEED}"
  fi
  echo "teacher run: $RUN"
  export PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management:src/ilp_learning
  pixi run python -m ilp_learning.m6_oracle "$RUN" --out "$OUT" \
    --objective "$OBJECTIVE" --horizon "$HORIZON"
  STATUS=$?
  echo "task $INDEX: exit $STATUS end $(date +%T)"
  exit $STATUS
) > "$LOG" 2>&1
STATUS=$?
tail -1 "$LOG"
exit $STATUS
