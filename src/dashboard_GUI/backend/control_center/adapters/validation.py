"""Original manifest/loader semantics for job validation; no simulation or writes."""

from dataclasses import replace
from pathlib import Path

from fastapi import HTTPException

from control_center.adapters.catalogue import REPO
from control_center.schemas.jobs import GazeboSpec, RunSpec
from control_center.storage.files import digest, open_source, read_json
from control_center.storage.index import ArtifactReader


def validate_manifest(record, spec, config, warehouse, output, strategy, robots, seed):
    from mrta_core.orders import load_orders_csv, select_orders
    from simulation_env.dataset import build_manifest
    from task_management.allocation_strategy import get_batch_strategy

    if not isinstance(spec, (RunSpec, GazeboSpec)) and spec.workloads == "seeded":
        relative = f"workloads/orders_s{seed}.csv"
        with open_source(output, "workloads/workloads.json") as (handle, _):
            workload = read_json(handle).get(str(seed), {})
        with open_source(output, relative) as (handle, _):
            if digest(handle) != workload.get("sha256"):
                raise ValueError("orders_sha256: generated workload changed")
        config = replace(config, orders_path=Path(output) / relative)
    config = replace(config, robot_count=robots)
    single = isinstance(spec, (RunSpec, GazeboSpec))
    window = config.allocation_window if spec.window is None else spec.window
    width = replace(config, allocation_window=str(window)).window_width()
    available = load_orders_csv(config.orders_path, warehouse)
    if (
        not single
        and spec.workloads == "seeded"
        and len(available) != spec.workload_orders
    ):
        raise ValueError(
            "orders_available: generated workload count differs from request"
        )
    expected = build_manifest(
        config,
        strategy=strategy,
        strategy_identity=get_batch_strategy(strategy, seed=seed).identity(),
        window=window,
        window_width=width,
        seed=seed,
        speed_mps=config.speed_mps
        if not single or spec.speed_mps is None
        else spec.speed_mps,
        dispatch_quantum_s=config.dispatch_quantum_s
        if spec.quantum_s is None
        else spec.quantum_s,
        orders=select_orders(available, spec.orders),
        order_limit=spec.orders,
        orders_available=len(available),
        repo=REPO,
    )
    # Original experiment children record relative config/map paths; byte hashes
    # establish input equality across equivalent path spellings. The run_id also
    # incorporates those spellings, and is already checked against table rows.
    for key, value in expected.items():
        if (
            key not in ("run_id", "config_path", "map_path", "orders_path")
            and record["manifest"].get(key) != value
        ):
            raise ValueError(f"{key}: artifact does not match requested inputs")


def validate_resume(output, grid, spec, config, warehouse):
    reader = ArtifactReader(output)
    for strategy, robots, seed in grid:
        directory = f"{strategy}/r{robots}_s{seed}"
        path = Path(output) / directory
        # These are precisely the files whose presence causes the original
        # runner to skip a run. Partial attempts without both may be retried.
        if not (path / "run.json").exists() or not (path / "metrics.json").exists():
            continue
        try:
            record = reader.inspect(directory)
            if (
                record["integrity"] != "complete"
                or record["scientific_status"] != "completed"
            ):
                raise ValueError("Incomplete or failed original artifacts")
            validate_manifest(
                record, spec, config, warehouse, output, strategy, robots, seed
            )
        except (ValueError, ImportError, OSError, HTTPException) as exc:
            raise HTTPException(
                409,
                f"Existing run artifacts at {directory} cannot be safely skipped: {exc}. "
                "Use a new experiment name or explicit force; original evidence was retained.",
            ) from exc
