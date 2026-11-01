"""Resolve a scenario id to its config file, from the dashboard registry.

The registry (src/dashboard_GUI/config/scenarios.yaml) is the one list of
scenarios. A launch file given ``scenario:=<id>`` reads only the ``config``
path of the entry with that id, then everything else (map, orders, world,
fleet) follows from that config as for any config. Nothing here knows a
particular map: a new map needs a registry entry and its generated bundle
(scripts/build_scenario_bundle.py).

Visualization plumbing only: it picks which config the launch hands to the
same scientific engine. It plans and allocates nothing.
"""

import re
from pathlib import Path

import yaml


SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")

REGISTRY_RELATIVE = Path("src/dashboard_GUI/config/scenarios.yaml")


def find_registry(source_package) -> Path:
    """The registry beside the source tree (src/simulation_env), if present."""

    if source_package is None:
        raise FileNotFoundError(
            "scenario:= needs the source workspace (the package was not built "
            "from a tree that holds src/dashboard_GUI/config/scenarios.yaml)"
        )

    registry = Path(source_package).resolve().parents[1] / REGISTRY_RELATIVE

    if not registry.is_file():
        raise FileNotFoundError(f"scenario registry not found: {registry}")

    return registry


def config_for_scenario(scenario_id: str, registry: Path) -> Path:
    """The config file registered for ``scenario_id``."""

    if SAFE_ID.fullmatch(scenario_id or "") is None:
        raise ValueError(f"invalid scenario id {scenario_id!r}")

    document = yaml.safe_load(Path(registry).read_text()) or {}

    for entry in document.get("scenarios") or []:
        if isinstance(entry, dict) and entry.get("id") == scenario_id:
            config = Path(str(entry.get("config", "")))

            if not config.is_absolute():
                config = Path(registry).parent / config

            config = config.resolve()

            if not config.is_file():
                raise FileNotFoundError(f"scenario {scenario_id!r}: no config at {config}")

            return config

    known = [e.get("id") for e in document.get("scenarios") or [] if isinstance(e, dict)]

    raise ValueError(f"unknown scenario {scenario_id!r}; registered: {known}")


def resolve_config(config_file: str, scenario: str, source_package, default: str) -> str:
    """Pick the config: explicit ``config_file``, else ``scenario``, else default."""

    if config_file and scenario:
        raise ValueError("give config_file or scenario, not both")

    if config_file:
        return config_file

    if scenario:
        return str(config_for_scenario(scenario, find_registry(source_package)))

    return default
