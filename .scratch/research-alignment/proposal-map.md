# Proposal to current work

Status: ready-for-human

Source: `Final_RnD_Proposal_GridAware_ILP_MRTA.tex` (May 2026). Where the proposal and a frozen decision differ, the frozen decision is what runs, and the difference is reported here so the final report can state it.

## Questions and what answers them

| Item | Claim | Answered by | State |
|---|---|---|---|
| RQ1 | State, grid and outcomes as predicates | M2 data layer, Tier A and Tier B | Tier A live; Tier B has offline/online parity |
| RQ2 | Compact rules from multi-strategy traces | M4, M5 | Not built. `assign` only: see "Differences" |
| RQ3, H1 | Outcome labels beat imitation on mission time | M6 against M4 and M5 | Not built |
| RQ4, H3 | Grid and congestion predicates help | Tier A against Tier A+B, same supervision, across M4-M6 | Possible now that Tier B runs online |
| H2 | Less congestion delay than nearest and score-based, congested scenarios | M6 against `nearest_astar`, `multi_criteria`, at 70 and 100 robots | Not built |
| RQ5, H4 | Scale and online runtime | Decision time at each fleet size | Tier A measured in M3; Tier B cost measured, see CURRENT_PHASE |
| RQ6, H5 | Trade-offs, explanation coverage | M7 | Rule trace exists; explanations not built |

## Why the order matters

- M4 and M5 are comparison baselines. They are not expected to beat their teachers.
- H1 is only valid if M4, M5 and M6 differ in labels alone: the same decisions, `bk.pl`, `bias.pl`, thresholds and matcher (M2 spec 6.1).
- H3 is only valid if Tier A and Tier A+B get the same labels.

## Differences from the proposal

| Proposal | Now | Decided by |
|---|---|---|
| 20, 30, 50 robots; 100 optional | W100, 10-100; 30, 70, 100 in M3 | DECISIONS "Warehouse Scalability" |
| Task priority, release time, deadline | Not in the order schema | CLAUDE.md, frozen |
| Five scenarios, including robot failure | Uniform demand only; spatial variant open (F12) | Open: needed before any robustness claim |
| `reject(R,T)` target | `assign` only; a deferral is expressed by no rule firing | M1 record, item 5 |
| Friedman, then Wilcoxon, then Holm | Paired t primary, Wilcoxon sensitivity, Holm | EXPERIMENT_PROTOCOL, proposed, **not frozen** |
| `Q(a)` weighted outcome score | `J = pivot_mission_s + 0.5 * waiting_in_h_s` over 120 s | M1 record, item 2 |
| Teacher decisions compared across comparable states | Counterfactual rollouts from the same state | M1 record, item 2 |

The statistics row needs the researcher's decision before M8: the proposal names Friedman as primary, and the protocol does not.
