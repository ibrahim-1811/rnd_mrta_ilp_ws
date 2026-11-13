#!/usr/bin/env bash
# M8 final campaign, evaluation (DECISIONS.md, "M8 Final Campaign"): one job = one length and
# one fleet size; all 20 arms on the same workloads, run_experiment's own parallelism.
#   arms   the 8 conventional strategies; ilp_<label> for every runs/campaign/rules/<label>
#          (single_<teacher> x 8, pooled, majority, plurality) and ilp_outcome_multi_criteria
#          (the Gate 2 robust rule set)
#   STAGE=dry    seeds 1-3, --purpose evaluation (catches failures; nothing is chosen from it)
#   STAGE=final  seeds 201-230, --purpose final (sealed: run once)
# Held-out transfer map (DECISIONS.md, "Held-out Transfer Campaign"): the same 20 arms and
# rule files, unchanged, on H100 via --map H100.xlsx:
#   STAGE=transfer-dry  seeds 1-3, --purpose evaluation (nothing is chosen from it)
#   STAGE=transfer      seeds 301-330, --purpose transfer (sealed: run once)
# Submit from the repository root, one job per length and fleet size, e.g.:
#   for O in 1500 5000; do for N in 70 80 90 100; do
#     sbatch -p hpc1 --export=ALL,STAGE=dry,ORDERS=$O,ROBOTS=$N scripts/slurm/campaign_eval.sbatch
#   done; done
# 5000 orders needs about 2.6 GB per run: the job asks 96 GB for 28 parallel runs.
# Output: runs/campaign/<stage>/o<orders>/r<robots>/summary.csv; resubmitting resumes.
#SBATCH --job-name=c8-eval
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=28
#SBATCH --mem=96G
#SBATCH --time=36:00:00
#SBATCH --output=runs/campaign/logs/eval_%x_%j.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?submit from the repository root}"
STAGE=${STAGE:?dry, final, transfer-dry or transfer}; ORDERS=${ORDERS:?1500 or 5000}; ROBOTS=${ROBOTS:?fleet size}
case "$STAGE" in
  dry)   SEEDS="1 2 3"; PURPOSE=evaluation;;
  final) SEEDS=$(seq -s " " 201 230); PURPOSE=final;;
  transfer-dry) SEEDS="1 2 3"; PURPOSE=evaluation; MAP=H100.xlsx;;
  transfer)     SEEDS=$(seq -s " " 301 330); PURPOSE=transfer; MAP=H100.xlsx;;
  *) echo "STAGE is dry, final, transfer-dry or transfer"; exit 2;;
esac
MAP_ARGS=(); [ -n "${MAP:-}" ] && MAP_ARGS=(--map "$MAP")
command -v pixi >/dev/null || export PATH="${PIXI_BIN:-$(cd .. && pwd)/.pixi_home/bin}:$PATH"
command -v pixi >/dev/null || { echo "pixi not found; set PIXI_BIN"; exit 2; }
export PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management:src/ilp_learning
STRATEGIES="random nearest_manhattan nearest_astar earliest_finish multi_criteria auction hungarian milp"
OUTCOME=runs/gate2/rules/o300_robust/oracle_multi_criteria/rule_artifact.json
RULES=()
for f in runs/campaign/rules/*/rule_artifact.json; do
  [ -e "$f" ] && RULES+=("$(basename "$(dirname "$f")")=$f")
done
[ -e "$OUTCOME" ] && RULES+=("outcome_multi_criteria=$OUTCOME")
[ ${#RULES[@]} -eq 12 ] || { echo "expected 12 rule sets, found ${#RULES[@]}: ${RULES[*]}"; exit 1; }
NAME="campaign/${STAGE}/o${ORDERS}/r${ROBOTS}"
mkdir -p runs/campaign/logs
echo "c8 eval $NAME: 8 strategies + ${#RULES[@]} rule sets, seeds $SEEDS, map ${MAP:-config}, start $(date +%T) on $(hostname)"
# shellcheck disable=SC2086
pixi run python src/simulation_env/scripts/run_experiment.py \
  --name "$NAME" --strategies $STRATEGIES --rules "${RULES[@]}" --robots "$ROBOTS" --seeds $SEEDS \
  --orders "$ORDERS" --workload-orders "$ORDERS" --quantum 3 --window fleet_size \
  --purpose "$PURPOSE" "${MAP_ARGS[@]}" --jobs "${SLURM_CPUS_PER_TASK:-1}" --resume || STATUS=$?
pixi run python src/simulation_env/scripts/compare_paired.py --name "$NAME" || true
echo "c8 eval $NAME end $(date +%T), exit ${STATUS:-0}"
exit "${STATUS:-0}"
