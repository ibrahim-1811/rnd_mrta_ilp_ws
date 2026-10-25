"""Run-level metrics: the numbers an experiment compares, one file per run.

docs/EXPERIMENT_PROTOCOL.md, "Core Metrics", lists what a run is judged
on: MRTA, traffic and battery metrics. The final report prints them;
this module gathers the same numbers into one dict so a run directory
carries them as ``metrics.json`` beside its decision and outcome tables,
and an experiment can be plotted without reading terminal output.

Read only. Every value is taken from the finished executor, its engine,
its battery ledger and its charge records; nothing here feeds back into
a run. Realized outcomes only: decision-time features stay in the
decision table (docs/DECISIONS.md, "Decision-Time Features").
"""

from json import dumps
from pathlib import Path
from statistics import mean, median

from simulation_env.battery_ledger import fleet_summary


METRICS_FILE = "metrics.json"


def _trips(executor) -> list:
    """Every charging-workflow record of the run, once."""

    return list(
        {
            id(record): record
            for record in (*executor.charge_history, *executor.charge.values())
        }.values()
    )


def run_metrics(executor, *, strategy: str, seed: int, wall_s: float) -> dict:
    """The protocol's run-level metrics for a finished run."""

    result = executor.result
    totals = executor.totals()
    makespan = float(totals["makespan_s"])
    completions = [c.sim_time for c in result.completions]
    completed = len(completions)

    metrics = {
        "strategy": strategy,
        "seed": seed,
        "robots": len(executor.engine.robots),
        "orders": len(executor.queue),
        # -- MRTA ----------------------------------------------------
        "orders_completed": completed,
        "makespan_s": makespan,
        "throughput_orders_per_h": completed / makespan * 3600 if makespan else 0.0,
        # Every order is released at t=0, so its completion time is the
        # instant it was fulfilled.
        "mean_completion_time_s": mean(completions) if completions else None,
        "median_completion_time_s": median(completions) if completions else None,
        "total_distance_m": float(totals["distance_m"]),
        "distance_per_order_m": float(totals["distance_m"]) / completed if completed else None,
        "total_travel_time_s": float(totals["travel_time_s"]),
        "total_waiting_s": float(totals["waiting_time_s"]),
        "total_service_s": float(totals["service_time_s"]),
        "allocator_cpu_s": result.perf.get("allocator", 0.0),
        "mission_build_cpu_s": result.perf.get("mission_build", 0.0),
        "decisions": result.decisions,
        "choice_bearing_decisions": result.choice_bearing,
        "worst_dispatch_s": result.worst_dispatch_s,
        "wall_clock_s": wall_s,
        # -- traffic -------------------------------------------------
        "reservation_violations": totals["reservation_violations"],
        "waiting_per_order_s": float(totals["waiting_time_s"]) / completed if completed else None,
        "waiting_share": _share(totals),
        "yield_events": result.yield_events,
        "plan_deferrals": result.plan_failures,
        "peak_in_progress": result.peak_in_progress,
    }

    metrics.update(_battery_metrics(executor, makespan))

    return metrics


def _share(totals) -> float | None:
    """Waiting as a share of all time robots spent on missions."""

    busy = (
        float(totals["travel_time_s"])
        + float(totals["waiting_time_s"])
        + float(totals["service_time_s"])
    )

    return float(totals["waiting_time_s"]) / busy if busy else None


def _battery_metrics(executor, makespan: float) -> dict:
    ledger = executor.engine.battery

    if ledger is None:
        return {"battery_enabled": False}

    fleet = fleet_summary(ledger.snapshots(executor.engine.time))
    result = executor.result
    trips = _trips(executor)

    # Out of the fleet: from a charge request until the robot rejoined,
    # whichever trips it took in between. One request may span a
    # holding or Stage Zone trip and then a charger trip; both carry the
    # original request time, so a request is (robot, requested_at).
    # A holding or Stage Zone trip ends at promotion and never rejoins,
    # so only a request with no rejoin at all is counted to the end of
    # the run.
    rejoined = {}

    for record in trips:
        key = (record.robot_id, record.requested_at)
        rejoined.setdefault(key, None)

        if record.rejoined_at is not None:
            rejoined[key] = float(record.rejoined_at)

    out_of_fleet = {
        key: (makespan if end is None else end) - float(key[1])
        for key, end in rejoined.items()
    }

    # Waiting for a charger: on a holding cell or a Stage Zone, from
    # arrival until promotion (or the end of the run).
    waiting = [
        (float(r.promoted_at) if r.promoted_at is not None else makespan)
        - float(r.arrived_at)
        for r in trips
        if r.kind in ("holding", "stage") and r.arrived_at is not None
    ]

    return {
        "battery_enabled": True,
        "energy_consumed_percent": float(fleet["total_energy_consumed"]),
        "energy_per_order_percent": (
            float(fleet["total_energy_consumed"]) / len(result.completions)
            if result.completions
            else None
        ),
        "min_soc_percent": float(fleet["lowest_minimum_soc"]),
        "mean_final_soc_percent": float(fleet["mean_final_soc"]),
        "below_zero_robots": len(fleet["below_zero"]),
        "charge_requests": len(out_of_fleet),
        "charge_sessions": result.charge_sessions,
        "charging_time_s": float(fleet["total_charging_s"]),
        "charge_energy_percent": float(fleet["total_charge"]),
        "out_of_fleet_time_s": sum(out_of_fleet.values()),
        "charger_wait_time_s": sum(waiting),
        "saturation_events": result.saturation_events,
        "promotions": result.promotions,
        "stage_promotions": result.stage_promotions,
        "preemptions": result.preemptions,
    }


def write_metrics(run_dir, metrics: dict) -> Path:
    path = Path(run_dir) / METRICS_FILE
    path.write_text(dumps(metrics, indent=2, default=float))
    return path
