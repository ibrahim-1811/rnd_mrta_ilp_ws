# M6-MP on Slurm (CPU only)

Nothing here submits itself. From the repository root:

```bash
scripts/slurm/make_tasks.sh 4                 # 28 corpus tasks, 112 rollout shards
mkdir -p runs/m6mp/logs
C=$(sbatch --parsable --array=0-27 scripts/slurm/m6mp_corpus.sbatch)
R=$(sbatch --parsable --array=0-111 --dependency=afterok:$C scripts/slurm/m6mp_rollouts.sbatch)
for E in A B; do for ARM in m4 m5_majority m5_plurality m6 m6_sens m6_nobattery m6_nogrid; do
  sbatch --dependency=afterok:$R --export=ALL,EXPERIMENT=$E,ARM=$ARM \
    --job-name=train-$E-$ARM scripts/slurm/m6mp_train.sbatch
done; done
```

- Seeds 104-110 only; `--purpose induction` and the label builders refuse any other.
- Every task writes its own directory: `runs/m6mp/corpus/r<N>_s<S>/`,
  `runs/m6mp/rollouts/r<N>_s<S>_shard<k>of<n>/`, `runs/m6mp/rules/<A|B>/<arm>/`.
- `QUOTA` (decisions per stratum, default 8) and `MAX_ACTIONS` (default 6) are
  environment variables of the rollout array. 6 strata x 8 = 48 decisions per run.
- Time and memory limits are estimates from one pilot run (see docs/CURRENT_PHASE.md);
  check `sacct` after the first array before scaling.
- Without Slurm, run any line of a .tsv by hand with `SLURM_ARRAY_TASK_ID=<i>` and
  `SLURM_SUBMIT_DIR=$PWD` set.

# Teachers, single-teacher and pooled ILP (groups A-D)

```bash
SHARDS=4 scripts/slurm/make_teacher_tasks.sh 300   # 224 corpus runs, 896 rollout shards
mkdir -p runs/teachers/logs
C=$(sbatch --parsable --array=0-223 scripts/slurm/teacher_corpus.sbatch)          # A
R=$(sbatch --parsable --array=0-895 --dependency=afterok:$C scripts/slurm/teacher_rollouts.sbatch)
T=""
for ARM in random nearest_manhattan nearest_astar earliest_finish multi_criteria auction hungarian milp pooled; do
  T="$T:$(sbatch --parsable --dependency=afterok:$C --export=ALL,ARM=$ARM,ORDERS=300,EXPERIMENT=A \
    --job-name=train-$ARM scripts/slurm/teacher_train.sbatch)"                    # B, C
  T="$T:$(sbatch --parsable --dependency=afterok:$R --export=ALL,ARM=$ARM,ORDERS=300,EXPERIMENT=A,SUPERVISION=outcome \
    --job-name=train-outcome-$ARM scripts/slurm/teacher_train.sbatch)"            # D
done
sbatch --array=0-3 --dependency=afterok${T} --export=ALL,ORDERS=300,EXPERIMENT=A \
  scripts/slurm/teacher_eval.sbatch
```

- `teacher_corpus.sbatch` records features and the teacher panel (`mp1` needs it).
- `teacher_rollouts.sbatch` (group D, per teacher): `mp1` candidates forced from the
  identical replayed state, continued by the run's own strategy, written to
  `runs/teachers/rollouts/o<ORDERS>/<strategy>/`. `QUOTA`, `MAX_ACTIONS` as for M6-MP.
- `teacher_train.sbatch SUPERVISION=outcome` trains `outcome_<ARM>` with
  `--continuation behaviour`; `ARM=pooled` pools the eight teachers' outcome labels.
  `MATCHED=1` restricts demonstration arms to the probed decisions (labels-only contrast).
- `teacher_eval.sbatch`: one task per fleet size (70, 80, 90, 100). It runs the eight
  teachers and every `rule_artifact.json` under `runs/teachers/rules/o<ORDERS>/<EXPERIMENT>/<VOCAB>/`
  as arms `ilp_<dir>` on validation seeds 116-120 (`--purpose backup`), then `compare_paired.py`. Add
  outcome-aware or panel-vote rules with `EXTRA_RULES="label=path ..."`.
- `run_experiment.py --rules` refuses an artifact trained on any evaluated seed. Final
  seeds (201-230) need `SEEDS=... PURPOSE=final` and are meant to be run once.
- `summary.csv` gives `rule_picks`, `fallback_picks`, `fallback_rate` for each learned run.

# Gate 2: oracle demonstrations (DECISIONS.md, "Gate 2 Pass Criterion and Order")

```bash
scripts/slurm/make_gate2_tasks.sh A          # 84 tasks: 3 teachers x 70-100 robots x seeds 104-110, 300 orders
for FIRST in 0 28 56; do
  sbatch -p hpc1 --cpus-per-task=28 --export=ALL,FIRST=$FIRST,COUNT=28 scripts/slurm/gate2.sbatch
done
```

- One oracle run per task (`--objective wholerun` for A, `horizon` for B), written as a run
  folder `train_rules` reads: `runs/gate2/o<orders>/<objective>/<teacher>/r<N>_s<S>/` with
  `run.json` (`label_source: oracle`), decisions, outcomes, features, teacher_labels and
  `oracle.json`. Live log per task: `runs/gate2/logs/<tag>.log`.
- Induction seeds only: `run_experiment.py --purpose induction` refuses any other seed.
- Option B (`make_gate2_tasks.sh B`) reuses the 1500-order teacher corpus in
  `runs/teachers/o1500/`; submit it with `--time=18:00:00 --mem=96G`, only after A.
- A task whose `oracle.json` exists is skipped, so a job can be resubmitted after a failure.

## Robust oracle (DECISIONS.md, "Robust Oracle Labels and the Combined Campaign")

`GATE2_CONTINUATIONS` makes each task a robust oracle: every candidate is rolled out under
each listed continuation policy, and a pick is taken only if it beats the teacher's pair in
all of them. Output goes to `runs/gate2/o<orders>/<objective>_robust/...`, never over the
plain oracle's folders. About 3x the plain oracle's time. Pilot (7 tasks, one job):

```bash
mkdir -p runs/gate2/logs
for S in 104 105 106 107 108 109 110; do printf "wholerun\t300\tnearest_astar\t100\t%s\t120\n" $S; done \
  > runs/gate2/pilot_robust.tsv
export GATE2_CONTINUATIONS="nearest_astar multi_criteria milp"
sbatch -p hpc1 --cpus-per-task=7 --time=04:00:00 --job-name=g2-robust-pilot \
  --export=ALL,FIRST=0,COUNT=7,GATE2_TSV=runs/gate2/pilot_robust.tsv \
  scripts/slurm/gate2.sbatch
```
