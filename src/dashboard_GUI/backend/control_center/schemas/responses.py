from typing import Any
from pydantic import BaseModel, Field


class Envelope(BaseModel):
    api_schema_version: int = 1
    data: Any = None
    availability: str = "available"
    warnings: list[str] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    next_cursor: str | None = None


def result(data=None, **kwargs):
    return Envelope(data=data, **kwargs)
