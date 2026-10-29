"""M1: deterministic counterfactual rollouts from cloned simulator states.

A feasibility probe, not a labelling pipeline. The question it answers:
can we observe what *would* have happened had a decision gone another
way -- without imputing anything -- by cloning the simulator just before
the decision, forcing one alternative, and letting the same strategy
carry on?

Clone point. The executor's external tick callback is the last thing
FleetSimulationEngine.step_once does, so at that callback the simulator
sits exactly between two event batches. A deep copy taken there is
resumed by running the copied engine and, if the clock runs dry with
work left, the executor's own recovery loop -- the same sequence
WorkloadExecutor.run performs. resume() is checked against the
uninterrupted run (verify): an untouched clone must finish with the
same decision and outcome tables.

Counterfactual. ForcedDecision wraps the clone's strategy and, at one
decision only, replaces one pair of the strategy's answer -- the pair of
the oldest order -- with another eligible order for the same robot,
ranked by the decision-time finish estimate. Every other pair, every
later decision, routing, traffic, battery and execution are unchanged.
The alternative is a legal decision: eligible, from the window, one
order per robot; the executor's own checks still apply to it.

What a rollout measures, from t_k (the decision time) on:

    window_flow_s     mean completed_at - t_k over the decision's window
                      orders (every one of them completes before the
                      rollout stops)
    completed_in_h    orders completed in (t_k, t_k + horizon]
    waiting_in_h_s    fleet waiting booked between the decision and the
                      first tick at or after t_k + horizon: the same
                      interval for every branch of a decision. Booked:
                      a wait still in progress at that tick is not yet
                      counted.
    pivot_mission_s   the forced pair's own mission time
    order_makespan_s  only when the rollout runs to the end (no horizon)
"""

import copy
import time

from simulation_env.workload_executor import (
    PerfBudget,
    WorkloadStalledError,
)
from task_management.allocation_strategy import OrderAssignment


class _Stop(Exception):
    """The rollout reached its horizon."""


class ForcedDecision:
    """The wrapped strategy, except at one decision.

    ``alternative`` None keeps the strategy's own answer (the teacher
    branch); an integer i replaces the pivot pair with the i-th ranked
    alternative order for the pivot robot.
    """

    def __init__(self, inner, executor, decision_id, alternative=None):
        self.inner = inner
        self.executor = executor
        self.decision_id = decision_id
        self.alternative = alternative
        self.decided = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def assign(self, warehouse, robots, orders, fifo_position, cache=None, battery=None, timing=None):
        chosen = self.inner.assign(
            warehouse, robots, orders, fifo_position, cache, battery=battery, timing=timing
        )

        if self.executor.result.decisions - 1 != self.decision_id or not chosen:
            return chosen

        pivot = chosen[0]
        decision = {
            "t": float(self.executor.engine.time),
            # At the decision, not at the clone point: waiting booked in
            # between belongs to the shared past, not to this branch.
            "waiting_at_decision": _fleet_waiting(self.executor),
            "window": tuple(order.order_id for order in orders),
            "pivot_robot": pivot.robot_id,
            "teacher_order": pivot.order_id,
            "pairs": len(chosen),
        }

        taken = {a.order_id for a in chosen[1:]}
        options = sorted(
            (
                c
                for c in self.inner.score(
                    warehouse, robots, orders, fifo_position, cache, battery=battery
                )
                if c.robot_id == pivot.robot_id
                and c.eligible
                and c.order_id != pivot.order_id
                and c.order_id not in taken
            ),
            key=lambda c: (timing.finish_s(c), c.fifo_position),
        )

        decision["options"] = len(options)
        decision["teacher_est_s"] = next(
            (
                timing.finish_s(c)
                for c in self.inner.score(warehouse, robots, orders, fifo_position, cache)
                if c.robot_id == pivot.robot_id and c.order_id == pivot.order_id
            ),
            float("nan"),
        )

        if self.alternative is None or self.alternative >= len(options):
            # The strategy's own answer: the teacher branch, or an
            # alternative rank this robot did not have.
            decision["order"] = pivot.order_id
            decision["est_s"] = decision["teacher_est_s"]
            decision["replaced"] = False
            self.decided = decision
            return chosen

        option = options[self.alternative]
        decision["order"] = option.order_id
        decision["est_s"] = timing.finish_s(option)
        decision["replaced"] = True
        self.decided = decision

        replaced = OrderAssignment(
            robot_id=option.robot_id,
            order_id=option.order_id,
            cost=option.cost,
            route=option.route,
            energy=option.energy,
        )

        return sorted([replaced, *chosen[1:]], key=lambda a: fifo_position[a.order_id])


def clone(executor, rows=True):
    """A deep copy of the executor that can be resumed on its own.

    Shared, not copied: the warehouse (never written during a run) and
    the route cache (a pure memo: same inputs, same route). With
    ``rows`` False the copy starts with empty decision and outcome
    lists; a rollout needs only what happens after the clone point, and
    the copy is then far cheaper.
    """

    memo = {
        id(executor.warehouse): executor.warehouse,
        id(executor.routes): executor.routes,
    }

    if not rows:
        memo[id(executor.recorder.decisions)] = []
        memo[id(executor.recorder.outcomes)] = []

    return copy.deepcopy(executor, memo)


def resume(executor, stop=None) -> None:
    """Continue a cloned executor exactly as WorkloadExecutor.run would.

    ``stop(executor)`` is checked after every tick; when it returns true
    the rollout ends there.
    """

    def tick(sim_time, batch):
        if stop is not None and stop(executor):
            raise _Stop

    executor._external_tick = tick

    try:
        executor.engine.run()

        while not executor.finished():
            executor._perf = PerfBudget(executor.engine.sim_time)

            try:
                recovered = executor._recover()
            finally:
                executor._close_perf()

            if not recovered:
                raise WorkloadStalledError("rollout stalled")

            executor.dispatch()
            executor.engine.run()

    except _Stop:
        pass


