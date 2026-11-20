"""
Guardian Configuration Schema and YAML Loader.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import yaml


@dataclass(frozen=True)
class Guardrail:
    name: str
    metric_source: str  # e.g., 'prometheus', 'clickhouse', 'mock'
    comparison: str     # e.g., '>', '>=', '<', '<=', '==', '!='
    threshold: float
    query: Optional[str] = None
    description: str = ""

    def evaluate(self, current_value: float) -> bool:
        """
        Returns True if the guardrail is BREACHED (unhealthy), False if healthy.
        Example: error_rate > 5.0 -> True means breach.
        """
        ops = {
            ">": lambda val, thresh: val > thresh,
            ">=": lambda val, thresh: val >= thresh,
            "<": lambda val, thresh: val < thresh,
            "<=": lambda val, thresh: val <= thresh,
            "==": lambda val, thresh: abs(val - thresh) < 1e-6,
            "!=": lambda val, thresh: abs(val - thresh) >= 1e-6,
        }
        comparator = ops.get(self.comparison)
        if not comparator:
            raise ValueError(f"Unsupported comparison operator: {self.comparison}")
        return comparator(current_value, self.threshold)


@dataclass(frozen=True)
class RolloutConfig:
    flag_name: str
    stages: List[int] = field(default_factory=lambda: [1, 5, 25, 100])
    soak_time_seconds: int = 30
    require_approval_before_final: bool = True
    guardrails: List[Guardrail] = field(default_factory=list)
    project_id: int = 1
    environment_id: int = 1
    environment_key: str = ""

    def __post_init__(self):
        if not self.flag_name:
            raise ValueError("flag_name must not be empty")
        if not self.stages:
            raise ValueError("stages must contain at least one percentage stage")
        for s in self.stages:
            if not (0 <= s <= 100):
                raise ValueError(f"Stage percentage must be between 0 and 100, got: {s}")
        if sorted(self.stages) != self.stages:
            raise ValueError(f"Stages must be in ascending order: {self.stages}")
        if self.soak_time_seconds < 0:
            raise ValueError("soak_time_seconds must be non-negative")


def load_config_from_dict(data: Dict[str, Any]) -> RolloutConfig:
    raw_guardrails = data.get("guardrails", [])
    guardrails = [
        Guardrail(
            name=g["name"],
            metric_source=g.get("metric_source", "mock"),
            comparison=g.get("comparison", ">"),
            threshold=float(g["threshold"]),
            query=g.get("query"),
            description=g.get("description", ""),
        )
        for g in raw_guardrails
    ]

    return RolloutConfig(
        flag_name=data["flag_name"],
        stages=data.get("stages", [1, 5, 25, 100]),
        soak_time_seconds=int(data.get("soak_time_seconds", 30)),
        require_approval_before_final=bool(data.get("require_approval_before_final", True)),
        guardrails=guardrails,
        project_id=int(data.get("project_id", 1)),
        environment_id=int(data.get("environment_id", 1)),
        environment_key=data.get("environment_key", ""),
    )


def load_config(file_path: str | Path) -> RolloutConfig:
    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return load_config_from_dict(data)
