# 08 — Explainability

The project title claims *explainable* task allocation. This document records what
explanation capability exists today.

## Status: PARTIAL — one-line text reasons, plus a full numeric decision trace

Two mechanisms exist. Neither is a symbolic explanation system.

### 1. `explain()` strings

Every strategy implements `explain(candidates, chosen) -> str`
(`allocation_strategy.py:56`). The base implementation is generic; five strategies
override it with something informative:

| Strategy | Example output |
|---|---|
| base (7 strategies) | `spt: selected robot 3` |
| `nearest` | `nearest: robot 2 closest at 37.0 m` |
| `heuristic` | `heuristic: robot 1 feasible, workload=0, distance=8.0 m, battery=90%` |
| `score` | `score: robot 1 min cost C_ij=1.234` |
| `bid` / `auction` | `auction: robot 4 won with bid=0.4213` |
| any, on defer | `auction: all bids zero -> defer` |

The string is logged to the launch console, published inside
`/ilp/task_allocation_event`, and written to the `explanation` column of every dataset
row.

**Limitation:** it describes only the **winner**. `round_robin`, `random`, `least_busy`,
`highest_battery`, `spt`, `lpt` and `edf` emit only `"<name>: selected robot N"`, which
states the outcome without any reason at all.

### 2. The decision trace — the real explanatory asset

`_publish_event` (`task_manager.py:258-284`) publishes, for **every** decision, a JSON
payload containing the task, the strategy, the decision, the chosen robot, the allocator
runtime, and **the full feature vector of every candidate considered** — chosen and
rejected alike.

`ilp_dataset_logger` flattens this to one CSV row per candidate per decision, with
`is_chosen` marking the winner. That gives a complete, machine-readable record of what
the allocator saw.

## Can the system answer these questions?

### "Why was Robot R2 assigned Task T7?" — ✅ **Yes, partially**

The trace gives R2's full feature vector and the strategy's one-line reason. Real
logged example:

```
task_id=order_1_task_1  strategy=nearest  decision=assign  chosen_robot=2
explanation="nearest: robot 2 closest at 37.0 m"
```

What is missing: for the 7 strategies without an `explain()` override you get the
numbers but no statement of the rule that used them.

### "Why was Robot R1 *not* selected?" — ⚠ **Partially, and only by hand**

Rejected candidates *are* logged with full features, so the comparison is recoverable:

```
candidate_robot=1  is_chosen=0  distance=38.0  travel_time=24.553  energy_pct=21.995
candidate_robot=2  is_chosen=1  distance=37.0  travel_time=24.053  energy_pct=21.721
```

A human can see R1 lost by 1 m. But the system never *generates* a contrastive
statement — there is no "R1 was rejected because …" output. You must diff the rows
yourself.

Two hard limits:

- **A robot busy at decision time is not a candidate at all**, so it never appears in
  the trace. "Why not R1?" is unanswerable when the true answer is "R1 was not idle" —
  that fact is simply absent from the record.
- **Half of all decisions have only one candidate.** Measured over a 10-order run:
  5 decisions logged 1 candidate, 3 logged 2, 1 logged 3, 1 logged 4. With a single
  candidate there is no contrast to explain.

### "What contributed most to the allocation cost?" — ❌ **No**

**There is no cost decomposition anywhere.** `task_cost` (`features.py:59`) returns a
single scalar; the individual terms `λ1T`, `λ2D`, `λ3G`, `λ4E`, `λ5W`, `λ6q` are
computed inline in one expression and never stored, published or logged.

The dataset logs the *raw features* (`distance`, `travel_time`, `congestion`,
`energy_pct`) but **not the weighted contributions**, and not the total `C_ij` either.
A reader can recompute the breakdown only by knowing the hard-coded `CostWeights` and
redoing the arithmetic.

For `score`, `bid` and `auction` — the only strategies where the question is meaningful
— term attribution would be a small change and a large gain.

## What does *not* exist

| Capability | Status |
|---|---|
| Reason codes (enumerated, machine-readable) | ❌ free text only |
| Cost decomposition / term attribution | ❌ not implemented |
| Generated selected-vs-rejected comparison | ❌ raw rows only, no generated contrast |
| Counterfactual explanation | ❌ not implemented |
| Symbolic/Prolog explanation at runtime | ❌ `symbolic.py` has no caller |
| Prolog runtime (`swipl`, Janus) | ❌ not installed, not integrated |
| Learned rule citation | ❌ no rules exist |
| Decision trace | ✅ **implemented and complete** |
| Per-decision logging | ✅ implemented |
| Exported explanation data | ✅ CSV column `explanation` |
| Explanation of *deferrals* | ⚠ string only (`"all bids zero -> defer"`) |

## The unused symbolic layer

`mrta_core/symbolic.py` is the intended foundation for real symbolic explanation —
`battery_high(r1)`, `short_path(r1,t7)`, `battery_feasible(r1,t7)`, and so on, with
configurable thresholds and Prolog serialisation. It is fully implemented and covered by
12 unit tests.

**No node imports it.** Bridging it into `task_manager._publish_event` would upgrade the
trace from numbers to predicates and is the obvious next step for the explainability
strand.

Before doing so, note the unit mismatch flagged in
[06_OUTCOME_AWARE_ILP.md](06_OUTCOME_AWARE_ILP.md): `PredicateThresholds` expects
battery as a 0–1 fraction while the live system uses 0–100 percent.

## Honest summary

The repository has **traceability**, not **explainability**.

Every decision is recorded with enough numeric detail to *reconstruct* why it happened,
which is a genuinely useful and better-than-average foundation. But the system does not
*produce* explanations: it does not decompose costs, does not articulate why losers
lost, does not use symbolic vocabulary, and cannot cite a rule. Against the claim
"explainable task allocation", the current state is a solid substrate with the
explanation layer not yet built.
