from math import pi

from mrta_core.kinematics import (
    RobotModel,
    _runs_and_turns,
    path_energy_percent,
    path_time,
)


def straight_path(n):
    return [(x, 0) for x in range(n + 1)]


def test_runs_and_turns_straight_line():
    runs, turns = _runs_and_turns(straight_path(5))
    assert runs == [5]
    assert turns == 0


def test_runs_and_turns_l_shape():
    # right 3 then up 2 -> one 90 degree turn
    path = [(0, 0), (1, 0), (2, 0), (3, 0), (3, 1), (3, 2)]
    runs, turns = _runs_and_turns(path)
    assert runs == [3, 2]
    assert turns == 1


def test_straight_time_trapezoid_matches_hand_calc():
    m = RobotModel(v_max=2.0, a_max=0.8)
    # L=16 m: d_acc = v^2/2a = 2.5 m, cruise = 16 - 5 = 11 m
    #  t = 2*(v/a) + cruise/v = 2*2.5 + 11/2 = 10.5 s
    assert abs(m.straight_time(16.0) - 10.5) < 1e-6


def test_straight_time_triangular_when_short():
    m = RobotModel(v_max=2.0, a_max=0.8)
    # L=1 m < 2*d_acc(=5) -> triangular: v_peak=sqrt(a L), t=2 v_peak/a
    assert m.straight_time(1.0) > 0
    assert m.straight_time(1.0) < m.straight_time(16.0)


def test_turn_adds_time():
    m = RobotModel()
    l_path = [(0, 0), (1, 0), (1, 1)]  # one turn
    straight = [(0, 0), (1, 0), (2, 0)]  # no turn, same length
    assert path_time(l_path, 1.0, m) > path_time(straight, 1.0, m)


def test_energy_percent_is_meaningful_but_bounded():
    m = RobotModel()
    # ~32 m round trip should drain a few percent (not ~0, not the whole battery)
    pct = path_energy_percent(straight_path(32), 1.0, m)
    assert 1.0 < pct < 30.0


def test_longer_path_costs_more_energy():
    m = RobotModel()
    assert path_energy_percent(straight_path(8), 1.0, m) < path_energy_percent(
        straight_path(24), 1.0, m
    )


def test_turn_time_default_is_quarter_turn():
    m = RobotModel(omega_max=2.84)
    assert abs(m.turn_time() - (pi / 2) / 2.84) < 1e-9
