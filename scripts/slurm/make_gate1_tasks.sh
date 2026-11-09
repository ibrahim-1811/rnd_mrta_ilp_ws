#!/usr/bin/env bash
# Gate 1 task list: is "better than the teacher" possible at all? The oracle (rollout policy)
# on validation seeds 116-120, never used for training.
#   gate1.tsv   objective orders teacher robots seed horizon_s     one oracle run
# Option A: wholerun at 300 orders (exact objective). Option B: horizon (120 s) at 1500.
# Usage: scripts/slurm/make_gate1_tasks.sh [A] [B]      (default: A B)
set -euo pipefail
cd "$(dirname "$0")"
OPTIONS=${*:-"A B"}
TEACHERS="nearest_astar multi_criteria milp"
ROBOTS="70 80 90 100"
SEEDS="116 117 118 119 120"
: > gate1.tsv
for option in $OPTIONS; do
  case $option in
    A) objective=wholerun; orders=300;;
    B) objective=horizon; orders=1500;;
    *) echo "unknown option $option"; exit 2;;
  esac
  for t in $TEACHERS; do for r in $ROBOTS; do for s in $SEEDS; do
    printf "%s\t%s\t%s\t%s\t%s\t%s\n" "$objective" "$orders" "$t" "$r" "$s" 120 >> gate1.tsv
  done; done; done
done
echo "gate1.tsv: $(wc -l < gate1.tsv) oracle runs"
