"""Smooth Gazebo playback: frames drawn on their own thread, at a steady pace."""

from time import perf_counter, sleep

from simulation_env.playback import Player


def test_frames_and_lines_come_out_in_order():
    seen = []
    player = Player(lambda payload: seen.append(("frame", payload)), say=lambda t: seen.append(("say", t)), hz=50)
    player.frame(0.0, 1)
    player.say("status")
    player.frame(0.0, 2)
    player.finish()
    assert seen == [("frame", 1), ("say", "status"), ("frame", 2)]


def test_frames_are_paced_while_the_producer_runs_ahead():
    times = []
    player = Player(lambda payload: times.append(perf_counter()), hz=50, preroll_s=0.0)
    started = perf_counter()
    for i in range(10):
        player.frame(0.02, i)  # 10 frames x 20 ms
    queued = perf_counter() - started
    player.finish()
    assert queued < 0.05  # the producer never waits for drawing
    assert 0.17 <= times[-1] - started <= 0.5
    assert player.frames == 10 and player.stalls == 0


def test_a_slow_producer_is_a_stall_not_a_rush():
    times = []
    player = Player(lambda payload: times.append(perf_counter()), hz=50, preroll_s=0.0)
    player.frame(0.01, 0)
    sleep(0.4)  # a long decision with an empty buffer
    player.frame(0.01, 1)
    player.frame(0.01, 2)
    player.finish()
    assert player.stalls == 1
    assert times[2] - times[1] >= 0.008  # paced again, not burst out


def test_playback_waits_for_the_preroll_before_the_first_frame():
    times = []
    player = Player(lambda payload: times.append(perf_counter()), hz=50, preroll_s=0.05)
    started = perf_counter()
    player.frame(0.01, 0)
    sleep(0.2)  # the first decision: not yet enough motion buffered
    assert times == []
    for i in range(5):
        player.frame(0.01, i + 1)
    player.finish()
    assert len(times) == 6 and player.stalls == 0
    assert times[0] - started >= 0.2
