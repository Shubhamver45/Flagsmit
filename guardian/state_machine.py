"""
Guardian Rollout State Machine.
Defines the lifecycle: PENDING -> STAGE_n -> AWAITING_APPROVAL -> PROMOTED (or ROLLED_BACK).
"""

from __future__ import annotations
from enum import Enum
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from guardian.config import RolloutConfig


class RolloutState(str, Enum):
    PENDING = "PENDING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    PROMOTED = "PROMOTED"
    ROLLED_BACK = "ROLLED_BACK"

    @classmethod
    def stage_name(cls, stage_index: int) -> str:
        """Dynamic state representation for STAGE_1, STAGE_2, etc."""
        return f"STAGE_{stage_index + 1}"


class RolloutDecision(str, Enum):
    ADVANCE = "ADVANCE"
    HOLD = "HOLD"
    ROLLBACK = "ROLLBACK"
    AWAIT_APPROVAL = "AWAIT_APPROVAL"
    PROMOTED = "PROMOTED"
    DRIFT = "DRIFT"
    RESOLVED = "RESOLVED"


class RolloutStateMachine:
    def __init__(self, config: RolloutConfig):
        self.config = config
        self.current_state: str = RolloutState.PENDING.value
        self.current_stage_index: int = -1  # -1 means PENDING
        self.stage_entered_at: Optional[float] = None
        self.is_approved: bool = False

    def restore_state(
        self,
        current_state: str,
        current_stage_index: int,
        stage_entered_at: Optional[float],
        is_approved: bool = False,
    ) -> None:
        self.current_state = current_state
        self.current_stage_index = current_stage_index
        self.stage_entered_at = stage_entered_at
        self.is_approved = is_approved

    @property
    def current_percentage(self) -> float:
        if self.current_state == RolloutState.PENDING.value or self.current_state == RolloutState.ROLLED_BACK.value:
            return 0.0
        if self.current_state == RolloutState.PROMOTED.value:
            return float(self.config.stages[-1])
        if 0 <= self.current_stage_index < len(self.config.stages):
            return float(self.config.stages[self.current_stage_index])
        return 0.0

    def start(self, now: Optional[float] = None) -> Tuple[str, RolloutDecision, str, Dict[str, Any]]:
        """Transition from PENDING to first stage."""
        if self.current_state != RolloutState.PENDING.value:
            raise ValueError(f"Cannot start rollout from state {self.current_state}")

        now = now if now is not None else time.time()
        self.current_stage_index = 0
        self.current_state = RolloutState.stage_name(0)
        self.stage_entered_at = now

        first_pct = self.config.stages[0]
        numbers = {
            "stage_index": 0,
            "target_percentage": first_pct,
            "timestamp": now,
        }
        reason = f"Rollout initiated at Stage 1 ({first_pct}%)."
        return self.current_state, RolloutDecision.ADVANCE, reason, numbers

    def evaluate(
        self,
        metric_values: Optional[Dict[str, float]] = None,
        evaluations: Optional[List[Any]] = None,
        now: Optional[float] = None,
    ) -> Tuple[str, RolloutDecision, str, Dict[str, Any]]:
        """
        Evaluates current metrics and soak time against guardrails.
        Supports structured MetricEvaluation objects or raw metric_values dictionary.
        Returns: (new_state, decision, reason, numbers)
        """
        now = now if now is not None else time.time()
        metric_values = metric_values or {}

        # Rolled back is a terminal state (stays at 0% until explicit operator reset)
        if self.current_state == RolloutState.ROLLED_BACK.value:
            numbers = {"current_percentage": self.current_percentage, "state": self.current_state}
            return self.current_state, RolloutDecision.HOLD, f"Rollout in terminal state: {self.current_state}", numbers

        # If PENDING, start automatically
        if self.current_state == RolloutState.PENDING.value:
            return self.start(now=now)

        # 1. Guardrail Evaluation from structured evaluations (if provided)
        insufficient_data_reasons = []
        if evaluations:
            for ev in evaluations:
                if ev.is_breached:
                    prev_state = self.current_state
                    self.current_state = RolloutState.ROLLED_BACK.value
                    numbers = {
                        "metric_name": ev.name,
                        "metric_value": ev.value,
                        "sample_size": ev.sample_size,
                        "stage_index": self.current_stage_index,
                        "rolled_back_from_percentage": self.current_percentage,
                        "details": ev.details,
                    }
                    return self.current_state, RolloutDecision.ROLLBACK, ev.reason, numbers
                if getattr(ev, "status", None) and ev.status.value == "INSUFFICIENT_DATA":
                    # Operational guardrails hold stage advancement on insufficient sample size.
                    # Experiment data collection is evaluated at the approval gate before promotion.
                    details = getattr(ev, "details", {}) or {}
                    if not details.get("is_collecting_data"):
                        insufficient_data_reasons.append(ev.reason)

        # Fallback to evaluating raw metric_values dictionary
        for guardrail in self.config.guardrails:
            val = metric_values.get(guardrail.name)
            if val is not None:
                breached = guardrail.evaluate(val)
                if breached:
                    prev_state = self.current_state
                    self.current_state = RolloutState.ROLLED_BACK.value
                    numbers = {
                        "metric_name": guardrail.name,
                        "metric_value": val,
                        "threshold": guardrail.threshold,
                        "comparison": guardrail.comparison,
                        "stage_index": self.current_stage_index,
                        "rolled_back_from_percentage": self.current_percentage,
                    }
                    reason = (
                        f"Guardrail '{guardrail.name}' breached! "
                        f"Observed value {val} {guardrail.comparison} threshold {guardrail.threshold}."
                    )
                    return self.current_state, RolloutDecision.ROLLBACK, reason, numbers

        # 2. Minimum Sample Size Check (Insufficent data forces HOLD)
        if insufficient_data_reasons:
            numbers = {
                "stage_index": self.current_stage_index,
                "current_percentage": self.current_percentage,
                "insufficient_reasons": insufficient_data_reasons,
            }
            reason = f"Holding stage {self.current_state} due to insufficient sample size: {insufficient_data_reasons[0]}"
            return self.current_state, RolloutDecision.HOLD, reason, numbers

        # If already fully promoted and guardrails are healthy, hold at 100%
        if self.current_state == RolloutState.PROMOTED.value:
            numbers = {"current_percentage": self.current_percentage, "state": self.current_state}
            return self.current_state, RolloutDecision.HOLD, "Rollout fully promoted at 100%. Guardrails healthy.", numbers

        # 3. If AWAITING_APPROVAL, check if human approval was granted
        if self.current_state == RolloutState.AWAITING_APPROVAL.value:
            if self.is_approved:
                self.current_state = RolloutState.PROMOTED.value
                self.current_stage_index = len(self.config.stages) - 1
                final_pct = self.config.stages[-1]
                numbers = {
                    "stage_index": self.current_stage_index,
                    "promoted_percentage": final_pct,
                }
                reason = f"Human approval granted. Promoted to final stage ({final_pct}%)."
                return self.current_state, RolloutDecision.PROMOTED, reason, numbers
            else:
                numbers = {
                    "current_percentage": self.current_percentage,
                    "target_percentage": self.config.stages[-1],
                }
                reason = "All guardrails healthy. Awaiting explicit human approval for final promotion."
                return self.current_state, RolloutDecision.HOLD, reason, numbers

        # 3. Check Soak Time in Current Stage
        elapsed_soak = now - (self.stage_entered_at or now)
        numbers = {
            "stage_index": self.current_stage_index,
            "current_percentage": self.current_percentage,
            "soak_elapsed_seconds": round(elapsed_soak, 2),
            "soak_required_seconds": self.config.soak_time_seconds,
            "metrics": metric_values,
        }

        if elapsed_soak < self.config.soak_time_seconds:
            reason = (
                f"Soaking in {self.current_state} ({self.current_percentage}%). "
                f"Elapsed {elapsed_soak:.1f}s < required {self.config.soak_time_seconds}s. Metrics healthy."
            )
            return self.current_state, RolloutDecision.HOLD, reason, numbers

        # 4. Advance Stage
        next_stage_index = self.current_stage_index + 1
        is_final_stage = next_stage_index == len(self.config.stages) - 1

        # If next stage is the final stage and approval is required
        if is_final_stage and self.config.require_approval_before_final and not self.is_approved:
            self.current_state = RolloutState.AWAITING_APPROVAL.value
            reason = (
                f"Soak time completed for {RolloutState.stage_name(self.current_stage_index)} ({self.current_percentage}%). "
                f"Ready for final stage ({self.config.stages[next_stage_index]}%), but requires human approval."
            )
            return self.current_state, RolloutDecision.AWAIT_APPROVAL, reason, numbers

        # Advance to next stage or promote
        self.current_stage_index = next_stage_index
        self.stage_entered_at = now

        if next_stage_index >= len(self.config.stages) - 1:
            self.current_state = RolloutState.PROMOTED.value
            final_pct = self.config.stages[-1]
            numbers["final_percentage"] = final_pct
            reason = f"Final rollout stage achieved and verified ({final_pct}%). Promoted."
            return self.current_state, RolloutDecision.PROMOTED, reason, numbers

        self.current_state = RolloutState.stage_name(next_stage_index)
        next_pct = self.config.stages[next_stage_index]
        numbers["target_percentage"] = next_pct
        reason = f"Soak time satisfied and metrics healthy. Advanced to {self.current_state} ({next_pct}%)."
        return self.current_state, RolloutDecision.ADVANCE, reason, numbers

    def approve(self) -> None:
        """Grant human approval for final stage promotion."""
        self.is_approved = True

    def manual_rollback(self) -> Tuple[str, RolloutDecision, str, Dict[str, Any]]:
        """Explicit manual rollback trigger."""
        prev_state = self.current_state
        self.current_state = RolloutState.ROLLED_BACK.value
        numbers = {"rolled_back_from_state": prev_state, "previous_percentage": self.current_percentage}
        reason = "Manual rollback triggered by operator."
        return self.current_state, RolloutDecision.ROLLBACK, reason, numbers
