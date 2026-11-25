"""
Guardian Configuration Schema and YAML Loader.
Supports single rollout or multiple rollouts at once.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import os
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
    in_flight_grace_seconds: float = 5.0

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
        if self.in_flight_grace_seconds < 0:
            raise ValueError("in_flight_grace_seconds must be non-negative")

    @property
    def rollouts(self) -> List[RolloutConfig]:
        return [self]

    @property
    def primary_rollout(self) -> RolloutConfig:
        return self


@dataclass(frozen=True)
class GuardianConfig:
    rollouts: List[RolloutConfig] = field(default_factory=list)
    db_path: str = "./data/guardian.db"
    in_flight_grace_seconds: float = 5.0

    @property
    def primary_rollout(self) -> RolloutConfig:
        if not self.rollouts:
            raise ValueError("No rollouts configured in GuardianConfig")
        return self.rollouts[0]

    def get_rollout(self, flag_name: str) -> Optional[RolloutConfig]:
        for r in self.rollouts:
            if r.flag_name == flag_name:
                return r
        return None

    # Forward common RolloutConfig attributes to primary_rollout for backwards compatibility
    @property
    def flag_name(self) -> str:
        return self.primary_rollout.flag_name

    @property
    def stages(self) -> List[int]:
        return self.primary_rollout.stages

    @property
    def soak_time_seconds(self) -> int:
        return self.primary_rollout.soak_time_seconds

    @property
    def require_approval_before_final(self) -> bool:
        return self.primary_rollout.require_approval_before_final

    @property
    def guardrails(self) -> List[Guardrail]:
        return self.primary_rollout.guardrails

    @property
    def project_id(self) -> Optional[int]:
        return self.primary_rollout.project_id

    @property
    def environment_id(self) -> Optional[int]:
        return self.primary_rollout.environment_id

    @property
    def environment_key(self) -> str:
        return self.primary_rollout.environment_key

    @property
    def slack_webhook_url(self) -> Optional[str]:
        return self.primary_rollout.slack_webhook_url

    @property
    def generic_webhook_url(self) -> Optional[str]:
        return self.primary_rollout.generic_webhook_url


def _parse_single_rollout(data: Dict[str, Any]) -> RolloutConfig:
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
        in_flight_grace_seconds=float(data.get("in_flight_grace_seconds", os.environ.get("GUARDIAN_IN_FLIGHT_GRACE_SECONDS", 5.0))),
    )


def load_config_from_dict(data: Dict[str, Any]) -> Union[RolloutConfig, GuardianConfig]:
    db_path = data.get("db_path", os.environ.get("GUARDIAN_DB_PATH", "./data/guardian.db"))

    if "rollouts" in data or "flags" in data:
        items = data.get("rollouts") or data.get("flags") or []
        rollouts = [_parse_single_rollout(item) for item in items]
        return GuardianConfig(rollouts=rollouts, db_path=db_path)

    single = _parse_single_rollout(data)
    return single


def load_config(file_path: str | Path = "config.yaml") -> Union[RolloutConfig, GuardianConfig]:
    p = Path(file_path)
    if not p.exists() and str(file_path) == "config.yaml":
        if Path("config.demo.yaml").exists():
            p = Path("config.demo.yaml")
        elif Path("config.example.yaml").exists():
            p = Path("config.example.yaml")

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
        in_flight_grace_seconds=float(os.environ.get("GUARDIAN_IN_FLIGHT_GRACE_SECONDS", 5.0)),
    )
