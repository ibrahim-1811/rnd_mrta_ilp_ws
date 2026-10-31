# Spec-to-code predicate names

Status: needs-triage (a reference for vocabulary decision 5; it freezes nothing)

Spec names are from `spec.md` 3.2. Code names are from `bias_templates.py`. "Tie-inclusive" means every entity at the extreme gets the fact.

## Implemented, Tier A

| Code | Spec | Note |
|---|---|---|
| `nearest_static/3` | `best_cost_for_robot/3` | Tie-inclusive |
| `second_nearest_static/3` | `second_cost_for_robot/3` | Second distinct value |
| `best_cost_for_order/3` | `best_cost_for_order/3` | Tie-inclusive; added in M2 corrections |
| `earliest_static_finish/3` | `best_finish_for_robot/3` | Tie-inclusive; derived from the manifest |
| `second_earliest_static_finish/3` | none | |
| `closer_static/4` | `cheaper_for_robot/4` | Strict, but bounded: 3 cost levels, reach 2. Spec says all pairs, which is about 495k facts per decision at R100 with W=fleet size |
| `earlier_static_finish/4` | none | Same bound |
| `oldest_fifo/2` | `front_of_window_order/2` | Spec form is `fifo_position == 0` |
| `prior_fifo/3` | none | Bounded to 3 following orders |
| `candidate_pair/3`, `candidate_robot/2`, `candidate_order/2` | none | Structural |

## Implemented, Tier B

Spec 5.2 and DECISIONS.md item 7 replaced the original Tier B list, so names differ.

| Code | Spec | Note |
|---|---|---|
| `lowest_occupied_for_robot/3` | same, 5.2 | Tie-inclusive |
| `high_occupied_now/3` | same, 5.2 | |
| `fewest_conflict_robots_for_robot/3` | same, 5.2 | Tie-inclusive |
| `high_conflict_robots/3` | same, 5.2 | |
| `fewest_conflicts_for_robot/3` | none | Tie-inclusive; on `st_conflicts_30s` |
| `low_local_density/2` | `high_local_density/2` | Opposite polarity |
| `low_route_overlap/3` | `high_route_overlap/3`, `lowest_overlap_for_robot/3` | Opposite polarity; per-pair threshold |

## In the spec, not in the code

`near_best_cost_for_robot`, `best_finish_for_order` (identical to `best_cost_for_order` on 2,623 measured decisions), `oldest_eligible_for_robot` (identical to `front_of_window_order` on the same data), `highest_margin_for_robot`, `unique_robot_for_order`, `unique_order_for_robot`, `cheaper_robot_for_order`, `single_shelf_order`, `multi_shelf_order`, `aging_window_order`, `low_soc_robot`, `tight_energy_margin`, `ample_energy_margin`, `near_charger_dropoff`, `batch_contention`, `high_fleet_busy_ratio`, `high_queue_pressure`, `charging_active`, `conflict_free_30s`, `high_st_conflict_30s`.

## Emitted, not in the bias

`decision/1`, `fleet_load/2`, `free_robots_bin/2`: facts Popper cannot use.

## Counts

Bias today: 12 Tier A + 7 Tier B = 19 body predicates. Spec: 24 Tier A + 7 Tier B = 31. The "21" and "31" figures in earlier notes mix these two sets.