def _fleet_waiting(executor) -> float:
    return sum(robot.waiting_time_s for robot in executor.engine.robots.values())


def measure(executor, forced, horizon_s) -> dict:
    """What happened in one rollout after the forced decision."""

    decided = forced.decided
    outcomes = {row["order_id"]: row for row in executor.recorder.outcomes}
    t = decided["t"]

    window = [outcomes[o]["completed_at"] - t for o in decided["window"] if o in outcomes]
    pivot = outcomes.get(decided["order"])

    result = {
        "window_complete": len(window) == len(decided["window"]),
        "window_flow_s": sum(window) / len(window) if window else float("nan"),
        "completed_in_h": sum(
            1 for row in outcomes.values() if t < row["completed_at"] <= t + horizon_s
        ),
        "waiting_in_h_s": decided.get("waiting_at_horizon", float("nan"))
        - decided["waiting_at_decision"],
        "pivot_mission_s": (
            pivot["retreat_finished_at"] - pivot["launched_at"] if pivot else float("nan")
        ),
        "stopped_at_s": float(executor.engine.time),
    }

    if executor.finished() and outcomes:
        result["order_makespan_s"] = max(row["completed_at"] for row in outcomes.values())

    return result


def horizon_stop(forced, horizon_s):
    """Stop once every window order is done and the horizon has passed."""

    def stop(executor):
        decided = forced.decided

        if decided is None or float(executor.engine.time) < decided["t"] + horizon_s:
            return False

        # The common horizon: the first tick at or after t + H.
        decided.setdefault("waiting_at_horizon", _fleet_waiting(executor))

        done = {row["order_id"] for row in executor.recorder.outcomes}
        return all(order_id in done for order_id in decided["window"])

    return stop


def branch(base, decision_id, alternative, horizon_s):
    """One rollout from a clone; returns (measures, decision) or None."""

    started = time.perf_counter()
    rollout = clone(base, rows=False)
    copy_s = time.perf_counter() - started

    forced = ForcedDecision(rollout.allocator, rollout, decision_id, alternative)
    rollout.allocator = forced

    resume(rollout, None if horizon_s is None else horizon_stop(forced, horizon_s))

    if forced.decided is None:
        return None

    measures = measure(rollout, forced, horizon_s or float("inf"))
    measures["copy_s"] = copy_s
    measures["wall_s"] = time.perf_counter() - started

    return measures, forced.decided


def decision_ticks(build):
    """Pass 1: run once, and say in which tick each decision was made.

    Returns (executor after the run, {decision_id: tick}). Decision 0 is
    made before the first tick and cannot be cloned before; it is left
    out.
    """

    executor = build()
    ticks = {"n": 0}
    made_in = {}

    def tick(sim_time, batch):
        ticks["n"] += 1
        for decision_id in range(len(made_in) + 1, executor.result.decisions):
            made_in[decision_id] = ticks["n"]

    executor._external_tick = tick
    executor.run()

    return executor, made_in


def verified(checks) -> bool:
    """Rollout evidence stands only on at least one exact clone check.

    No check is not a pass: with nothing verified, nothing is known
    about whether a clone replays the run.
    """

    return bool(checks) and all(check["identical"] for check in checks)


def tables(executor):
    """The recorded rows, for exact comparisons."""

    return executor.recorder.decisions, executor.recorder.outcomes


def probe(build, decision_ids, made_in, *, alternatives=3, horizon_s=120.0, verify=2, baseline=None):
    """Pass 2: clone before each chosen decision and branch.

    ``verify`` clone points are first resumed untouched to the end and
    compared with ``baseline`` (pass 1's tables): the determinism check
    every rollout result depends on.
    """

    wanted = {}
    for decision_id in decision_ids:
        if decision_id in made_in and made_in[decision_id] > 1:
            wanted.setdefault(made_in[decision_id] - 1, []).append(decision_id)

    executor = build()
    ticks = {"n": 0}
    rows, checks = [], []

    def tick(sim_time, batch):
        ticks["n"] += 1

        if ticks["n"] not in wanted:
            return

        if baseline is not None and len(checks) < verify:
            whole = clone(executor)
            resume(whole)
            checks.append(
                {
                    "tick": ticks["n"],
                    "identical": tables(whole) == baseline,
                    "decisions": len(whole.recorder.decisions),
                    "outcomes": len(whole.recorder.outcomes),
                }
            )

        started = time.perf_counter()
        base = clone(executor, rows=False)
        base_copy_s = time.perf_counter() - started

        for decision_id in wanted[ticks["n"]]:
            for alternative in (None, *range(alternatives)):
                result = branch(base, decision_id, alternative, horizon_s)

                if result is None:
                    continue

                measures, decided = result

                if alternative is not None and not decided["replaced"]:
                    # Fewer alternatives than asked for: this branch is
                    # the teacher's again, not a counterfactual.
                    continue

                rows.append(
                    {
                        "decision_id": decision_id,
                        "branch": "teacher" if alternative is None else f"alt{alternative + 1}",
                        "t": decided["t"],
                        "pairs": decided["pairs"],
                        "options": decided["options"],
                        "pivot_robot": decided["pivot_robot"],
                        "teacher_order": decided["teacher_order"],
                        "order": decided["order"],
                        "teacher_est_s": decided["teacher_est_s"],
                        "est_s": decided["est_s"],
                        "base_copy_s": base_copy_s,
                        **measures,
                    }
                )

    executor._external_tick = tick
    executor.run()

    return rows, checks
