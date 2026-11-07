#!/usr/bin/env bash
# Writes the task lists for the M6-MP collection arrays. Training seeds only (104-110).
#   corpus.tsv    robots seed               one corpus run per line (array 1)
#   rollouts.tsv  robots seed shard shards  one rollout shard per line (array 2)
# Usage: scripts/slurm/make_tasks.sh [SHARDS]   (default 4)
set -euo pipefail
cd "$(dirname "$0")"
SHARDS=${1:-4}
ROBOTS="70 80 90 100"
SEEDS="104 105 106 107 108 109 110"
: > corpus.tsv; : > rollouts.tsv
for r in $ROBOTS; do for s in $SEEDS; do
  printf "%s\t%s\n" "$r" "$s" >> corpus.tsv
  for k in $(seq 0 $((SHARDS - 1))); do printf "%s\t%s\t%s\t%s\n" "$r" "$s" "$k" "$SHARDS" >> rollouts.tsv; done
done; done
echo "corpus.tsv: $(wc -l < corpus.tsv) tasks; rollouts.tsv: $(wc -l < rollouts.tsv) tasks"
