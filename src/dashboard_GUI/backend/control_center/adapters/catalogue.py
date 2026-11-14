"""Read production declarations; never construct an allocator or executor."""

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
CONFIG = REPO / "src/simulation_env/config/warehouse.yaml"


def strategies():
    from task_management.allocation_strategy import BATCH_STRATEGIES

    return [
        dict(
            id=name,
            implementation=cls.__name__,
            registered=True,
            review_status="See docs/CURRENT_PHASE.md; registration is not scientific approval",
        )
        for name, cls in sorted(BATCH_STRATEGIES.items())
    ]


def load_scenario():
    from mrta_core.sim_config import load_sim_config
    from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx

    config = load_sim_config(CONFIG)
    if config.map_path.stem != "W100":
        raise ValueError("Configured map is no longer the allowlisted W100 scenario")
    return config, load_warehouse_xlsx(config.map_path, resolution=config.resolution_m)


def scenarios():
    from simulation_env.fleet import home_bank

    config, warehouse = load_scenario()
    settings = {
        k: str(v) if isinstance(v, Path) else v for k, v in asdict(config).items()
    }
    hashes = {
        name: sha256(path.read_bytes()).hexdigest()
        for name, path in [
            ("config", CONFIG),
            ("map", config.map_path),
            ("orders", config.orders_path),
        ]
    }
    return [
        dict(
            id="W100",
            width=warehouse.grid.width,
            height=warehouse.grid.height,
            capacity=len(home_bank(warehouse)),
            hashes=hashes,
            settings=settings,
        )
    ]


def geometry():
    from mrta_core.warehouse_routing import non_transit_cells, traversable_lane_edges

    _, warehouse = load_scenario()
    grid = warehouse.grid
    return dict(
        id="W100",
        width=grid.width,
        height=grid.height,
        origin=grid.to_world((0, 0)),
        resolution_m=grid.resolution,
        coordinate_convention="zero-based [x,y]; world = origin + cell * resolution; y increases upward",
        obstacles=sorted(grid.obstacles),
        traversable_cells=[
            (x, y)
            for y in range(grid.height)
            for x in range(grid.width)
            if warehouse.is_traversable((x, y))
        ],
        zones=[
            dict(id=z.zone_id, category=z.zone_type.value, cells=sorted(z.cells))
            for z in warehouse.zones
        ],
        non_transit_cells=sorted(non_transit_cells(warehouse)),
        directed_edges=sorted(traversable_lane_edges(warehouse)),
        lane_semantics="Includes terminal entry/retreat; terminals are endpoints, never transit shortcuts",
    )
