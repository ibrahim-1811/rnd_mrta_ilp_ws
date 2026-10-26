# real_warehouse_traffic_demo.py finds no synchronous conflict

Status: needs-triage

## Summary

`src/rmf_grid_traffic/scripts/real_warehouse_traffic_demo.py` aborts:

```text
RuntimeError: No synchronous conflict found in the bounded search set.
```

Reproduces on both maps:

```bash
PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management \
  python3 src/rmf_grid_traffic/scripts/real_warehouse_traffic_demo.py --delay 0
PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management \
  python3 src/rmf_grid_traffic/scripts/real_warehouse_traffic_demo.py \
    --map src/simulation_env/maps/CSV_To_Gazebo_v2.xlsx --delay 0
```

## Not a regression from S0 or Phase 8

Found while running the Phase 8 regression gate, 2026-10-22. It is
pre-existing:

- the committed `HEAD` version of the script fails identically
  (`git show HEAD:...` run directly)
- the script imports only `mrta_core.warehouse_map_xlsx` and
  `mrta_core.warehouse_routing`. It does not touch the allocator, the
  candidate window, the dispatch quantum, `WorkloadExecutor` or the
  dataset recorder, so nothing in S0 or Phase 8 is on its path

## Likely cause

The premise looks stale rather than the code broken. The demo searches a
bounded set of start/goal pairs for a *synchronous* conflict, and its
default map is still the V1 `CSV_To_Gazebo.xlsx`. Normal transit aisles
are one-way by frozen decision, so structural head-on conflicts in that
search set are exactly what the lane policy is supposed to have removed.
A demo whose job is to display one has nothing left to find.

If that reading is right, the fix is to retarget the search at the
conflict classes the current traffic model does produce -- vertex
contention, merge contention, intersection contention -- not to
reintroduce a bidirectional lane to make the demo pass.

## Coverage is not lost

The head-on and yield behaviour this demo visualises is asserted by
validators that pass:

```text
check_local_headon_resolution.py   PASS
check_engine_headon_yield.py       PASS
check_local_yield_selector.py      PASS
```

So this is a broken *visualisation*, not an unverified behaviour.

## Suggested disposition

Out of phase for Phase 8. Either retarget the search (small, self
contained) or retire the script in favour of the three validators above.
Needs a decision on which, hence `needs-triage`.
