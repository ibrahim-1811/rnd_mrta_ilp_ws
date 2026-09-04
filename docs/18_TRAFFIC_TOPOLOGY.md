# 18 — Directed warehouse traffic topology

Why the aisles are one-way, and what must stay true about them.

Authoritative source: `src/mrta_core/mrta_core/warehouse_routing.py`.
Everything else — A*, the executor, the RMF nav graph, Gazebo — derives
its lane rules from `directional_blocked_edges()`. Nothing restates
them.

---

## The problem this solves

The warehouse core used to be bidirectional. Two robots could legally
claim the same edge from opposite ends, each hold the cell the other
needed, and neither could move:

```
robot_006  (10,15) -> (10,16)
robot_013  (10,16) -> (10,15)
```

That is a *structural* deadlock: no arbitration policy can resolve it,
because both intents are legal. The 100-order workload stalled here at
t=224 with all 18 robots waiting — sixteen of them queued behind that
one pair.

There were **102 uncontrolled bidirectional core edges** in that map:

* 54 horizontal rungs in shelf rows (`y=6..14`, `y=20..28`)
* 48 vertical edges on shelf columns `x=4,7,10,13` inside the open bands
* 2 explicitly declared turnaround connectors

## The rule

> For every pair of adjacent **transit** cells, at most one of the two
> directions is legal.

If that holds, opposing intents on one edge cannot both be planned, so
head-on deadlock cannot occur — no arbitration required.

Only **service** edges stay two-way, because a robot must drive in and
reverse out:

* aisle ↔ shelf
* aisle ↔ dropoff
* aisle ↔ staging

Service cells are never through-routes: `navigation_grid()` blocks them
for any leg that is not targeting them.

## The layout (17 × 35)

```
x = 3, 6, 9, 12    northbound trunk aisles
x = 5, 8, 11, 14   southbound trunk aisles
x = 4, 7, 10, 13   shelf columns; open floor only inside the three
                   horizontal bands, northbound there
x = 1, 2           left staging frontage lanes   (y = 13..21)
x = 15             right staging frontage lane   (y = 13..21)

y = 1..5, 15..19, 29..33    open bands, alternating one-way rows
y = 6..14, 20..28           shelf blocks; the only horizontal core
                            edges are rungs between an aisle pair,
                            all westbound

eastbound rows : 2, 4, 15, 17, 19, 29, 31, 33
westbound rows : 1, 3, 5, 16, 18, 30, 32
```

### Why the bottom band runs opposite to the top

This is the one part that looks like an inconsistency and is not.

The bottom band (`y=1..5`) and the top band (`y=29..33`) are mirror
images geometrically, but the columns crossing them are **not**
mirrored: `x=3` is northbound and `x=14` is southbound over the whole
height.

So at the top, `(3,31)` is the *end* of northbound `x=3` and `(14,33)`
is the *start* of southbound `x=14` — both are fed correctly by
eastbound rows. At the bottom the roles invert: `(3,3)` is the *foot*
of the northbound column and `(14,1)` the *foot* of the southbound one.
With eastbound rows they became a source and a sink with no way in or
out, and the graph lost strong connectivity.

Flipping the bottom band's handedness feeds both from their own row.
That is what let the two turnaround connectors be removed entirely:
`BIDIRECTIONAL_CONNECTORS` is now **empty**.

## Invariants

These are enforced by `validate_directional_highways.py` and must hold
after any change to the map or the policy:

| Invariant | Value |
|---|---|
| uncontrolled bidirectional core edges | **0** |
| core strongly connected | PASS — one SCC, 347/347 |
| every shelf/dropoff/staging enterable and leavable | PASS — 130/130 |
| directed core edges | 559 |

Strong connectivity is not optional. One-way alone is easy; one-way
*and* every cell still reachable from every other is the real
constraint.

## What this costs, and what it buys

Real routes get longer, and asymmetric:

| | |
|---|---|
| mean true/Manhattan distance ratio | 1.883 |
| median | 1.348 |
| worst case | 25.0 — `(3,23) → (3,22)`, adjacent cells, 25 apart by road |
| pairs where Manhattan is exact | 26% |
| shelf pairs where `d(a,b) ≠ d(b,a)` | **90.9%** |

Two consequences for the research:

1. **Manhattan distance is not a usable cost model here.** It is
   symmetric; this warehouse is not. It stays useful only as A*'s
   admissible heuristic. Allocation must cost with A*, or it will
   systematically mis-rank candidate robots.

2. **Congestion is preserved, head-on is not.** The 18-robot stress
   scenario records 15 vertex conflicts and **0 edge conflicts**.
   Robots still queue, wait, and contend for cells — which is what the
   MRTA experiments need — but they can no longer face each other.

Despite longer individual routes, the fleet got faster, because it
stopped deadlocking: 100 orders went from a stall at t=224 to
completing in 315.3 s, and the 18-order makespan fell from 122.7 s to
102.0 s.

## Changing the map or the policy

Any edit to the XLSX or to `warehouse_routing.py` invalidates all of
the above. Re-run, in order:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/validate_directional_highways.py
PYTHONPATH=$PP $PY src/rmf_grid_traffic/scripts/generate_nav_graph.py \
    --map src/simulation_env/maps/CSV_To_Gazebo.xlsx \
    --output src/rmf_grid_traffic/config/warehouse_nav_graph.yaml
PYTHONPATH=$PP $PY src/rmf_grid_traffic/scripts/verify_nav_graph.py \
    --map src/simulation_env/maps/CSV_To_Gazebo.xlsx \
    --graph src/rmf_grid_traffic/config/warehouse_nav_graph.yaml
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py --orders 100 --progress 0
```

`discover_directional_highways.py` reports the map's aisle geometry and
is useful when deciding directions for a *new* layout.

## Consequences elsewhere

**Yield bays.** `select_local_yield_cell` requires a bay that is legal
to enter *and* to leave by the same edge, because the engine rejoins in
a single reverse step. On a one-way core that leaves exactly one kind
of bay: the aisle on the far side of a service cell. A robot in a
one-way aisle has no bay and simply waits — which is safe, because two
robots can no longer face each other there.

**Service entry is protected, not arbitrated.** A robot entering a
shelf or dropoff retains its approach cell for the whole service
(`retain_service_exit` in `fleet_engine.py`). That is what stops a
second robot occupying the retreat cell and deadlocking the pair on
the one edge class that is still two-way.

**RMF must mirror this exactly.** `generate_nav_graph.py` emits a lane
only in directions the policy permits, and refuses to write a graph
containing a two-way core edge. `verify_nav_graph.py` re-derives the
expected lane set independently and compares edge by edge.
