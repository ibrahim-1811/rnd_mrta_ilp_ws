#!/usr/bin/env bash
# One job per (experiment, supervision): M4, M5 and M6 on the same sampled decisions,
# same vocabulary (extended + Tier B + battery), so they differ in labels only.
#   EXPERIMENT=A  pooled: trains on 70, 80, 90, 100   (evaluate on 116-120 at each size)
#   EXPERIMENT=B  transfer: trains on 70, 80, 90      (evaluate at 100 without retraining)
#   ARM in m4 m5_majority m5_plurality m6 m6_sens m6_nobattery m6_nogrid
#SBATCH --job-name=m6mp-train
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --time=06:00:00
#SBATCH --output=runs/m6mp/logs/train_%x_%j.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?submit from the repository root}"
EXPERIMENT=${EXPERIMENT:?A or B}; ARM=${ARM:?}
case "$EXPERIMENT" in A) SIZES="70 80 90 100";; B) SIZES="70 80 90";; *) exit 2;; esac
RUNS=(); for r in $SIZES; do RUNS+=(runs/m6mp/corpus/r${r}_s*/nearest_astar/r${r}_s*); done
COMMON=(--decisions-from runs/m6mp/rollouts --extended --tier-b --battery)
case "$ARM" in
  m4)            EXTRA=(--label-teacher multi_criteria);;
  m5_majority)   EXTRA=(--label-policy majority);;
  m5_plurality)  EXTRA=(--label-policy plurality);;
  m6)            EXTRA=(--outcome-rollouts runs/m6mp/rollouts --outcome-objective jw2 --outcome-mode conservative);;
  m6_sens)       EXTRA=(--outcome-rollouts runs/m6mp/rollouts --outcome-objective jw2 --outcome-mode m6_1);;
  m6_nobattery)  COMMON=(--decisions-from runs/m6mp/rollouts --extended --tier-b)
                 EXTRA=(--outcome-rollouts runs/m6mp/rollouts --outcome-objective jw2 --outcome-mode conservative);;
  m6_nogrid)     COMMON=(--decisions-from runs/m6mp/rollouts --extended --battery)
                 EXTRA=(--outcome-rollouts runs/m6mp/rollouts --outcome-objective jw2 --outcome-mode conservative);;
  *) echo "unknown ARM $ARM"; exit 2;;
esac
export PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management:src/ilp_learning
pixi run python -m ilp_learning.train_rules "${RUNS[@]}" \
  --out "runs/m6mp/rules/${EXPERIMENT}/${ARM}" "${COMMON[@]}" "${EXTRA[@]}"
