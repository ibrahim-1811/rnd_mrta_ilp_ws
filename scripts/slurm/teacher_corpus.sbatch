#!/usr/bin/env bash
# Group A: one strategy drives one run (quantum 3, features recorded). Each task writes
# its own directory runs/teachers/o<orders>/<strategy>/r<N>_s<S>/. The teacher panel
# (teacher_labels.parquet) is recorded too: the mp1 rollouts of group D need it.
#SBATCH --job-name=teacher-corpus
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=02:00:00
#SBATCH --output=runs/teachers/logs/corpus_%A_%a.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?submit from the repository root}"
read -r STRATEGY ROBOTS SEED ORDERS < <(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" scripts/slurm/teacher_corpus.tsv)
pixi run python src/simulation_env/scripts/run_experiment.py \
  --name "teachers/o${ORDERS}/${STRATEGY}/r${ROBOTS}_s${SEED}" \
  --strategies "$STRATEGY" --robots "$ROBOTS" --seeds "$SEED" \
  --orders "$ORDERS" --workload-orders "$ORDERS" --quantum 3 --window fleet_size \
  --purpose induction --features --teacher-panel --jobs 1 --resume
