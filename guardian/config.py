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
    metric_source: str  # e.g., 'prometheus', 'flagsmith_experiment', 'mock'
    comparison: str     # e.g., '>', '>=', '<', '<=', '==', '!='
    threshold: float
    query: Optional[str] = None
    sample_size_query: Optional[str] = None
    min_samples: int = 30
    experiment_id: Optional[int] = None
    metric_id: Optional[int] = None
    action_on_srm: str = "ROLLBACK"  # ROLLBACK or HOLD
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
    project_id: Optional[int] = None
    environment_id: Optional[int] = None
    environment_key: str = ""
    slack_webhook_url: Optional[str] = None
    generic_webhook_url: Optional[str] = None

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
            sample_size_query=g.get("sample_size_query"),
            min_samples=int(g.get("min_samples", 30)),
            experiment_id=g.get("experiment_id"),
            metric_id=g.get("metric_id"),
            action_on_srm=g.get("action_on_srm", "ROLLBACK"),
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
        project_id=int(data["project_id"]) if data.get("project_id") is not None else None,
        environment_id=int(data["environment_id"]) if data.get("environment_id") is not None else None,
        environment_key=data.get("environment_key", ""),
        slack_webhook_url=data.get("slack_webhook_url"),
        generic_webhook_url=data.get("generic_webhook_url"),
    )


def load_config(file_path: str | Path = "config.yaml") -> RolloutConfig:
    import os
    p = Path(file_path)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return load_config_from_dict(data)

    # Fallback to pure environment variables if config file is not present
    flag_name = os.environ.get("FLAG_NAME") or os.environ.get("FLAGSMITH_FLAG_NAME")
    if not flag_name:
        raise FileNotFoundError(
            f"Configuration file '{file_path}' not found, and no FLAG_NAME environment variable set. "
            f"Run `guardian init` or provide a valid config file."
        )

    stages_str = os.environ.get("STAGES", "1,5,25,100")
    stages = [int(s.strip()) for s in stages_str.split(",") if s.strip()]
    soak = int(os.environ.get("SOAK_TIME_SECONDS", "30"))
    approval = os.environ.get("REQUIRE_APPROVAL", "true").lower() in ("1", "true", "yes")
    env_key = os.environ.get("FLAGSMITH_ENVIRONMENT_KEY", "")

    guardrails: List[Guardrail] = []
    prom_query = os.environ.get("GUARDRAIL_QUERY")
    if prom_query:
        guardrails.append(
            Guardrail(
                name=f"{flag_name}_error_rate",
                metric_source="prometheus",
                comparison=os.environ.get("GUARDRAIL_COMPARISON", ">"),
                threshold=float(os.environ.get("GUARDRAIL_THRESHOLD", "5.0")),
                query=prom_query,
                sample_size_query=os.environ.get("GUARDRAIL_SAMPLE_QUERY", "sum(checkout_requests_total)"),
                min_samples=int(os.environ.get("GUARDRAIL_MIN_SAMPLES", "25")),
                description="Prometheus guardrail configured via environment",
            )
        )

    return RolloutConfig(
        flag_name=flag_name,
        stages=stages,
        soak_time_seconds=soak,
        require_approval_before_final=approval,
        guardrails=guardrails,
        environment_key=env_key,
        slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL"),
        generic_webhook_url=os.environ.get("GENERIC_WEBHOOK_URL"),
    )
