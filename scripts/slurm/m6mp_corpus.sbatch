#!/usr/bin/env bash
# Array 1: record one nearest_astar run per (robots, seed): 2000 orders, quantum 3,
# features and teacher panel, induction seeds only. Isolated output per task.
#SBATCH --job-name=m6mp-corpus
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=01:00:00
#SBATCH --output=runs/m6mp/logs/corpus_%A_%a.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?submit from the repository root}"
read -r ROBOTS SEED < <(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" scripts/slurm/corpus.tsv)
NAME="m6mp/corpus/r${ROBOTS}_s${SEED}"
pixi run python src/simulation_env/scripts/run_experiment.py --name "$NAME" \
  --strategies nearest_astar --robots "$ROBOTS" --seeds "$SEED" \
  --orders 2000 --workload-orders 2000 --quantum 3 --window fleet_size \
  --purpose induction --features --teacher-panel --jobs 1 --resume
