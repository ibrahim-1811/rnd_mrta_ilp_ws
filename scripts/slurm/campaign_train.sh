#!/usr/bin/env bash
# M8 final campaign, training (DECISIONS.md, "M8 Final Campaign"): submits one Slurm job per
# rule set, all from the 1500-order teacher corpus (runs/teachers/o1500, seeds 104-110,
# 70-100 robots), vocabulary as Gate 2. Rule sets land in runs/campaign/rules/<arm>/:
#   single_<teacher>   B: that teacher's runs and picks, one per conventional strategy (8)
#   pooled             C: all eight teachers' runs, each labelled by its own picks
#   majority/plurality C: the panel's votes on nearest_astar states (M5 label policies)
# The outcome-aware arm D is the Gate 2 robust rule set and is not retrained here.
# Run from the repository root on the cluster:   scripts/slurm/campaign_train.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
command -v pixi >/dev/null || export PATH="${PIXI_BIN:-$(cd .. && pwd)/.pixi_home/bin}:$PATH"
command -v pixi >/dev/null || { echo "pixi not found; set PIXI_BIN"; exit 2; }
CORPUS=runs/teachers/o1500
OUT=runs/campaign/rules
VOCAB="--extended --tier-b --battery --workload"
TEACHERS="random nearest_manhattan nearest_astar earliest_finish multi_criteria auction hungarian milp"
mkdir -p "$OUT" runs/campaign/logs
export PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management:src/ilp_learning

submit() {  # submit NAME "train_rules arguments"
  local name=$1 args=$2
  if [ -f "$OUT/$name/rule_artifact.json" ]; then echo "$name: trained already"; return; fi
  sbatch -p hpc1 --cpus-per-task=1 --mem=32G --time=06:00:00 --job-name="c8-$name" \
    --output="runs/campaign/logs/train_${name}_%j.out" \
    --wrap="pixi run python -m ilp_learning.train_rules $args --out $OUT/$name $VOCAB"
}

for T in $TEACHERS; do
  submit "single_$T" "$CORPUS/$T/r*_s*/$T/r*_s* --teacher $T"
done
submit pooled "$CORPUS/*/r*_s*/*/r*_s* --pooled"
for P in majority plurality; do
  submit "$P" "$CORPUS/nearest_astar/r*_s*/nearest_astar/r*_s* --teacher nearest_astar --label-policy $P"
done
