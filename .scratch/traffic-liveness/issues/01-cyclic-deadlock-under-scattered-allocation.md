# Cyclic deadlock under a scattering allocation policy

Status: ready-for-human (root cause found 2026-10-23; see Comments)

## Summary

At sustained load, a legal set of assignments can drive the fleet into a
**cyclic wait**: a closed chain of robots each holding the cell the next
one needs. The engine detects it and fails loudly rather than hanging:

```text
STALLED:
DEADLOCK DIAGNOSTICS
time: 1138.6667
waiting robots: 22

CYCLES
  robot_007 -> robot_019 -> robot_005 -> robot_016 -> robot_007
```

This is **not** the structural head-on deadlock the one-way lane policy
removes. Every robot here is moving in a legal direction. It is a
circular wait around the circulation loop, which one-way lanes do not
prevent.

## Reproduce

```bash
export PY=.pixi/envs/default/bin/python
export PP=src/mrta_core:src/simulation_env:src/task_management

PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
    --config src/simulation_env/test/fixtures/v2_reference.yaml --orders-file orders_500.csv \
    --quiet --progress 0 --strategy random --seed 42
```

Exits 1 with the diagnostics above.

## Scope: rare, and policy-dependent

22 robots, 500 orders, V2 map:

```text
strategy            seed   result
nearest_astar         42   500/500   1057.3333 s   26992.0 m
nearest_manhattan     42   500/500   1068.6667 s   27302.0 m
random                 1   500/500   1130.6667 s
random                 7   500/500   1126.0000 s
random                42   STALLED at t=1138.6667
random                99   500/500   1133.3333 s
```

At 2000 orders `nearest_astar` and `nearest_manhattan` both complete
2000/2000 cleanly; `random` stalls at t=1160.6667, on the same workload
prefix and so almost certainly the same cycle.

The pattern is consistent with occupancy rather than chance: the two
*nearest* policies keep a robot's work near where it already stands, so
the circulation loop never fills. `random` scatters assignments across
the hall, several robots cross the full loop at once, and a cycle
becomes reachable. The loaded condition is rare -- one seed in four
here -- but it is not a knife-edge artefact of one workload.

## Not caused by Phase 7 slice 1

- the traffic controller, the lane policy and the engine are untouched
  by slice 1
- `nearest_astar` is bit-identical before and after the
  `greedy_pairing` refactor (268.6667 s / 5798.0 m / 305.3333 s /
  64 decisions / 1 forced)
- a strategy only chooses *which* robot takes which order; the traffic
  layer is what must keep any legal assignment set live

`random` is simply the first strategy that scatters, so it is the first
to expose the gap. The gap is pre-existing.

## Why it matters for the R&D

Strategy comparison at high load is blocked while this stands, and the
bias is in the worst possible direction: the deadlock is more likely
under the policies that scatter, which means a naive or random baseline
can fail to complete while a grid-aware one finishes. Any makespan or
throughput table built without resolving this would **flatter the
grid-aware strategy for a traffic-layer reason rather than an
allocation reason**.

It also bounds the scalability study. W30 and W50 raise robot density,
which raises loop occupancy, which is exactly the condition that makes a
cycle reachable.

## What it is not

Not a battery or charging deadlock. The diagnostics show
`MOVE_COMPLETE` waits on occupied cells; chargers, holding cells and
reservations are all clean at the stall instant, and `check_b4`,
`check_b5` and `check_charger_evacuation` all pass.

## Suggested disposition

Out of phase for Phase 7 slice 1. This is traffic infrastructure and
belongs with the traffic controller, not with any strategy, and the
fixed-infrastructure rule means it must be solved identically for every
strategy.

Options worth weighing, in the order they should be considered:

1. **Cycle-breaking in the traffic controller.** The cycle is already
   detected for the diagnostic; the same detection could force one
   member to yield into a side cell. Needs a deterministic victim rule
   that is not `robot_id` as a scientific predicate -- lowest progress,
   or longest wait, would both be defensible.
2. **Reservation ordering.** Acquire the next cell under a global order
   so a cycle cannot form, at a throughput cost that must be measured.
3. **Bounded-wait escape.** A robot waiting beyond a threshold replans
   around the blocked cell. Cheapest to implement, but introduces a
   timeout constant into fixed infrastructure, which needs freezing in
   DECISIONS.md.

Whatever is chosen must not reintroduce bidirectional transit and must
not be strategy-specific.

## Evidence

Full diagnostics captured at the time of writing; reproduce with the
command above. Found 2026-10-22 while soak-testing Phase 7 slice 1.

## Comments

### 2026-10-23 — root cause found: 2 x 2 rotaries in the lane layout

The V2 deadlock above is four robots on cells (13,3) (14,3) (14,4)
(13,4). Those four road cells form a **rotary**: a 2 x 2 square whose
one-way arrows chase each other round it. With a robot on each cell,
each waits for the next forever. A new structural check
(`map_tool.py check`, `mrta_core/map_check.py`) finds **33** such
squares on V2, all in its open bands where alternating rows cross
alternating columns; V2's shelf blocks have none.

Confirmed in the other direction too. A generated 100-robot map whose
cross-bands formed rotaries deadlocked under `nearest_astar` at
t=268 s; the same map with each band flipped (east row above the west
row, which removes every rotary) ran 100 robots through 1000/1000
orders with 0 reservation violations.

So the near-term mitigation is in the **map**, not the controller:
`map_tool.py check` warns on every rotary, generated maps have none,
and docs/MAKE_A_MAP.md tells authors to remove them on any map meant
for a large fleet. See DECISIONS.md, "Rotaries".

What remains open here: the controller still cannot *break* a waiting
circle once one forms. Larger circles (6, 8, ... cells) are possible in
principle but need as many robots as cells, all wanting to advance at
once, and none has been observed. V2 itself still has its 33 rotaries;
changing V2 would move every frozen baseline, so it is left as is.
