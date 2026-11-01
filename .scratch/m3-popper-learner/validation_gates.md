# M3 gates, commit 393e011b9080

Primary stop rule (quantum 3): not triggered. {'ask_about_arm_c': [], 'g7_failures': [], 'g11_failed': False, 'stop': False}

| Condition | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G9 |
|---|---|---|---|---|---|---|---|---|
| q3/30 | pass | n/a | pass | pass | FAIL | pass | pass | pass |
| q3/70 | pass | pass | pass | pass | pass | pass | pass | pass |
| q3/100 | pass | pass | pass | pass | pass | pass | pass | pass |
| q0/30 | pass | n/a | pass | pass | FAIL | pass | pass | pass |
| q0/70 | FAIL | FAIL | pass | pass | pass | FAIL | pass | FAIL |
| q0/100 | FAIL | pass | pass | pass | pass | FAIL | pass | FAIL |

| Condition | Metric | Mean log ratio | 90% CI | Margin | Verdict |
|---|---|---:|---|---:|---|
| q3/30 | order_makespan_s | -0.0011 | [-0.0110, +0.0089] | 0.0155 | equivalent |
| q3/30 | mean_completion_time_s | -0.0018 | [-0.0058, +0.0022] | 0.0179 | equivalent |
| q3/70 | order_makespan_s | +0.0001 | [-0.0066, +0.0068] | 0.0211 | equivalent |
| q3/70 | mean_completion_time_s | +0.0001 | [-0.0010, +0.0011] | 0.0261 | equivalent |
| q3/100 | order_makespan_s | +0.0012 | [-0.0284, +0.0309] | 0.0500 | equivalent |
| q3/100 | mean_completion_time_s | +0.0003 | [-0.0045, +0.0051] | 0.0500 | equivalent |
| q0/30 | order_makespan_s | +0.0001 | [-0.0001, +0.0002] | 0.0132 | equivalent |
| q0/30 | mean_completion_time_s | -0.0000 | [-0.0000, +0.0000] | 0.0166 | equivalent |
| q0/70 | order_makespan_s | -0.0078 | [-0.0294, +0.0137] | 0.0267 | inconclusive |
| q0/70 | mean_completion_time_s | -0.0019 | [-0.0042, +0.0005] | 0.0144 | equivalent |
| q0/100 | order_makespan_s | -0.0081 | [-0.0582, +0.0420] | 0.0216 | inconclusive |
| q0/100 | mean_completion_time_s | -0.0093 | [-0.0308, +0.0122] | 0.0212 | inconclusive |

| Condition | Frozen key | Tie-neutral | Distinct-cost | Exact | G5 drop | Limit | Original G5 | Random-key | Teacher-key | Tie share | Unexplained |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| q3/30 | 0.9907 | 0.9953 | 1.0000 | 0.9926 | +0.0079 | 0.0036 | FAIL | 0.9382 | 1.0000 | 0.0047 | 0.4493 |
| q3/70 | 0.9940 | 0.9987 | 1.0000 | 0.9860 | +0.0031 | 0.0083 | pass | 0.8968 | 1.0000 | 0.0047 | 0.6041 |
| q3/100 | 0.9833 | 0.9900 | 1.0000 | 0.9903 | -0.0005 | 0.0814 | pass | 0.8477 | 1.0000 | 0.0067 | 0.6133 |
| q0/30 | 0.9946 | 0.9967 | 1.0000 | 0.9992 | +0.0054 | 0.0007 | FAIL | 0.9474 | 1.0000 | 0.0020 | 0.1271 |
| q0/70 | 0.9585 | 0.9933 | 0.9786 | 0.9547 | +0.0038 | 0.0382 | pass | 0.8727 | 0.9605 | 0.0348 | 0.3003 |
| q0/100 | 0.9044 | 0.9679 | 0.9940 | 0.9408 | +0.0417 | 0.0933 | pass | 0.8564 | 0.9459 | 0.0635 | 0.4238 |

| Condition | Relabel differs | Permuted | Removing O drop | Removing O |
|---|---|---:|---:|---|
| q3/30 | True | 0.0453 (0 clauses) | 0.0415 | FAIL |
| q3/70 | True | 0.0472 (0 clauses) | 0.8885 | pass |
| q3/100 | True | 0.0381 (0 clauses) | 0.8852 | pass |
| q0/30 | True | 0.0339 (0 clauses) | 0.0430 | FAIL |
| q0/70 | True | 0.0368 (0 clauses) | 0.1169 | FAIL |
| q0/100 | True | 0.0301 (0 clauses) | 0.1775 | FAIL |

G11: pass. {'commit': '393e011b9080825baaf79b6819d4a57b5891571b', 'clean': True, 'tagged': True, 'bk_identical_across_teachers': True, 'rule_hash_in_identity': True, 'allowlist_test': True, 'pass': True}

Battery probe: {'config': '/home/ibrahim/rnd_mrta_ilp_research/runs/m3_evidence_1/battery/warehouse_soc20.yaml', 'initial_soc_percent': 20.0, 'nearest_astar': {'orders_completed': 300, 'reservation_violations': 0, 'charge_sessions': 30, 'ineligible_rows': 4669, 'ineligible_assignments': 0, 'double_assigned_orders': 0, 'double_assigned_robots': 0, 'min_soc_percent': 7.178003703703704, 'informative': True, 'pass': True}, 'learned': {'orders_completed': 300, 'reservation_violations': 0, 'charge_sessions': 30, 'ineligible_rows': 4669, 'ineligible_assignments': 0, 'double_assigned_orders': 0, 'double_assigned_robots': 0, 'min_soc_percent': 7.178003703703704, 'rule_coverage': 1.0, 'fallback': 0.0, 'teacher_agreement': 0.98, 'informative': True, 'pass': True}, 'informative': True, 'pass': True}

G12: {'q3/30': {'corpus_seed': 20261029, 'decision_cap': 800, 'decisions': 800, 'k_neg': 3, 'negatives': 5317, 'positives': 1776, 'timed_out': False}, 'q3/70': {'corpus_seed': 20261029, 'decision_cap': 800, 'decisions': 492, 'k_neg': 3, 'negatives': 6272, 'positives': 2098, 'timed_out': False}, 'q3/100': {'corpus_seed': 20261029, 'decision_cap': 800, 'decisions': 401, 'k_neg': 3, 'negatives': 6287, 'positives': 2099, 'timed_out': False}, 'q0/30': {'corpus_seed': 20261029, 'decision_cap': 800, 'decisions': 800, 'k_neg': 3, 'negatives': 2873, 'positives': 959, 'timed_out': False}, 'q0/70': {'corpus_seed': 20261029, 'decision_cap': 800, 'decisions': 800, 'k_neg': 3, 'negatives': 3427, 'positives': 1149, 'timed_out': False}, 'q0/100': {'corpus_seed': 20261029, 'decision_cap': 800, 'decisions': 800, 'k_neg': 3, 'negatives': 5152, 'positives': 1721, 'timed_out': False}}
