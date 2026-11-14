"""Only fields the original runners support; no browser-supplied paths."""

from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Count = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
RobotCount = Annotated[int, Field(strict=True, ge=1, le=1000)]
Seed = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
Window = Union[
    Literal["fleet_size", "legacy"], Annotated[int, Field(strict=True, ge=1, le=100000)]
]
Purpose = Literal[
    "training",
    "validation",
    "evaluation",
    "backup",
    "final",
    "induction",
    "exploratory",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class RunSpec(StrictModel):
    kind: Literal["run"] = "run"
    scenario_id: Literal["W100"] = "W100"
    strategy: str = Field("nearest_astar", min_length=1, max_length=100)
    robots: Count = 0
    orders: Count = 0
    seed: Seed | None = None
    window: Window | None = None
    quantum_s: float | None = Field(None, ge=0, le=3600)
    speed_mps: float | None = Field(None, gt=0, le=100)
    progress_interval_s: float = Field(10.0, ge=0, le=3600)
    quiet: bool = True
    export: bool = False
    purpose: Purpose | None = None
    features: bool = False
    teacher_panel: bool = False
    rule_artifact: str | None = Field(
        None,
        min_length=1,
        max_length=240,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_./-]*\.json$",
    )
    rule_matching: Literal["iterative", "cardinality", "greedy"] | None = None
    input_id: str | None = Field(None, pattern=r"^[a-f0-9]{64}$")
    execution_mode: Literal["cli", "observed"] = "cli"
    observation_sample_s: float | None = Field(None, ge=0.001, le=3600)

    @model_validator(mode="after")
    def observation_mode(self):
        if self.execution_mode == "cli" and self.observation_sample_s is not None:
            raise ValueError("observation_sample_s requires observed execution mode")
        if self.execution_mode == "observed" and (self.features or self.teacher_panel):
            raise ValueError(
                "features and teacher_panel require original CLI execution mode"
            )
        if self.strategy == "ilp" and self.rule_artifact is None:
            raise ValueError("the ilp strategy requires a registered rule_artifact")
        if self.strategy != "ilp" and (
            self.rule_artifact is not None or self.rule_matching is not None
        ):
            raise ValueError(
                "rule_artifact and rule_matching are only valid for the ilp strategy"
            )
        if self.execution_mode == "observed" and self.strategy == "ilp":
            raise ValueError("the ilp strategy requires original CLI execution mode")
        return self


class ExperimentSpec(StrictModel):
    kind: Literal["experiment"] = "experiment"
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    strategies: list[str] = Field(
        default_factory=lambda: ["nearest_astar"], min_length=1, max_length=32
    )
    robots: list[RobotCount] = Field(
        default_factory=lambda: [10], min_length=1, max_length=100
    )
    seeds: list[Seed] = Field(default_factory=lambda: [1], min_length=1, max_length=100)
    orders: Count = 0
    jobs: int = Field(1, strict=True, ge=1, le=4)
    workloads: Literal["seeded", "reference"] = "seeded"
    workload_orders: Annotated[int, Field(strict=True, ge=1, le=1_000_000)] = 2000
    export: bool = False
    quantum_s: float | None = Field(None, ge=0, le=3600)
    window: Window | None = None
    purpose: Purpose | None = None
    features: bool = False
    teacher_panel: bool = False
    mode: Literal["new", "resume", "force"] = "new"

    @field_validator("strategies", "robots", "seeds")
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Duplicate values are not allowed")
        return value

    @field_validator("strategies")
    @classmethod
    def supported_matrix_strategies(cls, value):
        if "ilp" in value:
            raise ValueError(
                "the ilp strategy requires a rule_artifact and is only supported in single-run CLI mode"
            )
        return value


class GazeboSpec(StrictModel):
    kind: Literal["gazebo"] = "gazebo"
    scenario_id: Literal["W100"] = "W100"
    strategy: str = Field("nearest_astar", min_length=1, max_length=100)
    robots: RobotCount = 10
    orders: Count = 50
    window: Window | None = None
    quantum_s: float | None = Field(None, ge=0, le=3600)
    speed_mps: float | None = Field(None, gt=0, le=100)
    progress_interval_s: float = Field(10.0, ge=0, le=3600)
    headless: bool = False
    verbose: bool = False


Spec = Annotated[
    Union[RunSpec, ExperimentSpec, GazeboSpec], Field(discriminator="kind")
]


class LaunchRequest(StrictModel):
    preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")
