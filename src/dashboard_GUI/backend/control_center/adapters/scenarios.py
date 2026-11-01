"""Read-only, production-validated warehouse scenario registry."""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import yaml
from openpyxl.utils.exceptions import InvalidFileException


REPO = Path(__file__).resolve().parents[5]
REGISTRY = REPO / "src/dashboard_GUI/config/scenarios.yaml"
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class ScenarioError(ValueError):
    """A declared scenario cannot be used."""


class UnsafeScenarioId(ScenarioError):
    """A caller supplied something other than a stable registry ID."""


class UnknownScenario(ScenarioError):
    """No registry declaration has the requested stable ID."""


class InvalidScenario(ScenarioError):
    """The declaration exists, but production validation rejected it."""


def _jsonable(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _problem(problem):
    cells = ", ".join(f"({x},{y})" for x, y in problem.cells)
    location = f" at {cells}" if cells else ""
    return f"{problem.kind}{location}: {problem.message}"


def _declarations():
    raw = yaml.safe_load(REGISTRY.read_text()) or {}
    if raw.get("version") != 1 or not isinstance(raw.get("scenarios"), list):
        raise ValueError(f"{REGISTRY}: expected version 1 with a scenarios list")
    return raw["scenarios"]


def _base_record(declaration, index):
    if not isinstance(declaration, dict):
        return {
            "id": f"invalid-entry-{index + 1}",
            "label": f"Invalid registry entry {index + 1}",
            "reference": False,
            "provenance": None,
            "width": None,
            "height": None,
            "capacity": None,
            "valid": False,
            "validation": {"errors": ["Registry entry must be a mapping"], "warnings": []},
            "hashes": {},
            "identity": None,
            "settings": {},
        }
    return {
        "id": declaration.get("id") if isinstance(declaration.get("id"), str) else f"invalid-entry-{index + 1}",
        "label": declaration.get("label") if isinstance(declaration.get("label"), str) else str(declaration.get("id", f"Entry {index + 1}")),
        "reference": declaration.get("reference") is True,
        "provenance": declaration.get("provenance") if isinstance(declaration.get("provenance"), str) else None,
        "width": None,
        "height": None,
        "capacity": None,
        "valid": False,
        "validation": {"errors": [], "warnings": []},
        "hashes": {},
        "identity": None,
        "settings": {},
    }


def _evaluate(declaration, index=0):
    record = _base_record(declaration, index)
    if not isinstance(declaration, dict):
        return record, None, None
    errors = record["validation"]["errors"]
    scenario_id = declaration.get("id")
    if not isinstance(scenario_id, str) or SAFE_ID.fullmatch(scenario_id) is None:
        errors.append("Scenario id must contain only letters, numbers, underscore, or hyphen")
    config_name = declaration.get("config")
    if not isinstance(config_name, str) or not config_name:
        errors.append("Scenario declaration requires a config path")
        return record, None, None
    config_path = Path(config_name)
    if not config_path.is_absolute():
        config_path = (REGISTRY.parent / config_path).resolve()
    config = warehouse = None
    try:
        from mrta_core.map_check import check_map
        from mrta_core.sim_config import load_sim_config
        from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx

        config = load_sim_config(config_path)
        warehouse = load_warehouse_xlsx(config.map_path, resolution=config.resolution_m)
        report = check_map(warehouse)
        record.update(width=report.width, height=report.height, capacity=report.max_robots())
        errors.extend(_problem(problem) for problem in report.errors)
        record["validation"]["warnings"].extend(_problem(problem) for problem in report.warnings)
        hashes = {
            "config": _digest(config_path),
            "map": _digest(config.map_path),
            "orders": _digest(config.orders_path),
        }
        record["hashes"] = hashes
        record["settings"] = _jsonable(asdict(config))
        identity_input = {
            "declaration": {
                "id": scenario_id,
                "label": record["label"],
                "config": config_name,
                "reference": record["reference"],
                "provenance": record["provenance"],
            },
            "hashes": hashes,
        }
        record["identity"] = hashlib.sha256(
            json.dumps(identity_input, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    except (ImportError, OSError, TypeError, ValueError, KeyError,
            yaml.YAMLError, BadZipFile, ParseError, InvalidFileException) as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    record["valid"] = not errors
    return record, config, warehouse


def scenarios():
    declarations = _declarations()
    records = [_evaluate(declaration, index)[0] for index, declaration in enumerate(declarations)]
    counts = {}
    for record in records:
        counts[record["id"]] = counts.get(record["id"], 0) + 1
    for record in records:
        if counts[record["id"]] > 1:
            record["validation"]["errors"].append("Scenario id is declared more than once")
            record["valid"] = False
    return records


def _find(scenario_id):
    if not isinstance(scenario_id, str) or SAFE_ID.fullmatch(scenario_id) is None:
        raise UnsafeScenarioId("Invalid scenario ID")
    matches = [item for item in _declarations() if isinstance(item, dict) and item.get("id") == scenario_id]
    if not matches:
        raise UnknownScenario(f"Unknown scenario {scenario_id!r}")
    if len(matches) != 1:
        raise InvalidScenario(f"Scenario {scenario_id!r} is declared more than once")
    return matches[0]


def scenario(scenario_id="W100"):
    record, _, _ = _evaluate(_find(scenario_id))
    if not record["valid"]:
        raise InvalidScenario("; ".join(record["validation"]["errors"]))
    return record


def load_scenario(scenario_id="W100"):
    record, config, warehouse = _evaluate(_find(scenario_id))
    if not record["valid"] or config is None or warehouse is None:
        raise InvalidScenario("; ".join(record["validation"]["errors"]))
    return config, warehouse


def geometry(scenario_id="W100"):
    from mrta_core.warehouse_routing import non_transit_cells, traversable_lane_edges

    _, warehouse = load_scenario(scenario_id)
    grid = warehouse.grid
    return {
        "id": scenario_id,
        "width": grid.width,
        "height": grid.height,
        "origin": grid.to_world((0, 0)),
        "resolution_m": grid.resolution,
        "coordinate_convention": "zero-based [x,y]; world = origin + cell * resolution; y increases upward",
        "obstacles": sorted(grid.obstacles),
        "traversable_cells": [
            (x, y)
            for y in range(grid.height)
            for x in range(grid.width)
            if warehouse.is_traversable((x, y))
        ],
        "zones": [
            {"id": zone.zone_id, "category": zone.zone_type.value, "cells": sorted(zone.cells)}
            for zone in warehouse.zones
        ],
        "non_transit_cells": sorted(non_transit_cells(warehouse)),
        "directed_edges": sorted(traversable_lane_edges(warehouse)),
        "lane_semantics": "Includes terminal entry/retreat; terminals are endpoints, never transit shortcuts",
    }
