"""
SwitchGuard Incident Intelligence.

Structured incident records are created whenever Guardian triggers an
automated action (rollback, drift containment, SRM block).  Each incident
captures the full context — metric name, observed value, threshold, state
at trigger time, containment duration — and a human-readable explanation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


@dataclass
class Incident:
    """A single production incident detected and acted on by SwitchGuard."""

    flag_name: str
    trigger_type: str          # "GUARDRAIL_BREACH" | "DRIFT" | "SRM" | "MANUAL"
    action_taken: str          # "ROLLBACK" | "HELD" | "ALERT"
    explanation: str           # Human-readable root-cause sentence

    # Optional structured context
    metric_name: Optional[str] = None
    observed_value: Optional[float] = None
    threshold: Optional[float] = None
    state_at_trigger: Optional[str] = None
    percentage_at_trigger: Optional[float] = None

    # Timing
    triggered_at: float = field(default_factory=time.time)
    resolved_at: Optional[float] = None

    # Filled in by storage on save
    id: Optional[int] = None

    @property
    def containment_duration_seconds(self) -> Optional[float]:
        if self.resolved_at is not None:
            return round(self.resolved_at - self.triggered_at, 2)
        return None

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None

    def resolve(self, now: Optional[float] = None) -> None:
        self.resolved_at = now if now is not None else time.time()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["containment_duration_seconds"] = self.containment_duration_seconds
        d["is_resolved"] = self.is_resolved
        return d
