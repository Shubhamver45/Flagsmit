"""
Base Metric Evaluation Interfaces and Data Structures.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Protocol


class MetricStatus(str, Enum):
    HEALTHY = "HEALTHY"
    BREACH = "BREACH"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    SRM_BREACH = "SRM_BREACH"
    ERROR = "ERROR"


@dataclass(frozen=True)
class MetricEvaluation:
    name: str
    value: Optional[float]
    sample_size: int
    is_breached: bool
    status: MetricStatus
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)


class MetricSourceProtocol(Protocol):
    def evaluate(self, guardrail: Any) -> MetricEvaluation:
        ...
