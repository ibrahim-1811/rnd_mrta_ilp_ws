from task_management.allocation_strategy import STRATEGIES, get_strategy
from task_management.features import Candidate


def C(rid, dist, ttime, proc, epct, bat, wl, cum, cong=0.0, feasible=True,
      reachable=True, prio=1):
    return Candidate(
        robot_id=rid, reachable=reachable, distance=dist, travel_time=ttime,
        round_trip=dist * 2, proc_time=proc, congestion=cong, energy_pct=epct,
        battery=bat, workload=wl, cumulative_load=cum, battery_feasible=feasible,
        priority=prio)


# r4 closest but battery-infeasible; r3 fastest processing + fullest battery;
# r2 close and cheap-per-task but heavily loaded (workload 5); r1 idle, high battery.
CANDS = [
    C(1, dist=5, ttime=5, proc=8, epct=3, bat=90, wl=0, cum=100),
    C(2, dist=2, ttime=3, proc=6, epct=2, bat=40, wl=5, cum=50),
    C(3, dist=8, ttime=7, proc=5, epct=4, bat=100, wl=1, cum=200),
    C(4, dist=1, ttime=2, proc=20, epct=25, bat=15, wl=0, cum=0, feasible=False),
]


def test_registry_has_all_twelve_plus_alias():
    expected = {'random', 'round_robin', 'nearest', 'least_busy', 'highest_battery',
                'spt', 'lpt', 'edf', 'heuristic', 'score', 'bid', 'auction'}
    assert expected <= set(STRATEGIES)
    assert 'greedy' in STRATEGIES  # alias for heuristic


def test_greedy_is_alias_of_heuristic():
    assert STRATEGIES['greedy'] is STRATEGIES['heuristic']


def test_nearest_picks_closest():
    assert get_strategy('nearest').select(CANDS) == 4


def test_least_busy_breaks_ties_by_distance():
    assert get_strategy('least_busy').select(CANDS) == 4


def test_highest_battery():
    assert get_strategy('highest_battery').select(CANDS) == 3


def test_spt_picks_fastest_processing():
    assert get_strategy('spt').select(CANDS) == 3


def test_lpt_picks_least_cumulative_load():
    assert get_strategy('lpt').select(CANDS) == 4


def test_heuristic_excludes_infeasible_then_least_busy():
    # feasible = r1,r2,r3; least workload is r1 (0)
    assert get_strategy('heuristic').select(CANDS) == 1
    assert get_strategy('greedy').select(CANDS) == 1


def test_edf_falls_back_to_nearest():
    assert get_strategy('edf').select(CANDS) == 4


def test_score_minimises_cost_and_balances_load():
    # workload term penalises busy r2 -> idle r1 wins
    assert get_strategy('score').select(CANDS) == 1


def test_bid_ignores_load_and_picks_cheap_busy_robot():
    # bid drops the workload term -> close, low-energy r2 wins despite being busy
    assert get_strategy('bid').select(CANDS) == 2


def test_auction_includes_load_and_differs_from_bid():
    assert get_strategy('auction').select(CANDS) == 1
    # the whole point of the physics cost model: load-aware auction != load-blind bid
    assert get_strategy('auction').select(CANDS) != get_strategy('bid').select(CANDS)


def test_round_robin_cycles():
    rr = get_strategy('round_robin')
    assert [rr.select(CANDS) for _ in range(5)] == [1, 2, 3, 4, 1]


def test_random_is_seeded_and_in_pool():
    a = [get_strategy('random', seed=42).select(CANDS) for _ in range(6)]
    b = [get_strategy('random', seed=42).select(CANDS) for _ in range(6)]
    # each fresh instance reseeds, so first picks match
    assert a[0] == b[0]
    assert all(p in {1, 2, 3, 4} for p in a)


def test_defer_when_no_reachable_robot():
    blocked = [C(1, 1, 1, 1, 1, 100, 0, 0, reachable=False)]
    for name in ('nearest', 'least_busy', 'spt', 'lpt', 'score', 'bid', 'auction',
                 'heuristic'):
        assert get_strategy(name).select(blocked) is None


def test_auction_defers_when_all_infeasible():
    infeasible = [C(1, 1, 1, 1, 90, 5, 0, 0, feasible=False)]
    assert get_strategy('auction').select(infeasible) is None
    assert get_strategy('nearest').select(infeasible) == 1  # nearest ignores feasibility
