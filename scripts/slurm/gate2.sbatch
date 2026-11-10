#!/usr/bin/env bash
# Gate 2 oracle demonstrations, packed: this job runs task lines FIRST .. FIRST+COUNT-1
# of scripts/slurm/gate2.tsv in parallel, one core each (the cluster allows only 5 tasks
# per job array). Submit from the repository root, e.g. for 84 tasks:
#   for FIRST in 0 28 56; do
#     sbatch -p hpc1 --cpus-per-task=28 --export=ALL,FIRST=$FIRST,COUNT=28 scripts/slurm/gate2.sbatch
#   done
# Option B (1500 orders) needs longer and more memory: add --time=18:00:00 --mem=96G.
#SBATCH --job-name=gate2
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=28
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=runs/gate2/logs/slurm_%j.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?submit from the repository root}"
: "${FIRST:?FIRST task line (0-based)}" "${COUNT:?COUNT task lines}"
echo "gate2 job $SLURM_JOB_ID on $(hostname): tasks $FIRST..$((FIRST + COUNT - 1)), start $(date +%T)"
FAILED=0
seq "$FIRST" $((FIRST + COUNT - 1)) | xargs -P "$COUNT" -I{} scripts/slurm/gate2_task.sh {} || FAILED=1
echo "gate2 job $SLURM_JOB_ID end $(date +%T), failed=$FAILED"
exit $FAILED
