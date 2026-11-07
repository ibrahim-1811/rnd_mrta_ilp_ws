#!/usr/bin/env bash
# Task lists for groups A-D: every conventional strategy drives its own runs. Training
# seeds only (104-110).
#   teacher_corpus.tsv    strategy robots seed orders               one recorded run
#   teacher_rollouts.tsv  strategy robots seed orders shard shards  one mp1 rollout shard
#                         (group D, per teacher: the run's own strategy continues)
# Usage: SHARDS=4 scripts/slurm/make_teacher_tasks.sh [ORDERS...]   (default: 300 2000)
set -euo pipefail
cd "$(dirname "$0")"
ORDERS=${*:-"300 2000"}
SHARDS=${SHARDS:-4}
STRATEGIES="random nearest_manhattan nearest_astar earliest_finish multi_criteria auction hungarian milp"
ROBOTS="70 80 90 100"
SEEDS="104 105 106 107 108 109 110"
: > teacher_corpus.tsv; : > teacher_rollouts.tsv
for o in $ORDERS; do for s in $STRATEGIES; do for r in $ROBOTS; do for k in $SEEDS; do
  printf "%s\t%s\t%s\t%s\n" "$s" "$r" "$k" "$o" >> teacher_corpus.tsv
  for i in $(seq 0 $((SHARDS - 1))); do
    printf "%s\t%s\t%s\t%s\t%s\t%s\n" "$s" "$r" "$k" "$o" "$i" "$SHARDS" >> teacher_rollouts.tsv
  done
done; done; done; done
echo "teacher_corpus.tsv: $(wc -l < teacher_corpus.tsv) runs; teacher_rollouts.tsv: $(wc -l < teacher_rollouts.tsv) shards"
