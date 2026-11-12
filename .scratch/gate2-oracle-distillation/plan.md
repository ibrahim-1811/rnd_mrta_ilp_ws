# Gate 2: can ILP rules learn the oracle's gain?

Status: ready-for-human (2026-11-12: code committed 248aee0; option A oracle runs done, 84/84, jobs 477249-477251; decided: --workload in, no pooled arm, one-sided test; next: owner submits the 6 training jobs, commands in status-2026-11-12.md)

## Where this comes from

Gate 1 (cluster jobs 477088-477091, results in `runs/gate1/`, table in
`runs/gate1/summary.txt`) asked: can anything beat the teacher at all?
The oracle (`ilp_learning.m6_oracle`: at every decision, simulate each candidate
pair from the identical state and take the decisively best one) did:

| Validation seeds 116-120, 70-100 robots | A: 300 orders, wholerun | B: 1500 orders, horizon 120 s |
|---|---|---|
| makespan wins vs teacher | 60/60 | 56/60 |
| mean makespan change | -7.9 % | -3.7 % |
| mean flow time | -5.7 % | -6.3 % |
| waiting | -31 % | -42 % |
| distance | ~0 % | +0.6 % |
| gain at 70 / 100 robots | -4.8 % / -13.0 % | -1.0 % / -8.1 % |

The oracle is not a deployable policy and not ILP: it re-simulates the future at
every decision (Gate 1B took 6-13 h per 30 runs). Gate 2 asks whether a rule set
trained on the oracle's choices keeps some of that gain when it runs on its own.

## Question and pass criterion (to freeze before any run)

Q: does an ILP policy trained on oracle choices beat its teacher, closed loop, on
validation seeds 116-120?

Proposed pass, per teacher, all on the same 20 paired runs (4 fleets x 5 seeds):

1. Problem A gate: non-empty program, rules fire, fallback_rate <= 0.5.
2. Mean order makespan lower than the teacher's, paired Wilcoxon signed-rank,
   Holm over the three teachers, alpha 0.05.
3. Reported, not gated: recovered share = (teacher - student) / (teacher - oracle),
   flow time, waiting, distance, charging, rule count and length, decision time.

## What it takes

### Data (cluster)

Oracle runs on the induction seeds 104-110 (never the validation seeds):
3 teachers (nearest_astar, multi_criteria, milp) x 4 fleets (70, 80, 90, 100) x
7 seeds = 84 oracle runs per option.

| | A: 300 orders, wholerun | B: 1500 orders, horizon |
|---|---|---|
| teacher runs | record (as Gate 1 did) | already on the cluster: `runs/teachers/o1500` |
| estimated wall | ~30 min per 28-run job, 3 jobs | 6-13 h per 28-run job, 3 jobs |
| cores held | 84 for ~0.5 h | 84 for up to ~13 h |

A first; B only after A shows a student can learn anything.

### Code (small, needs approval)

1. `m6_oracle` writes a trainable run folder: `run.json` (manifest, marked with
   label source `oracle:<objective>`), `decisions.parquet` with `selected` = the
   oracle's pick, and `features.parquet` (Tier B, battery and workload predicates
   need it). Also records the teacher's own pair in each verdict.
2. `train_rules --label-source oracle`: examples from the oracle's picks, same
   decision cap, sampling, `bk.pl`, bias, thresholds and matcher as the other groups.
3. Same-state imitation control: on the same oracle-trajectory decisions, labels =
   the teacher's own pair. Then oracle labels vs imitation labels differ in labels
   alone, which is what makes H1 valid (proposal-map.md).
4. Slurm task list and runner, like `make_gate1_tasks.sh` / `gate1_task.sh`.
5. Tests: oracle run folder loads in `train_rules`; induction-seed guard holds;
   a student artifact from one tiny oracle run runs closed loop.

### Training and evaluation

Per teacher, both label sources, `--extended --tier-b --battery --workload`,
Popper 300 s (unchanged). Closed loop on 116-120, 300 orders (A) / 1500 (B).
Pooled (all three teachers) as a secondary arm: dropped 2026-11-12 (not part of the pass criterion).

## Decisions needed

1. Oracle demonstrations become the primary group-D label (outcome-corrected
   labels kept as a recorded earlier result)? Or a separate arm?
2. The pass criterion above, frozen as written?
3. Option A first, B only if A learns non-empty rules with fallback <= 0.5?

## Risks

- The oracle may win for reasons the predicates cannot express (it sees the
  future; rules see only the decision-time state). Then the student imitates the
  teacher's picks plus noise. The earlier outcome-corrected labels gave no gain
  (CURRENT_PHASE.md, 2026-11-09), so a null result is possible and will be reported.
- One distillation round trains on the oracle's own states only; a student that
  drifts off them is untrained there (DAgger/VIPER-style re-labelling is the fix,
  only if time permits).
- Option B labels come from an approximate objective (4 of 60 Gate 1B losses).
