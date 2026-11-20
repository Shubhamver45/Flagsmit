"""
Guardian Orchestration Engine.
Coordinates config, state machine, Flagsmith client, guardrail metric evaluations, and audit logging.
"""

from __future__ import annotations
from typing import Any, Callable, Dict, Optional, Union
from guardian.config import RolloutConfig
from guardian.flagsmith_client import FlagsmithClientProtocol
from guardian.state_machine import RolloutDecision, RolloutState, RolloutStateMachine
from guardian.audit_log import AuditEntry, AuditLogger


class GuardianEngine:
    def __init__(
        self,
        config: RolloutConfig,
        flagsmith_client: FlagsmithClientProtocol,
        audit_logger: Optional[AuditLogger] = None,
        metric_provider: Optional[Callable[[], Dict[str, float]]] = None,
    ):
        self.config = config
        self.flagsmith_client = flagsmith_client
        self.audit_logger = audit_logger or AuditLogger()
        self.metric_provider = metric_provider or (lambda: {})
        self.state_machine = RolloutStateMachine(config)

    @property
    def current_state(self) -> str:
        return self.state_machine.current_state

    @property
    def current_percentage(self) -> float:
        return self.state_machine.current_percentage

    def tick(
        self,
        metric_values: Optional[Dict[str, float]] = None,
        now: Optional[float] = None,
    ) -> AuditEntry:
        """
        Executes one evaluation cycle:
        1. Reads metrics.
        2. Steps the state machine.
        3. Enforces the action on Flagsmith (advance, rollback, hold).
        4. Appends decision and numerical evidence to audit log.
        """
        if metric_values is None:
            metric_values = self.metric_provider()

        prev_state = self.state_machine.current_state
        new_state, decision, reason, numbers = self.state_machine.evaluate(metric_values, now=now)

        # Apply actions to Flagsmith flag
        if decision in (RolloutDecision.ADVANCE, RolloutDecision.PROMOTED):
            pct = self.state_machine.current_percentage
            self.flagsmith_client.set_percentage_rollout(self.config.flag_name, pct)
        elif decision == RolloutDecision.ROLLBACK:
            self.flagsmith_client.rollback_to_zero(self.config.flag_name, reason=reason)

        entry = self.audit_logger.record(
            flag_name=self.config.flag_name,
            from_state=prev_state,
            to_state=new_state,
            decision=decision.value,
            reason=reason,
            numbers=numbers,
        )
        return entry

    def approve(self, now: Optional[float] = None) -> AuditEntry:
        """
        Operator action: Approve final promotion when in AWAITING_APPROVAL.
        """
        self.state_machine.approve()
        return self.tick(now=now)

    def manual_rollback(self, reason: str = "Manual operator rollback", now: Optional[float] = None) -> AuditEntry:
        """
        Operator action: Force instant rollback to 0%.
        """
        prev_state = self.state_machine.current_state
        new_state, decision, dec_reason, numbers = self.state_machine.manual_rollback()
        self.flagsmith_client.rollback_to_zero(self.config.flag_name, reason=reason)

        return self.audit_logger.record(
            flag_name=self.config.flag_name,
            from_state=prev_state,
            to_state=new_state,
            decision=decision.value,
            reason=f"{reason} ({dec_reason})",
            numbers=numbers,
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            "flag_name": self.config.flag_name,
            "state": self.state_machine.current_state,
            "percentage": self.state_machine.current_percentage,
            "stage_index": self.state_machine.current_stage_index,
            "is_approved": self.state_machine.is_approved,
            "stages": self.config.stages,
            "soak_time_seconds": self.config.soak_time_seconds,
            "recent_audit_entries": [e.to_dict() for e in self.audit_logger.entries[-5:]],
        }
