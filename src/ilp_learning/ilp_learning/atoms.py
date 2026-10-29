"""Run- and decision-scoped symbolic atom encoders and bijective provenance.

Enforces:
1. Valid Prolog / Datalog atom syntax: ^[a-z][a-z0-9_]*$
2. Run and decision scoping: prevents cross-run and cross-decision identity leakage.
3. Order destruction: eliminates artificial lexical biases (e.g., R00 < R01).
4. Bijective traceability: full round-trip mapping via Parquet provenance tables.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import hmac
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class DecisionProvenance:
    atom: str
    run_id: str
    decision_id: int
    tick: int | float = 0


@dataclass(frozen=True)
class RobotProvenance:
    atom: str
    run_id: str
    decision_id: int
    robot_id: str


@dataclass(frozen=True)
class OrderProvenance:
    atom: str
    run_id: str
    decision_id: int
    order_id: str


def _hash_token(key: bytes, message: str, prefix: str, length: int = 16) -> str:
    digest = hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{prefix}_{digest[:length]}"


def encode_decision_id(run_id: str, decision_id: int) -> str:
    """Encode a run-scoped decision atom, e.g. 'd_a1b2c3d4e5f60718'."""
    msg = f"{run_id}:{decision_id}"
    return _hash_token(b"mrta_decision", msg, "d")


def encode_robot_id(run_id: str, decision_id: int, robot_id: str) -> str:
    """Encode a decision-scoped robot atom, e.g. 'r_a1b2c3d4e5f60718'."""
    msg = f"{run_id}:{decision_id}:{robot_id}"
    return _hash_token(b"mrta_robot", msg, "r")


def encode_order_id(run_id: str, decision_id: int, order_id: str) -> str:
    """Encode a decision-scoped order atom, e.g. 'o_a1b2c3d4e5f60718'."""
    msg = f"{run_id}:{decision_id}:{order_id}"
    return _hash_token(b"mrta_order", msg, "o")


class ProvenanceTracker:
    """Maintains bijective provenance between original simulation IDs and Prolog atoms."""

    def __init__(self) -> None:
        self.decisions: dict[str, DecisionProvenance] = {}
        self.robots: dict[str, RobotProvenance] = {}
        self.orders: dict[str, OrderProvenance] = {}

    def get_or_create_decision(self, run_id: str, decision_id: int, tick: int | float = 0) -> str:
        atom = encode_decision_id(run_id, decision_id)
        if atom not in self.decisions:
            self.decisions[atom] = DecisionProvenance(atom, run_id, decision_id, tick)
        return atom

    def get_or_create_robot(self, run_id: str, decision_id: int, robot_id: str) -> str:
        atom = encode_robot_id(run_id, decision_id, robot_id)
        if atom not in self.robots:
            self.robots[atom] = RobotProvenance(atom, run_id, decision_id, robot_id)
        return atom

    def get_or_create_order(self, run_id: str, decision_id: int, order_id: str) -> str:
        atom = encode_order_id(run_id, decision_id, order_id)
        if atom not in self.orders:
            self.orders[atom] = OrderProvenance(atom, run_id, decision_id, order_id)
        return atom

    def to_dataframes(self) -> dict[str, pd.DataFrame]:
        return {
            "decisions": pd.DataFrame([asdict(p) for p in self.decisions.values()]),
            "robots": pd.DataFrame([asdict(p) for p in self.robots.values()]),
            "orders": pd.DataFrame([asdict(p) for p in self.orders.values()]),
        }

    def save_parquet(self, directory: Path | str) -> None:
        out_dir = Path(directory)
        out_dir.mkdir(parents=True, exist_ok=True)
        dfs = self.to_dataframes()
        for name, df in dfs.items():
            df.to_parquet(out_dir / f"provenance_{name}.parquet", index=False)

    @classmethod
    def load_parquet(cls, directory: Path | str) -> ProvenanceTracker:
        in_dir = Path(directory)
        tracker = cls()

        dec_path = in_dir / "provenance_decisions.parquet"
        if dec_path.exists():
            df = pd.read_parquet(dec_path)
            for _, r in df.iterrows():
                tracker.decisions[r["atom"]] = DecisionProvenance(
                    r["atom"], r["run_id"], int(r["decision_id"]), r.get("tick", 0)
                )

        rob_path = in_dir / "provenance_robots.parquet"
        if rob_path.exists():
            df = pd.read_parquet(rob_path)
            for _, r in df.iterrows():
                tracker.robots[r["atom"]] = RobotProvenance(
                    r["atom"], r["run_id"], int(r["decision_id"]), str(r["robot_id"])
                )

        ord_path = in_dir / "provenance_orders.parquet"
        if ord_path.exists():
            df = pd.read_parquet(ord_path)
            for _, r in df.iterrows():
                tracker.orders[r["atom"]] = OrderProvenance(
                    r["atom"], r["run_id"], int(r["decision_id"]), str(r["order_id"])
                )

        return tracker
