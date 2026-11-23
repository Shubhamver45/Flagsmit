"""
Guardian Orchestration Engine.
Coordinates config, state machine, Flagsmith client, guardrail metric evaluations,
audit logging, alert dispatching, and idempotent execution.
"""

from __future__ import annotations
import os
from typing import Any, Callable, Dict, List, Optional, Union
from guardian.config import RolloutConfig
from guardian.flagsmith_client import FlagsmithClientProtocol
from guardian.state_machine import RolloutDecision, RolloutState, RolloutStateMachine
from guardian.audit_log import AuditEntry, AuditLogger
from guardian.actions.alerts import AlertDispatcher
from guardian.actions.idempotency import IdempotencyManager


class GuardianEngine:
    def __init__(
        self,
        config: RolloutConfig,
        flagsmith_client: FlagsmithClientProtocol,
        audit_logger: Optional[AuditLogger] = None,
        metric_provider: Optional[Any] = None,
        alert_dispatcher: Optional[AlertDispatcher] = None,
        idempotency_manager: Optional[IdempotencyManager] = None,
    ):
        self.config = config
        self.flagsmith_client = flagsmith_client
        self.audit_logger = audit_logger or AuditLogger()
        self.metric_provider = metric_provider or (lambda: {})
        self.alert_dispatcher = alert_dispatcher or AlertDispatcher(
            slack_webhook_url=config.slack_webhook_url,
            generic_webhook_url=config.generic_webhook_url,
        )
        self.idempotency_manager = idempotency_manager or IdempotencyManager()
        self.state_machine = RolloutStateMachine(config)
        self.latest_evaluations: List[Any] = []

    @property
    def current_state(self) -> str:
        return self.state_machine.current_state

    @property
    def current_percentage(self) -> float:
        return self.state_machine.current_percentage

    def tick(
        self,
        metric_values: Optional[Dict[str, float]] = None,
        evaluations: Optional[List[Any]] = None,
        now: Optional[float] = None,
    ) -> AuditEntry:
        """
        Executes one evaluation cycle:
        1. Reads metrics via CompositeMetricProvider or callable.
        2. Steps the state machine.
        3. Enforces the action on Flagsmith (idempotently).
        4. Dispatches webhook alerts (idempotently).
        5. Appends decision and numerical evidence to audit log.
        """
        if evaluations is None and hasattr(self.metric_provider, "evaluate_all"):
            evaluations = self.metric_provider.evaluate_all(self.config.guardrails)
        elif metric_values is None and callable(self.metric_provider):
            res = self.metric_provider()
            if isinstance(res, list):
                evaluations = res
            elif isinstance(res, dict):
                metric_values = res

        if evaluations:
            self.latest_evaluations = evaluations

        prev_state = self.state_machine.current_state
        new_state, decision, reason, numbers = self.state_machine.evaluate(
            metric_values=metric_values,
            evaluations=evaluations,
            now=now,
        )

        # Notify metric providers at the start of a new stage soak window
        if new_state != prev_state and hasattr(self.metric_provider, "notify_stage_started"):
            try:
                self.metric_provider.notify_stage_started(new_state, guardrails=self.config.guardrails)
            except Exception:
                pass

        stage_idx = self.state_machine.current_stage_index
        should_run = self.idempotency_manager.should_execute(
            flag_name=self.config.flag_name,
            decision=decision.value,
            to_state=new_state,
            stage_index=stage_idx,
        )

        if should_run:
            # Apply actions to Flagsmith flag
            if decision in (RolloutDecision.ADVANCE, RolloutDecision.PROMOTED):
                pct = self.state_machine.current_percentage
                self.flagsmith_client.set_percentage_rollout(self.config.flag_name, pct)
            elif decision == RolloutDecision.ROLLBACK:
                self.flagsmith_client.rollback_to_zero(self.config.flag_name, reason=reason)

            self.idempotency_manager.mark_executed(
                flag_name=self.config.flag_name,
                decision=decision.value,
                to_state=new_state,
                stage_index=stage_idx,
            )

        entry = self.audit_logger.record(
            flag_name=self.config.flag_name,
            from_state=prev_state,
            to_state=new_state,
            decision=decision.value,
            reason=reason if should_run else f"[Idempotent Re-evaluation] {reason}",
            numbers=numbers,
        )

        # Dispatch alerts only when action is newly executed (never on duplicate/re-evaluation ticks)
        if should_run and decision.value != "HOLD":
            self.alert_dispatcher.dispatch(entry)

        return entry

    def check_approval_eligibility(self) -> tuple[bool, Optional[str]]:
        """
        Validates whether human approval can proceed before the final 100% promotion.
        Approval is blocked if:
        1. Sample Ratio Mismatch (SRM) warning is active (p-value < 0.001).
        2. Bayesian chance_to_be_worse exceeds the configured limit.
        3. Any operational guardrail is in a BREACH state.
        """
        evaluations = self.latest_evaluations
        if not evaluations and hasattr(self.metric_provider, "evaluate_all"):
            evaluations = self.metric_provider.evaluate_all(self.config.guardrails)
            self.latest_evaluations = evaluations

        for ev in evaluations:
            details = getattr(ev, "details", {}) or {}

            # 0. Error Check
            status_val = getattr(ev, "status", None)
            if status_val == "ERROR" or (hasattr(status_val, "value") and status_val.value == "ERROR"):
                return False, f"Approval blocked: Guardrail '{ev.name}' encountered an error: {getattr(ev, 'reason', '')}"

            # 1. Collecting Data / Insufficient Sample Check
            if details.get("is_collecting_data") or status_val == "INSUFFICIENT_DATA" or (hasattr(status_val, "value") and status_val.value == "INSUFFICIENT_DATA"):
                return False, "Approval blocked: Flagsmith is still collecting data (under 50 identities per arm or under 5 conversions). Statistical inference not yet available."

            # 2. SRM Check
            srm_warning = details.get("srm_warning", False)
            srm_p_val = details.get("srm_p_value")
            if srm_warning or (srm_p_val is not None and srm_p_val < 0.001):
                p_display = f"{srm_p_val:.6f}" if srm_p_val is not None else "< 0.001"
                return False, f"Approval blocked: Sample Ratio Mismatch (SRM) detected (p-value {p_display} < 0.001). Traffic assignment is corrupted."

            # 3. Bayesian Chance-to-be-Worse Check
            chance_worse = details.get("chance_to_be_worse")
            if chance_worse is None and getattr(ev, "value", None) is not None:
                # If metric itself is chance_to_be_worse
                for g in self.config.guardrails:
                    if g.name == ev.name and g.metric_source == "flagsmith_experiment":
                        chance_worse = ev.value
                        break

            if chance_worse is not None:
                for g in self.config.guardrails:
                    if g.name == ev.name and g.metric_source == "flagsmith_experiment":
                        if chance_worse > g.threshold:
                            return False, f"Approval blocked: Bayesian chance-to-be-worse ({chance_worse:.1f}%) exceeds configured limit ({g.threshold}%)."

            # 4. General Guardrail Breach
            if getattr(ev, "is_breached", False):
                return False, f"Approval blocked: Guardrail '{ev.name}' is breached: {getattr(ev, 'reason', '')}"

        return True, None

    def approve(self, now: Optional[float] = None) -> AuditEntry:
        """
        Operator action: Approve final promotion when in AWAITING_APPROVAL.
        Strictly blocked if SRM fires or chance_to_be_worse exceeds configured threshold.
        """
        if self.state_machine.current_state != RolloutState.AWAITING_APPROVAL.value:
            raise ValueError(
                f"Cannot approve rollout: Current state is '{self.state_machine.current_state}', "
                f"expected '{RolloutState.AWAITING_APPROVAL.value}'"
            )

        eligible, block_reason = self.check_approval_eligibility()
        if not eligible:
            raise ValueError(block_reason)

        self.state_machine.approve()
        return self.tick(now=now)

    def manual_rollback(self, reason: str = "Manual operator rollback", now: Optional[float] = None) -> AuditEntry:
        """
        Operator action: Force instant rollback to 0%.
        """
        prev_state = self.state_machine.current_state
        new_state, decision, dec_reason, numbers = self.state_machine.manual_rollback()

        should_run = self.idempotency_manager.should_execute(
            flag_name=self.config.flag_name,
            decision=decision.value,
            to_state=new_state,
            stage_index=-99,
        )

        if should_run:
            self.flagsmith_client.rollback_to_zero(self.config.flag_name, reason=reason)
            self.idempotency_manager.mark_executed(
                flag_name=self.config.flag_name,
                decision=decision.value,
                to_state=new_state,
                stage_index=-99,
            )

        entry = self.audit_logger.record(
            flag_name=self.config.flag_name,
            from_state=prev_state,
            to_state=new_state,
            decision=decision.value,
            reason=f"{reason} ({dec_reason})",
            numbers=numbers,
        )

        if should_run:
            self.alert_dispatcher.dispatch(entry)

        return entry

    def reset(self) -> AuditEntry:
        """
        Resets rollout state back to PENDING (0%) and clears idempotency tracking.
        """
        prev_state = self.state_machine.current_state
        self.state_machine = RolloutStateMachine(self.config)
        self.idempotency_manager.reset()
        self.flagsmith_client.set_percentage_rollout(self.config.flag_name, 0.0)
        entry = self.audit_logger.record(
            flag_name=self.config.flag_name,
            from_state=prev_state,
            to_state=self.state_machine.current_state,
            decision="ADVANCE",
            reason="Rollout state reset to initial PENDING by operator.",
            numbers={"percentage": 0.0},
        )
        return entry

    def get_status(self) -> Dict[str, Any]:
        guardrail_reports = []
        if self.latest_evaluations:
            for ev in self.latest_evaluations:
                guardrail_reports.append({
                    "name": ev.name,
                    "value": ev.value,
                    "sample_size": ev.sample_size,
                    "status": ev.status.value if hasattr(ev.status, "value") else str(ev.status),
                    "is_breached": getattr(ev, "is_breached", False),
                    "reason": getattr(ev, "reason", ""),
                    "details": getattr(ev, "details", {}),
                })
        else:
            for g in self.config.guardrails:
                guardrail_reports.append({
                    "name": g.name,
                    "threshold": g.threshold,
                    "comparison": g.comparison,
                    "metric_source": g.metric_source,
                    "status": "WAITING_FIRST_TICK",
                    "value": None,
                    "sample_size": None,
                    "is_breached": False,
                    "reason": "Awaiting initial metric evaluation",
                })

        import time
        now = time.time()
        soak_elapsed = 0.0
        if self.state_machine.stage_entered_at:
            soak_elapsed = max(0.0, round(now - self.state_machine.stage_entered_at, 1))

        # Build experiment results summary if present in guardrails
        experiment_summary = None
        for ev in (self.latest_evaluations or []):
            details = getattr(ev, "details", {}) or {}
            is_exp = (
                "as_of" in details
                or getattr(ev, "name", "").startswith("checkout_ab")
                or any(g.name == ev.name and g.metric_source == "flagsmith_experiment" for g in self.config.guardrails)
            )
            if is_exp:
                srm_val = details.get("srm_p_value")
                srm_warn = bool(details.get("srm_warning", False) or (srm_val is not None and srm_val < 0.001))
                chance_worse = details.get("chance_to_be_worse")
                if chance_worse is None and getattr(ev, "value", None) is not None:
                    chance_worse = ev.value

                eligible, block_reason = self.check_approval_eligibility()
                experiment_summary = {
                    "name": "Checkout A/B Experiment",
                    "as_of": details.get("as_of"),
                    "is_collecting_data": details.get("is_collecting_data", False),
                    "chance_to_be_worse": chance_worse,
                    "chance_to_win": details.get("chance_to_win"),
                    "lift": details.get("lift"),
                    "ci_low": details.get("ci_low"),
                    "ci_high": details.get("ci_high"),
                    "total_samples": details.get("total_samples", ev.sample_size),
                    "control_n": details.get("control_n"),
                    "variation_n": details.get("variation_n"),
                    "control_conversions": details.get("control_conversions"),
                    "variation_conversions": details.get("variation_conversions"),
                    "srm_p_value": srm_val,
                    "srm_warning": srm_warn,
                    "status": ev.status.value if hasattr(ev.status, "value") else str(ev.status),
                    "is_eligible_for_approval": eligible,
                    "block_reason": block_reason,
                    "demo_shortcut": bool(details.get("demo_shortcut", False) or os.environ.get("DEMO_FORCE_RECALC", "").lower() in ("1", "true", "yes")),
                }
                break

        return {
            "flag_name": self.config.flag_name,
            "state": self.state_machine.current_state,
            "percentage": self.state_machine.current_percentage,
            "stage_index": self.state_machine.current_stage_index,
            "is_approved": self.state_machine.is_approved,
            "stages": self.config.stages,
            "soak_time_seconds": self.config.soak_time_seconds,
            "soak_elapsed_seconds": soak_elapsed,
            "guardrails": guardrail_reports,
            "experiment": experiment_summary,
            "timeline": [e.to_dict() for e in reversed(self.audit_logger.entries)],
            "recent_audit_entries": [e.to_dict() for e in self.audit_logger.entries[-5:]],
        }
