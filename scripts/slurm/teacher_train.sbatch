#!/usr/bin/env bash
# Groups B-D: Popper rules from recorded teacher runs, shared vocabulary
# (extended + Tier B + battery; VOCAB=nobattery or nogrid for ablations).
#   ARM=<strategy>  that strategy's own runs (one policy per teacher)
#   ARM=pooled      runs of all eight strategies, no agreement required
#   SUPERVISION=demo     labels are the driving strategy's own picks (B, C)       [default]
#   SUPERVISION=outcome  labels are jw2 rollout outcomes from identical states, each run
#                        continued by its own strategy (D per teacher; pooled D)
#   MATCHED=1       demo arms train only on the decisions the rollouts probed, so B/C and
#                   D differ in labels only
#   ORDERS=300|2000   EXPERIMENT=A (70-100 pooled sizes) | B (70-90, test at 100)
#SBATCH --job-name=teacher-train
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --time=06:00:00
#SBATCH --output=runs/teachers/logs/train_%x_%j.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?submit from the repository root}"
ARM=${ARM:?}; ORDERS=${ORDERS:?}; EXPERIMENT=${EXPERIMENT:?A or B}; VOCAB=${VOCAB:-full}
SUPERVISION=${SUPERVISION:-demo}; MATCHED=${MATCHED:-0}
case "$EXPERIMENT" in A) SIZES="70 80 90 100";; B) SIZES="70 80 90";; *) exit 2;; esac
case "$VOCAB" in
  full)      FLAGS=(--extended --tier-b --battery);;
  nobattery) FLAGS=(--extended --tier-b);;
  nogrid)    FLAGS=(--extended --battery);;
  *) exit 2;;
esac
shopt -s nullglob
RUNS=()
if [ "$ARM" = pooled ]; then
  for r in $SIZES; do RUNS+=(runs/teachers/o${ORDERS}/*/r${r}_s*/*/r${r}_s*); done
  EXTRA=(--pooled); ROLLOUTS="runs/teachers/rollouts/o${ORDERS}"
else
  for r in $SIZES; do RUNS+=(runs/teachers/o${ORDERS}/${ARM}/r${r}_s*/${ARM}/r${r}_s*); done
  EXTRA=(--teacher "$ARM"); ROLLOUTS="runs/teachers/rollouts/o${ORDERS}/${ARM}"
fi
[ ${#RUNS[@]} -gt 0 ] || { echo "no runs for $ARM o$ORDERS $EXPERIMENT"; exit 1; }
case "$SUPERVISION" in
  demo)    NAME=$ARM
           [ "$MATCHED" = 1 ] && { EXTRA+=(--decisions-from "$ROLLOUTS"); NAME=${ARM}_matched; };;
  outcome) NAME=outcome_$ARM
           EXTRA+=(--outcome-rollouts "$ROLLOUTS" --decisions-from "$ROLLOUTS" --continuation behaviour
                   --outcome-objective jw2 --outcome-mode conservative);;
  *) exit 2;;
esac
export PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management:src/ilp_learning
pixi run python -m ilp_learning.train_rules "${RUNS[@]}" \
  --out "runs/teachers/rules/o${ORDERS}/${EXPERIMENT}/${VOCAB}/${NAME}" "${FLAGS[@]}" "${EXTRA[@]}"
