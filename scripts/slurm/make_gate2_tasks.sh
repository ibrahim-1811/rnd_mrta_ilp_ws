#!/usr/bin/env bash
# Gate 2 task list: oracle demonstrations on the induction seeds 104-110 (DECISIONS.md,
# "Gate 2 Pass Criterion and Order"). Each task records one oracle run as a run folder
# train_rules can read. Validation seeds 116-120 are for the closed-loop test only.
#   gate2.tsv   objective orders teacher robots seed horizon_s     one oracle run
# Option A: wholerun at 300 orders. Option B: horizon (120 s) at 1500, only after A.
# Usage: scripts/slurm/make_gate2_tasks.sh [A] [B]      (default: A)
set -euo pipefail
cd "$(dirname "$0")"
OPTIONS=${*:-"A"}
TEACHERS="nearest_astar multi_criteria milp"
ROBOTS="70 80 90 100"
SEEDS="104 105 106 107 108 109 110"
: > gate2.tsv
for option in $OPTIONS; do
  case $option in
    A) objective=wholerun; orders=300;;
    B) objective=horizon; orders=1500;;
    *) echo "unknown option $option"; exit 2;;
  esac
  for t in $TEACHERS; do for r in $ROBOTS; do for s in $SEEDS; do
    printf "%s\t%s\t%s\t%s\t%s\t%s\n" "$objective" "$orders" "$t" "$r" "$s" 120 >> gate2.tsv
  done; done; done
done
echo "gate2.tsv: $(wc -l < gate2.tsv) oracle runs"
