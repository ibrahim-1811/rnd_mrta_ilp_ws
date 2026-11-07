#!/usr/bin/env bash
# Closed-loop evaluation, groups A-D: the eight teachers and every learned rule set of one
# training configuration, paired on the same workloads. One array task = one fleet size.
#   --array=0-3  ->  70 80 90 100 robots
#   ORDERS=300|2000   EXPERIMENT=A|B   VOCAB=full|nobattery|nogrid
#   SEEDS     default 116-120, the M4-M6 validation pool (--purpose backup; 111-115 are
#             spent by M3). Final 201-230 only with SEEDS=... PURPOSE=final, once.
#   EXTRA_RULES  more LABEL=ARTIFACT entries, e.g. outcome-aware rules from runs/m6mp/rules
# run_experiment refuses any artifact trained on an evaluated seed.
#SBATCH --job-name=teacher-eval
#SBATCH --cpus-per-task=8
#SBATCH --mem=24G
#SBATCH --time=12:00:00
#SBATCH --output=runs/teachers/logs/eval_%A_%a.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?submit from the repository root}"
ORDERS=${ORDERS:?}; EXPERIMENT=${EXPERIMENT:?A or B}; VOCAB=${VOCAB:-full}
SEEDS=${SEEDS:-"116 117 118 119 120"}; PURPOSE=${PURPOSE:-backup}
SIZES=(70 80 90 100); ROBOTS=${SIZES[$SLURM_ARRAY_TASK_ID]}
STRATEGIES="random nearest_manhattan nearest_astar earliest_finish multi_criteria auction hungarian milp"
RULES=()
for f in runs/teachers/rules/o${ORDERS}/${EXPERIMENT}/${VOCAB}/*/rule_artifact.json; do
  [ -e "$f" ] && RULES+=("$(basename "$(dirname "$f")")=$f")
done
# shellcheck disable=SC2206
RULES+=(${EXTRA_RULES:-})
[ ${#RULES[@]} -gt 0 ] || { echo "no rule artifacts for o$ORDERS $EXPERIMENT $VOCAB"; exit 1; }
# shellcheck disable=SC2086
pixi run python src/simulation_env/scripts/run_experiment.py \
  --name "teachers/eval/o${ORDERS}/${EXPERIMENT}/${VOCAB}/${PURPOSE}/r${ROBOTS}" \
  --strategies $STRATEGIES --rules "${RULES[@]}" --robots "$ROBOTS" --seeds $SEEDS \
  --orders "$ORDERS" --workload-orders "$ORDERS" --quantum 3 --window fleet_size \
  --purpose "$PURPOSE" --jobs "${SLURM_CPUS_PER_TASK:-1}" --resume
pixi run python src/simulation_env/scripts/compare_paired.py \
  --name "teachers/eval/o${ORDERS}/${EXPERIMENT}/${VOCAB}/${PURPOSE}/r${ROBOTS}"
