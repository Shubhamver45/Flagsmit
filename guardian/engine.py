"""
Guardian Orchestration Engine.
Coordinates config, state machine, Flagsmith client, guardrail metric evaluations,
audit logging, alert dispatching, and idempotent execution.
Supports multiple rollouts simultaneously, SQLite persistence across restarts,
and automatic startup reconciliation with drift detection.
"""

from __future__ import annotations
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Union

from guardian.config import GuardianConfig, RolloutConfig
from guardian.flagsmith_client import FlagsmithClientProtocol
from guardian.state_machine import RolloutDecision, RolloutState, RolloutStateMachine
from guardian.audit_log import AuditEntry, AuditLogger
from guardian.actions.alerts import AlertDispatcher
from guardian.actions.idempotency import IdempotencyManager
from guardian.storage import GuardianStorage

logger = logging.getLogger(__name__)


class GuardianEngine:
    def __init__(
        self,
        config: Union[RolloutConfig, GuardianConfig],
        flagsmith_client: FlagsmithClientProtocol,
        audit_logger: Optional[AuditLogger] = None,
        metric_provider: Optional[Any] = None,
        alert_dispatcher: Optional[AlertDispatcher] = None,
        idempotency_manager: Optional[IdempotencyManager] = None,
        storage: Optional[GuardianStorage] = None,
        skip_startup_reconciliation: bool = False,
    ):
        self.raw_config = config
        self.storage = storage or GuardianStorage()
        self.flagsmith_client = flagsmith_client

        # Build rollout configs map
        self.rollout_configs: Dict[str, RolloutConfig] = {
            rc.flag_name: rc for rc in config.rollouts
        }
        self.state_machines: Dict[str, RolloutStateMachine] = {
            rc.flag_name: RolloutStateMachine(rc) for rc in config.rollouts
        }
        self.drift_flags: Dict[str, Dict[str, Any]] = {
            flag_name: {"detected": False} for flag_name in self.rollout_configs
        }
        self.latest_evaluations: Dict[str, List[Any]] = {
            flag_name: [] for flag_name in self.rollout_configs
        }
        self.in_flight_grace_seconds: float = float(
            getattr(config, "in_flight_grace_seconds", os.environ.get("GUARDIAN_IN_FLIGHT_GRACE_SECONDS", 5.0))
        )
        self.echo_window_seconds: float = float(
            getattr(config, "echo_window_seconds", os.environ.get("GUARDIAN_ECHO_WINDOW_SECONDS", 30.0))
        )
        self.in_flight_transitions: Dict[str, Dict[str, Any]] = {}
        self._reconcile_lock = threading.Lock()

        # Backwards compatibility accessors for primary rollout
        self.config: RolloutConfig = getattr(config, "primary_rollout", config.rollouts[0])
        self.state_machine: RolloutStateMachine = self.state_machines[self.config.flag_name]

        self.audit_logger = audit_logger or AuditLogger(storage=self.storage)
        self.metric_provider = metric_provider or (lambda: {})
        self.alert_dispatcher = alert_dispatcher or AlertDispatcher(
            slack_webhook_url=self.config.slack_webhook_url,
            generic_webhook_url=self.config.generic_webhook_url,
        )
        self.idempotency_manager = idempotency_manager or IdempotencyManager(storage=self.storage)

        if not skip_startup_reconciliation:
            self.reconcile_on_startup()

    @property
    def current_state(self) -> str:
        return self.state_machine.current_state

    @property
    def current_percentage(self) -> float:
        return self.state_machine.current_percentage

    # -------------------------------------------------------------------------
    # Startup Reconciliation & Drift Detection
    # -------------------------------------------------------------------------
    def reconcile_on_startup(self) -> None:
        """
        On startup, reconcile: read the flag's real percentage from Flagsmith
        and compare it to the stored state. If they differ, hold, log it,
        and show a DRIFT warning on the status page instead of acting blindly.
        """
        for flag_name, rc in self.rollout_configs.items():
            sm = self.state_machines[flag_name]
            saved = self.storage.load_rollout_state(flag_name)

            if saved:
                sm.restore_state(
                    current_state=saved["current_state"],
                    current_stage_index=saved["current_stage_index"],
                    stage_entered_at=saved["stage_entered_at"],
                    is_approved=saved["is_approved"],
                    paused_from_state=saved.get("paused_from_state"),
                )
                expected_pct = sm.current_percentage
                stored_state = sm.current_state
                logger.info(
                    f"Loaded stored state for '{flag_name}' from SQLite: "
                    f"state={stored_state}, stage={sm.current_stage_index}, "
                    f"entered_at={sm.stage_entered_at}, expected_pct={expected_pct}%"
                )
            else:
                expected_pct = 0.0
                stored_state = RolloutState.PENDING.value

            # Read actual flag percentage from Flagsmith
            try:
                real_pct = self.flagsmith_client.get_percentage_rollout(flag_name)
            except Exception as e:
                logger.warning(f"Could not read actual Flagsmith rollout percentage for '{flag_name}': {e}")
                real_pct = expected_pct

            # Check if this is a standard boolean flag (where Flagsmith returns 100.0 for any enabled state)
            is_standard_flag_match = False
            if real_pct == 100.0 and expected_pct > 0.0 and hasattr(self.flagsmith_client, "get_environment_feature_state"):
                try:
                    fs = self.flagsmith_client.get_environment_feature_state(flag_name)
                    if isinstance(fs, dict) and not fs.get("multivariate_feature_state_values"):
                        is_standard_flag_match = True
                except Exception:
                    pass

            # Compare actual vs stored expected
            if not is_standard_flag_match and abs(real_pct - expected_pct) > 0.01:
                drift_msg = (
                    f"Flagsmith actual percentage ({real_pct}%) does not match "
                    f"stored state '{stored_state}' expected percentage ({expected_pct}%)."
                )
                logger.warning(f"⚠️  [DRIFT DETECTED] Flag '{flag_name}': {drift_msg} Holding rollout.")
                drift_info = {
                    "detected": True,
                    "actual_percentage": real_pct,
                    "expected_percentage": expected_pct,
                    "stored_state": stored_state,
                    "message": drift_msg,
                }
                self.drift_flags[flag_name] = drift_info
                self.storage.save_rollout_state(
                    flag_name=flag_name,
                    current_state=stored_state,
                    current_stage_index=sm.current_stage_index,
                    stage_entered_at=sm.stage_entered_at,
                    current_percentage=expected_pct,
                    is_approved=sm.is_approved,
                    drift_detected=True,
                    drift_details=drift_info,
                )
            else:
                self.drift_flags[flag_name] = {"detected": False}
                logger.info(
                    f"✔ Reconciled flag '{flag_name}': Flagsmith actual percentage ({real_pct}%) "
                    f"matches stored state '{stored_state}' ({expected_pct}%)."
                )
                self.storage.save_rollout_state(
                    flag_name=flag_name,
                    current_state=sm.current_state,
                    current_stage_index=sm.current_stage_index,
                    stage_entered_at=sm.stage_entered_at,
                    current_percentage=sm.current_percentage,
                    is_approved=sm.is_approved,
                    drift_detected=False,
                    drift_details=None,
                )

    # -------------------------------------------------------------------------
    # Webhook Handling & Runtime Reconciliation
    # -------------------------------------------------------------------------
    def handle_flagsmith_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Processes incoming Flagsmith webhook events (FLAG_UPDATED).
        Verifies if the flag is managed, checks if the actual percentage differs from
        what Guardian last wrote to SQLite. If it differs, pauses the rollout with DRIFT
        warning, dispatches alerts, and updates SQLite state.
        Guardian's own writes must NOT trigger drift.
        Deduplicates identical drift events within the grace window.
        """
        event_type = payload.get("event_type")
        if event_type != "FLAG_UPDATED":
            return {"status": "ignored", "reason": f"Event type '{event_type}' not handled"}

        data = payload.get("data", {})
        new_state = data.get("new_state", {})
        feature = new_state.get("feature", {})
        flag_name = feature.get("name")
        changed_by = data.get("changed_by") or "unknown operator"

        if not flag_name or flag_name not in self.rollout_configs:
            return {"status": "ignored", "reason": f"Flag '{flag_name}' is not managed by Guardian"}

        with self._reconcile_lock:
            sm = self.state_machines[flag_name]
            saved = self.storage.load_rollout_state(flag_name)
            stored_pct = saved["current_percentage"] if saved else sm.current_percentage
            sm_pct = sm.current_percentage
            stored_state = sm.current_state if sm.current_state != "PENDING" else (saved["current_state"] if saved else "PENDING")
            expected_pct = sm.current_percentage if sm.current_percentage > 0.0 else stored_pct

            is_enabled = bool(new_state.get("enabled", False))
            mv_values = new_state.get("multivariate_feature_state_values", [])

            if not is_enabled:
                actual_pct = 0.0
            elif mv_values:
                actual_pct = float(mv_values[0].get("percentage_allocation", 0.0))
            else:
                actual_pct = 100.0 if is_enabled else 0.0

            # Check if standard boolean flag match
            is_standard_flag_match = False
            if not mv_values and is_enabled and (stored_pct > 0.0 or sm_pct > 0.0):
                is_standard_flag_match = True

            # Check if this write is an in-flight Guardian transition within the grace window
            in_flight = self.in_flight_transitions.get(flag_name)
            is_in_flight = False
            if in_flight and time.time() < in_flight.get("expires_at", 0):
                prev_in_flight = in_flight.get("previous_percentage", -1.0)
                target_in_flight = in_flight.get("target_percentage", -1.0)
                if abs(actual_pct - prev_in_flight) <= 0.01 or abs(actual_pct - target_in_flight) <= 0.01:
                    is_in_flight = True
                    if abs(actual_pct - target_in_flight) <= 0.01:
                        self.in_flight_transitions.pop(flag_name, None)

            # Check if this write matches a ledger entry written by Guardian within GUARDIAN_ECHO_WINDOW_SECONDS
            is_echo = self.storage.is_recent_write(
                flag_name=flag_name,
                percentage=actual_pct,
                window_seconds=self.echo_window_seconds,
            )

            matches_expected = (
                is_standard_flag_match
                or is_in_flight
                or is_echo
                or abs(actual_pct - stored_pct) <= 0.01
                or abs(actual_pct - sm_pct) <= 0.01
            )

            if matches_expected:
                if self.drift_flags.get(flag_name, {}).get("detected"):
                    # Only resolve if the actual percentage matches the expected target percentage
                    if abs(actual_pct - expected_pct) <= 0.01:
                        resolve_msg = f"Drift resolved via webhook: flag '{flag_name}' restored to expected {expected_pct}% ({stored_state})."
                        logger.info(f"✔ {resolve_msg}")
                        self.drift_flags[flag_name] = {"detected": False}
                        self.storage.save_rollout_state(
                            flag_name=flag_name,
                            current_state=stored_state,
                            current_stage_index=sm.current_stage_index,
                            stage_entered_at=sm.stage_entered_at,
                            current_percentage=expected_pct,
                            is_approved=sm.is_approved,
                            drift_detected=False,
                            drift_details=None,
                        )
                        entry = self.audit_logger.record(
                            flag_name=flag_name,
                            from_state=stored_state,
                            to_state=stored_state,
                            decision="RESOLVED",
                            reason=f"[DRIFT RESOLVED via Webhook] {resolve_msg}",
                            numbers={
                                "target_percentage": expected_pct,
                                "actual_percentage": actual_pct,
                                "expected_percentage": expected_pct,
                                "source": "webhook",
                            },
                        )
                        self.alert_dispatcher.dispatch(entry)
                if is_echo and abs(actual_pct - expected_pct) > 0.01:
                    logger.info(
                        f"✔ Webhook for '{flag_name}': actual ({actual_pct}%) is a verified Guardian echo written within {self.echo_window_seconds}s. Skipping false drift."
                    )
                else:
                    logger.info(f"✔ Webhook for '{flag_name}': actual ({actual_pct}%) matches expected ({expected_pct}%). No drift.")
                return {
                    "status": "ok",
                    "message": f"Flag '{flag_name}' state matches expected state or echo ({expected_pct}%).",
                    "flag_name": flag_name,
                }

            # Deduplicate identical drift events (same flag, actual, and expected within grace window)
            current_drift = self.drift_flags.get(flag_name, {})
            now_ts = time.time()
            drift_ts = current_drift.get("detected_at")
            if not isinstance(drift_ts, (int, float)):
                drift_ts = current_drift.get("timestamp")
            ts_diff = (now_ts - drift_ts) if isinstance(drift_ts, (int, float)) else 0.0

            if (
                current_drift.get("detected")
                and abs(float(current_drift.get("actual_percentage", -1.0)) - actual_pct) <= 0.01
                and abs(float(current_drift.get("expected_percentage", -1.0)) - expected_pct) <= 0.01
                and ts_diff < self.in_flight_grace_seconds
            ):
                return {
                    "status": "drift_already_active",
                    "flag_name": flag_name,
                    "drift": current_drift,
                }

            # Out-of-band change detected!
            drift_msg = (
                f"Flag '{flag_name}' modified out-of-band by {changed_by}: "
                f"actual {actual_pct}% differs from expected {expected_pct}% ({stored_state})."
            )
            logger.warning(f"⚠️  [DRIFT DETECTED via Webhook] {drift_msg} Holding rollout.")
            drift_info = {
                "detected": True,
                "source": "webhook",
                "changed_by": changed_by,
                "actual_percentage": actual_pct,
                "expected_percentage": expected_pct,
                "stored_state": stored_state,
                "message": drift_msg,
                "timestamp": data.get("timestamp") or now_ts,
                "detected_at": now_ts,
            }
            self.drift_flags[flag_name] = drift_info
            self.storage.save_rollout_state(
                flag_name=flag_name,
                current_state=stored_state,
                current_stage_index=sm.current_stage_index,
                stage_entered_at=sm.stage_entered_at,
                current_percentage=expected_pct,
                is_approved=sm.is_approved,
                drift_detected=True,
                drift_details=drift_info,
            )

            entry = self.audit_logger.record(
                flag_name=flag_name,
                from_state=stored_state,
                to_state=stored_state,
                decision="DRIFT",
                reason=f"[DRIFT DETECTED via Webhook] {drift_msg}",
                numbers={
                    "target_percentage": actual_pct,
                    "changed_by": changed_by,
                    "actual_percentage": actual_pct,
                    "expected_percentage": expected_pct,
                    "source": "webhook",
                },
            )
            self.alert_dispatcher.dispatch(entry)

            return {
                "status": "drift_detected",
                "flag_name": flag_name,
                "drift": drift_info,
            }

    def reconcile_runtime(self) -> Dict[str, Any]:
        """
        Periodic runtime reconciliation: reads each flag's real percentage from Flagsmith
        and compares it to stored state. Drift detected via polling behaves identically
        to drift detected via webhook.
        """
        with self._reconcile_lock:
            results = {}
            for flag_name, rc in self.rollout_configs.items():
                sm = self.state_machines[flag_name]
                saved = self.storage.load_rollout_state(flag_name)
                stored_pct = saved["current_percentage"] if saved else sm.current_percentage
                sm_pct = sm.current_percentage
                stored_state = saved["current_state"] if saved else sm.current_state

                try:
                    real_pct = self.flagsmith_client.get_percentage_rollout(flag_name)
                except Exception as e:
                    logger.warning(f"Reconciliation query failed for '{flag_name}': {e}")
                    continue

                is_standard_flag_match = False
                if real_pct == 100.0 and (stored_pct > 0.0 or sm_pct > 0.0) and hasattr(self.flagsmith_client, "get_environment_feature_state"):
                    try:
                        fs = self.flagsmith_client.get_environment_feature_state(flag_name)
                        if isinstance(fs, dict) and not fs.get("multivariate_feature_state_values"):
                            is_standard_flag_match = True
                    except Exception:
                        pass

                # Check if this write is an in-flight Guardian transition within the grace window
                in_flight = self.in_flight_transitions.get(flag_name)
                is_in_flight = False
                if in_flight and time.time() < in_flight.get("expires_at", 0):
                    prev_in_flight = in_flight.get("previous_percentage", -1.0)
                    target_in_flight = in_flight.get("target_percentage", -1.0)
                    if abs(real_pct - prev_in_flight) <= 0.01 or abs(real_pct - target_in_flight) <= 0.01:
                        is_in_flight = True
                        logger.debug(
                            f"Reconciler: flag '{flag_name}' is in-flight ({prev_in_flight}% -> {target_in_flight}%, actual {real_pct}%). Skipping false drift."
                        )
                        if abs(real_pct - target_in_flight) <= 0.01:
                            self.in_flight_transitions.pop(flag_name, None)

                # Check if this write matches a ledger entry written by Guardian within GUARDIAN_ECHO_WINDOW_SECONDS
                is_echo = self.storage.is_recent_write(
                    flag_name=flag_name,
                    percentage=real_pct,
                    window_seconds=self.echo_window_seconds,
                )
                if is_echo and abs(real_pct - stored_pct) > 0.01:
                    logger.debug(
                        f"Reconciler: flag '{flag_name}' actual {real_pct}% matches write in echo window ({self.echo_window_seconds}s). Skipping false drift."
                    )

                matches_expected = (
                    is_standard_flag_match
                    or is_in_flight
                    or is_echo
                    or abs(real_pct - stored_pct) <= 0.01
                    or abs(real_pct - sm_pct) <= 0.01
                )
                has_drift = not matches_expected
                expected_pct = sm.current_percentage if sm.current_percentage > 0.0 else stored_pct
                stored_state = sm.current_state if sm.current_state != "PENDING" else (saved["current_state"] if saved else "PENDING")

                if has_drift:
                    current_drift = self.drift_flags.get(flag_name, {})
                    if not current_drift.get("detected"):
                        drift_msg = (
                            f"Flagsmith actual percentage ({real_pct}%) differs from "
                            f"stored state '{stored_state}' expected percentage ({expected_pct}%)."
                        )
                        logger.warning(f"⚠️  [DRIFT DETECTED via Polling] Flag '{flag_name}': {drift_msg} Holding rollout.")
                        drift_info = {
                            "detected": True,
                            "source": "polling",
                            "changed_by": "unknown operator (detected via reconciliation polling)",
                            "actual_percentage": real_pct,
                            "expected_percentage": expected_pct,
                            "stored_state": stored_state,
                            "message": drift_msg,
                            "timestamp": time.time(),
                        }
                        self.drift_flags[flag_name] = drift_info
                        self.storage.save_rollout_state(
                            flag_name=flag_name,
                            current_state=stored_state,
                            current_stage_index=sm.current_stage_index,
                            stage_entered_at=sm.stage_entered_at,
                            current_percentage=expected_pct,
                            is_approved=sm.is_approved,
                            drift_detected=True,
                            drift_details=drift_info,
                        )
                        entry = self.audit_logger.record(
                            flag_name=flag_name,
                            from_state=stored_state,
                            to_state=stored_state,
                            decision="DRIFT",
                            reason=f"[DRIFT DETECTED via Polling] {drift_msg}",
                            numbers={
                                "target_percentage": real_pct,
                                "actual_percentage": real_pct,
                                "expected_percentage": expected_pct,
                                "source": "polling",
                            },
                        )
                        self.alert_dispatcher.dispatch(entry)
                    results[flag_name] = {"drift": True, "actual": real_pct, "expected": expected_pct}
                else:
                    if self.drift_flags.get(flag_name, {}).get("detected"):
                        resolve_msg = f"Drift resolved via polling: flag '{flag_name}' restored to expected {expected_pct}% ({stored_state})."
                        logger.info(f"✔ {resolve_msg}")
                        self.drift_flags[flag_name] = {"detected": False}
                        self.storage.save_rollout_state(
                            flag_name=flag_name,
                            current_state=stored_state,
                            current_stage_index=sm.current_stage_index,
                            stage_entered_at=sm.stage_entered_at,
                            current_percentage=expected_pct,
                            is_approved=sm.is_approved,
                            drift_detected=False,
                            drift_details=None,
                        )
                        entry = self.audit_logger.record(
                            flag_name=flag_name,
                            from_state=stored_state,
                            to_state=stored_state,
                            decision="RESOLVED",
                            reason=f"[DRIFT RESOLVED via Polling] {resolve_msg}",
                            numbers={
                                "target_percentage": expected_pct,
                                "actual_percentage": real_pct,
                                "expected_percentage": expected_pct,
                                "source": "polling",
                            },
                        )
                        self.alert_dispatcher.dispatch(entry)
                    results[flag_name] = {"drift": False, "actual": real_pct, "expected": expected_pct}

            return results

    # -------------------------------------------------------------------------
    # Evaluation Cycle
    # -------------------------------------------------------------------------
    def tick(
        self,
        flag_name: Optional[str] = None,
        metric_values: Optional[Dict[str, float]] = None,
        evaluations: Optional[List[Any]] = None,
        now: Optional[float] = None,
    ) -> AuditEntry:
        """
        Executes evaluation cycle for a specific flag, or all rollouts if flag_name is None.
        Returns the AuditEntry of the targeted/primary flag.
        """
        if flag_name is not None:
            return self._tick_single(
                flag_name=flag_name,
                metric_values=metric_values,
                evaluations=evaluations,
                now=now,
            )

        # Tick all rollouts
        entries: Dict[str, AuditEntry] = {}
        for fname in self.rollout_configs:
            entries[fname] = self._tick_single(
                flag_name=fname,
                metric_values=metric_values,
                evaluations=evaluations,
                now=now,
            )

        # Return primary flag entry for backwards compatibility
        return entries[self.config.flag_name]

    def _tick_single(
        self,
        flag_name: str,
        metric_values: Optional[Dict[str, float]] = None,
        evaluations: Optional[List[Any]] = None,
        now: Optional[float] = None,
    ) -> AuditEntry:
        rc = self.rollout_configs.get(flag_name)
        if not rc:
            raise ValueError(f"Unknown flag rollout '{flag_name}'")
        sm = self.state_machines[flag_name]

        with self._reconcile_lock:
            # 1. Check for active drift condition: hold immediately
            if self.drift_flags.get(flag_name, {}).get("detected"):
                drift_info = self.drift_flags[flag_name]
                reason = f"[DRIFT WARNING] Rollout held due to state drift: {drift_info.get('message')}"
                entry = self.audit_logger.record(
                    flag_name=flag_name,
                    from_state=sm.current_state,
                    to_state=sm.current_state,
                    decision=RolloutDecision.HOLD.value,
                    reason=reason,
                    numbers={"drift": drift_info, "state": sm.current_state},
                )
                return entry

            # 2. Gather metrics
            if evaluations is None and hasattr(self.metric_provider, "evaluate_all"):
                evaluations = self.metric_provider.evaluate_all(rc.guardrails)
            elif metric_values is None and callable(self.metric_provider):
                res = self.metric_provider()
                if isinstance(res, list):
                    evaluations = res
                elif isinstance(res, dict):
                    metric_values = res

            if evaluations:
                if isinstance(self.latest_evaluations, dict):
                    self.latest_evaluations[flag_name] = evaluations
                else:
                    self.latest_evaluations = evaluations
                # If experiment results present, persist to SQLite
                for ev in evaluations:
                    details = getattr(ev, "details", {}) or {}
                    if "as_of" in details or getattr(ev, "name", "").startswith("checkout_ab"):
                        self.storage.save_experiment_results(flag_name, details, now=now)

            prev_state = sm.current_state
            prev_pct = sm.current_percentage
            new_state, decision, reason, numbers = sm.evaluate(
                metric_values=metric_values,
                evaluations=evaluations,
                now=now,
            )

            # Notify metric provider when a new stage soak begins
            if new_state != prev_state and hasattr(self.metric_provider, "notify_stage_started"):
                try:
                    self.metric_provider.notify_stage_started(new_state, guardrails=rc.guardrails)
                except Exception:
                    pass

            stage_idx = sm.current_stage_index
            should_run = self.idempotency_manager.should_execute(
                flag_name=flag_name,
                decision=decision.value,
                to_state=new_state,
                stage_index=stage_idx,
            )

            if should_run:
                pct = sm.current_percentage
                # In-flight grace window measures real-time I/O latency for reconciler/webhooks
                self.in_flight_transitions[flag_name] = {
                    "previous_percentage": prev_pct,
                    "target_percentage": pct,
                    "expires_at": time.time() + self.in_flight_grace_seconds,
                    "source": "guardian",
                }
                # Persist updated rollout state and audit record to SQLite FIRST before remote Flagsmith write
                self.storage.save_rollout_state(
                    flag_name=flag_name,
                    current_state=new_state,
                    current_stage_index=sm.current_stage_index,
                    stage_entered_at=sm.stage_entered_at,
                    current_percentage=pct,
                    is_approved=sm.is_approved,
                    drift_detected=False,
                    drift_details=None,
                    now=now,
                )
                entry = self.audit_logger.record(
                    flag_name=flag_name,
                    from_state=prev_state,
                    to_state=new_state,
                    decision=decision.value,
                    reason=reason,
                    numbers=numbers,
                )

                if decision in (RolloutDecision.ADVANCE, RolloutDecision.PROMOTED):
                    self.storage.record_write(flag_name, pct, written_at=now)
                    self.flagsmith_client.set_percentage_rollout(flag_name, pct)
                elif decision == RolloutDecision.ROLLBACK:
                    self.storage.record_write(flag_name, 0.0, written_at=now)
                    self.flagsmith_client.rollback_to_zero(flag_name, reason=reason)
                    # --- Incident Intelligence: record this automated rollback ---
                    try:
                        from guardian.incident import Incident
                        metric_name = numbers.get("metric_name")
                        observed_val = numbers.get("metric_value")
                        threshold = numbers.get("threshold")
                        incident = Incident(
                            flag_name=flag_name,
                            trigger_type="GUARDRAIL_BREACH",
                            action_taken="ROLLBACK",
                            explanation=(
                                f"Guardrail breach detected: {metric_name or 'unknown metric'} "
                                f"observed {observed_val} (threshold {threshold}). "
                                f"Guardian automatically rolled back {flag_name} from "
                                f"{numbers.get('rolled_back_from_percentage', prev_state)}% to 0%."
                            ),
                            metric_name=metric_name,
                            observed_value=float(observed_val) if observed_val is not None else None,
                            threshold=float(threshold) if threshold is not None else None,
                            state_at_trigger=prev_state,
                            percentage_at_trigger=numbers.get("rolled_back_from_percentage"),
                            triggered_at=now if now is not None else time.time(),
                        )
                        self.storage.save_incident(incident)
                    except Exception as inc_err:
                        logger.warning(f"Could not save incident record: {inc_err}")

                self.idempotency_manager.mark_executed(
                    flag_name=flag_name,
                    decision=decision.value,
                    to_state=new_state,
                    stage_index=stage_idx,
                )
                if decision.value != "HOLD":
                    self.alert_dispatcher.dispatch(entry)
                return entry
            else:
                # Persist updated rollout state to SQLite
                self.storage.save_rollout_state(
                    flag_name=flag_name,
                    current_state=new_state,
                    current_stage_index=sm.current_stage_index,
                    stage_entered_at=sm.stage_entered_at,
                    current_percentage=sm.current_percentage,
                    is_approved=sm.is_approved,
                    drift_detected=False,
                    drift_details=None,
                    now=now,
                )
                entry = self.audit_logger.record(
                    flag_name=flag_name,
                    from_state=prev_state,
                    to_state=new_state,
                    decision=decision.value,
                    reason=f"[Idempotent Re-evaluation] {reason}" if prev_state == new_state and decision.value != "HOLD" else reason,
                    numbers=numbers,
                )
                return entry

    # -------------------------------------------------------------------------
    # Human Operator Actions
    # -------------------------------------------------------------------------
    def check_approval_eligibility(self, flag_name: Optional[str] = None) -> tuple[bool, Optional[str]]:
        flag_name = flag_name or self.config.flag_name
        rc = self.rollout_configs.get(flag_name, self.config)
        if isinstance(self.latest_evaluations, list):
            evaluations = self.latest_evaluations
        elif isinstance(self.latest_evaluations, dict):
            evaluations = self.latest_evaluations.get(flag_name, [])
        else:
            evaluations = []

        if not evaluations and hasattr(self.metric_provider, "evaluate_all"):
            evaluations = self.metric_provider.evaluate_all(rc.guardrails)
            if isinstance(self.latest_evaluations, dict):
                self.latest_evaluations[flag_name] = evaluations
            else:
                self.latest_evaluations = evaluations

        has_exp_in_evals = False
        for ev in evaluations:
            details = getattr(ev, "details", {}) or {}
            status_val = getattr(ev, "status", None)
            is_exp = (
                "as_of" in details
                or getattr(ev, "name", "").startswith("checkout_ab")
                or any(g.name == ev.name and g.metric_source == "flagsmith_experiment" for g in rc.guardrails)
            )
            if is_exp:
                has_exp_in_evals = True

            # 0. Error Check
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
                for g in rc.guardrails:
                    if g.name == ev.name and g.metric_source == "flagsmith_experiment":
                        chance_worse = ev.value
                        break

            if chance_worse is not None:
                chance_worse = round(float(chance_worse), 1)
                for g in rc.guardrails:
                    if g.name == ev.name and g.metric_source == "flagsmith_experiment":
                        if chance_worse > g.threshold:
                            return False, f"Approval blocked: Bayesian chance-to-be-worse ({chance_worse:.1f}%) exceeds configured limit ({g.threshold:.1f}%)."

            # 3b. Optional require_winner Check (chance_to_win >= 95% and CI interval above zero)
            require_winner = getattr(rc, "require_winner", False) or any(
                getattr(g, "require_winner", False) for g in rc.guardrails
            )
            if require_winner:
                cwin = details.get("chance_to_win")
                ci_low = details.get("ci_low")
                if cwin is not None and ci_low is not None:
                    cwin_val = float(cwin)
                    ci_low_val = float(ci_low)
                    if cwin_val < 0.95 or ci_low_val <= 0.0:
                        return False, (
                            f"Approval blocked: require_winner is enabled, but variation is not a decisive winner "
                            f"(chance to win {cwin_val * 100:.1f}% < 95.0% or CI low {ci_low_val:.3f} <= 0.000)."
                        )

            # 4. General Guardrail Breach
            if getattr(ev, "is_breached", False):
                return False, f"Approval blocked: Guardrail '{ev.name}' is breached: {getattr(ev, 'reason', '')}"

        # If evaluations did not include experiment data, check persisted experiment state in SQLite
        if not has_exp_in_evals:
            saved_exp = self.storage.load_experiment_results(flag_name)
            if saved_exp:
                if saved_exp.get("is_collecting_data"):
                    return False, "Approval blocked: Flagsmith is still collecting data (under 50 identities per arm or under 5 conversions). Statistical inference not yet available."
                srm_warning = saved_exp.get("srm_warning", False)
                srm_p_val = saved_exp.get("srm_p_value")
                if srm_warning or (srm_p_val is not None and srm_p_val < 0.001):
                    p_display = f"{srm_p_val:.6f}" if srm_p_val is not None else "< 0.001"
                    return False, f"Approval blocked: Sample Ratio Mismatch (SRM) detected (p-value {p_display} < 0.001). Traffic assignment is corrupted."
                cw = saved_exp.get("chance_to_be_worse")
                if cw is not None:
                    cw = round(float(cw), 1)
                    for g in rc.guardrails:
                        if g.metric_source == "flagsmith_experiment":
                            if cw > g.threshold:
                                return False, f"Approval blocked: Bayesian chance-to-be-worse ({cw:.1f}%) exceeds configured limit ({g.threshold:.1f}%)."
                require_winner = getattr(rc, "require_winner", False) or any(
                    getattr(g, "require_winner", False) for g in rc.guardrails
                )
                if require_winner:
                    cwin = saved_exp.get("chance_to_win")
                    ci_low = saved_exp.get("ci_low")
                    if cwin is not None and ci_low is not None:
                        cwin_val = float(cwin)
                        ci_low_val = float(ci_low)
                        if cwin_val < 0.95 or ci_low_val <= 0.0:
                            return False, (
                                f"Approval blocked: require_winner is enabled, but variation is not a decisive winner "
                                f"(chance to win {cwin_val * 100:.1f}% < 95.0% or CI low {ci_low_val:.3f} <= 0.000)."
                            )

        return True, None

    def approve(self, flag_name: Optional[str] = None, now: Optional[float] = None) -> AuditEntry:
        flag_name = flag_name or self.config.flag_name
        sm = self.state_machines.get(flag_name)
        if not sm:
            raise ValueError(f"Unknown flag rollout '{flag_name}'")

        if sm.current_state != RolloutState.AWAITING_APPROVAL.value:
            raise ValueError(
                f"Cannot approve rollout for '{flag_name}': Current state is '{sm.current_state}', "
                f"expected '{RolloutState.AWAITING_APPROVAL.value}'"
            )

        eligible, block_reason = self.check_approval_eligibility(flag_name=flag_name)
        if not eligible:
            raise ValueError(block_reason)

        sm.approve()
        entry = self.tick(flag_name=flag_name, now=now)
        return entry

    def manual_rollback(
        self,
        flag_name: Optional[str] = None,
        reason: str = "Manual operator rollback",
        now: Optional[float] = None,
    ) -> AuditEntry:
        flag_name = flag_name or self.config.flag_name
        sm = self.state_machines.get(flag_name)
        rc = self.rollout_configs.get(flag_name, self.config)
        if not sm:
            raise ValueError(f"Unknown flag rollout '{flag_name}'")

        prev_state = sm.current_state
        new_state, decision, dec_reason, numbers = sm.manual_rollback()

        should_run = self.idempotency_manager.should_execute(
            flag_name=flag_name,
            decision=decision.value,
            to_state=new_state,
            stage_index=-99,
        )

        if should_run:
            self.flagsmith_client.rollback_to_zero(flag_name, reason=reason)
            self.idempotency_manager.mark_executed(
                flag_name=flag_name,
                decision=decision.value,
                to_state=new_state,
                stage_index=-99,
            )

        self.storage.save_rollout_state(
            flag_name=flag_name,
            current_state=new_state,
            current_stage_index=sm.current_stage_index,
            stage_entered_at=sm.stage_entered_at,
            current_percentage=sm.current_percentage,
            is_approved=sm.is_approved,
            drift_detected=False,
            drift_details=None,
            now=now,
        )

        entry = self.audit_logger.record(
            flag_name=flag_name,
            from_state=prev_state,
            to_state=new_state,
            decision=decision.value,
            reason=f"{reason} ({dec_reason})",
            numbers=numbers,
        )

        if should_run:
            self.alert_dispatcher.dispatch(entry)

        return entry

    def reset(self, flag_name: Optional[str] = None) -> AuditEntry:
        """
        Resets rollout state back to PENDING (0%) and clears idempotency tracking in SQLite.
        Rejects re-arm if any guardrail is currently in a BREACH state.
        Also clears any active drift condition.
        """
        target_flags = [flag_name] if flag_name else list(self.rollout_configs.keys())
        last_entry = None

        for fname in target_flags:
            rc = self.rollout_configs[fname]
            sm = self.state_machines[fname]

            # Validate guardrails are not breaching before re-arming
            if hasattr(self.metric_provider, "evaluate_all"):
                evaluations = self.metric_provider.evaluate_all(rc.guardrails)
                self.latest_evaluations[fname] = evaluations
                for ev in evaluations:
                    if ev.is_breached:
                        val_str = f"{ev.value:.2f}%" if ev.value is not None else "unknown"
                        raise ValueError(
                            f"Cannot re-arm rollout: Guardrail '{ev.name}' is currently breaching "
                            f"(observed {val_str}). Resolve the underlying fault before re-arming: {ev.reason}"
                        )

            prev_state = sm.current_state
            sm = RolloutStateMachine(rc)
            self.state_machines[fname] = sm
            if fname == self.config.flag_name:
                self.state_machine = sm

            self.idempotency_manager.reset(flag_name=fname)
            self.drift_flags[fname] = {"detected": False}

            try:
                self.storage.record_write(fname, 0.0, written_at=time.time())
                self.flagsmith_client.set_percentage_rollout(fname, 0.0)
            except Exception as e:
                logger.warning(f"Could not reset Flagsmith rollout percentage for '{fname}': {e}")

            self.storage.save_rollout_state(
                flag_name=fname,
                current_state=sm.current_state,
                current_stage_index=sm.current_stage_index,
                stage_entered_at=sm.stage_entered_at,
                current_percentage=sm.current_percentage,
                is_approved=sm.is_approved,
                drift_detected=False,
                drift_details=None,
            )

            last_entry = self.audit_logger.record(
                flag_name=fname,
                from_state=prev_state,
                to_state=sm.current_state,
                decision="ADVANCE",
                reason=f"Rollout '{fname}' reset to initial PENDING by operator.",
                numbers={"percentage": 0.0},
            )

        return last_entry or self.audit_logger.get_latest()

    # -------------------------------------------------------------------------
    # Pause / Resume
    # -------------------------------------------------------------------------
    def pause(self, flag_name: Optional[str] = None) -> AuditEntry:
        """Pause a live rollout at its current stage without rolling back."""
        fname = flag_name or self.config.flag_name
        sm = self.state_machines.get(fname)
        if not sm:
            raise ValueError(f"Unknown flag '{fname}'")

        prev_state = sm.current_state
        new_state, decision, reason, numbers = sm.pause()

        self.storage.save_rollout_state(
            flag_name=fname,
            current_state=new_state,
            current_stage_index=sm.current_stage_index,
            stage_entered_at=sm.stage_entered_at,
            current_percentage=sm.current_percentage,
            is_approved=sm.is_approved,
            paused_from_state=prev_state,
        )
        return self.audit_logger.record(
            flag_name=fname,
            from_state=prev_state,
            to_state=new_state,
            decision=decision.value,
            reason=reason,
            numbers=numbers,
        )

    def resume(self, flag_name: Optional[str] = None) -> AuditEntry:
        """Resume a PAUSED rollout, restoring the state before pause."""
        fname = flag_name or self.config.flag_name
        sm = self.state_machines.get(fname)
        if not sm:
            raise ValueError(f"Unknown flag '{fname}'")

        prev_state = sm.current_state
        new_state, decision, reason, numbers = sm.resume()

        self.storage.save_rollout_state(
            flag_name=fname,
            current_state=new_state,
            current_stage_index=sm.current_stage_index,
            stage_entered_at=sm.stage_entered_at,
            current_percentage=sm.current_percentage,
            is_approved=sm.is_approved,
            paused_from_state=None,
        )
        return self.audit_logger.record(
            flag_name=fname,
            from_state=prev_state,
            to_state=new_state,
            decision=decision.value,
            reason=reason,
            numbers=numbers,
        )

    # -------------------------------------------------------------------------
    # Status Reporting
    # -------------------------------------------------------------------------
    def get_status(self, flag_name: Optional[str] = None, full: bool = False) -> Dict[str, Any]:
        now = time.time()
        rollouts_report: Dict[str, Any] = {}

        for fname, rc in self.rollout_configs.items():
            sm = self.state_machines[fname]
            if isinstance(self.latest_evaluations, list):
                evals = self.latest_evaluations
            elif isinstance(self.latest_evaluations, dict):
                evals = self.latest_evaluations.get(fname, [])
            else:
                evals = []
            guardrail_reports = []
            unit_by_name = {g.name: getattr(g, "unit", "%") for g in rc.guardrails}
            cfg_by_name = {g.name: g for g in rc.guardrails}
            if evals:
                for ev in evals:
                    cfg = cfg_by_name.get(ev.name)
                    guardrail_reports.append({
                        "name": ev.name,
                        "value": ev.value,
                        "threshold": cfg.threshold if cfg else None,
                        "comparison": cfg.comparison if cfg else None,
                        "metric_source": cfg.metric_source if cfg else "prometheus",
                        "unit": unit_by_name.get(ev.name, "ms" if "latency" in ev.name.lower() else "%"),
                        "sample_size": ev.sample_size,
                        "status": ev.status.value if hasattr(ev.status, "value") else str(ev.status),
                        "is_breached": getattr(ev, "is_breached", False),
                        "reason": getattr(ev, "reason", ""),
                        "details": getattr(ev, "details", {}),
                    })
            else:
                for g in rc.guardrails:
                    guardrail_reports.append({
                        "name": g.name,
                        "threshold": g.threshold,
                        "comparison": g.comparison,
                        "unit": getattr(g, "unit", "%"),
                        "metric_source": g.metric_source,
                        "status": "WAITING_FIRST_TICK",
                        "value": None,
                        "sample_size": None,
                        "is_breached": False,
                        "reason": "Awaiting initial metric evaluation",
                    })

            soak_elapsed = 0.0
            if sm.stage_entered_at:
                soak_elapsed = max(0.0, round(now - sm.stage_entered_at, 1))

            # Build experiment results summary if present
            experiment_summary = None
            # Check latest evaluations or load last experiment results from SQLite
            saved_exp = self.storage.load_experiment_results(fname)
            for ev in evals:
                details = getattr(ev, "details", {}) or {}
                is_exp = (
                    "as_of" in details
                    or getattr(ev, "name", "").startswith("checkout_ab")
                    or any(g.name == ev.name and g.metric_source == "flagsmith_experiment" for g in rc.guardrails)
                )
                if is_exp:
                    srm_val = details.get("srm_p_value")
                    srm_warn = bool(details.get("srm_warning", False) or (srm_val is not None and srm_val < 0.001))
                    chance_worse = details.get("chance_to_be_worse")
                    if chance_worse is None and getattr(ev, "value", None) is not None:
                        chance_worse = ev.value

                    eligible, block_reason = self.check_approval_eligibility(flag_name=fname)
                    cw = round(float(chance_worse), 1) if chance_worse is not None else None
                    cwin = float(details["chance_to_win"]) if details.get("chance_to_win") is not None else None
                    lift_val = float(details["lift"]) if details.get("lift") is not None else None
                    experiment_summary = {
                        "name": "Checkout A/B Experiment",
                        "as_of": details.get("as_of"),
                        "is_collecting_data": details.get("is_collecting_data", False),
                        "chance_to_be_worse": cw,
                        "chance_to_win": cwin,
                        "lift": lift_val,
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

            if not experiment_summary and saved_exp:
                eligible, block_reason = self.check_approval_eligibility(flag_name=fname)
                cw = round(float(saved_exp["chance_to_be_worse"]), 1) if saved_exp.get("chance_to_be_worse") is not None else None
                cwin = float(saved_exp["chance_to_win"]) if saved_exp.get("chance_to_win") is not None else None
                lift_val = float(saved_exp["lift"]) if saved_exp.get("lift") is not None else None
                srm_val = saved_exp.get("srm_p_value")
                srm_warn = bool(saved_exp.get("srm_warning", False) or (srm_val is not None and srm_val < 0.001))
                experiment_summary = {
                    "name": "Checkout A/B Experiment",
                    "as_of": saved_exp.get("as_of"),
                    "is_collecting_data": saved_exp.get("is_collecting_data", False),
                    "chance_to_be_worse": cw,
                    "chance_to_win": cwin,
                    "lift": lift_val,
                    "ci_low": saved_exp.get("ci_low"),
                    "ci_high": saved_exp.get("ci_high"),
                    "total_samples": saved_exp.get("total_samples"),
                    "control_n": saved_exp.get("control_n"),
                    "variation_n": saved_exp.get("variation_n"),
                    "control_conversions": saved_exp.get("control_conversions"),
                    "variation_conversions": saved_exp.get("variation_conversions"),
                    "srm_p_value": srm_val,
                    "srm_warning": srm_warn,
                    "status": saved_exp.get("status", "HEALTHY"),
                    "is_eligible_for_approval": eligible,
                    "block_reason": block_reason,
                    "demo_shortcut": bool(saved_exp.get("demo_shortcut", False) or os.environ.get("DEMO_FORCE_RECALC", "").lower() in ("1", "true", "yes")),
                }

            eligible, block_reason = self.check_approval_eligibility(flag_name=fname)
            rollout_data = {
                "flag_name": fname,
                "state": sm.current_state,
                "percentage": sm.current_percentage,
                "stage_index": sm.current_stage_index,
                "is_approved": sm.is_approved,
                "is_eligible_for_approval": eligible,
                "approval_block_reason": block_reason,
                "stages": rc.stages,
                "soak_time_seconds": rc.soak_time_seconds,
                "soak_elapsed_seconds": soak_elapsed,
                "drift": self.drift_flags.get(fname, {"detected": False}),
                "guardrails": guardrail_reports,
                "experiment": experiment_summary,
            }
            rollouts_report[fname] = rollout_data

        target_flag = flag_name or self.config.flag_name
        primary_report = rollouts_report.get(target_flag, rollouts_report[self.config.flag_name])

        if full:
            timeline_entries = self.storage.get_audit_entries(order="ASC", limit=None)
        else:
            timeline_entries = list(reversed(self.storage.get_audit_entries(order="DESC", limit=20)))

        # Build combined response
        return {
            "rollouts": rollouts_report,
            "flag_name": primary_report["flag_name"],
            "state": primary_report["state"],
            "percentage": primary_report["percentage"],
            "stage_index": primary_report["stage_index"],
            "is_approved": primary_report["is_approved"],
            "is_eligible_for_approval": primary_report["is_eligible_for_approval"],
            "approval_block_reason": primary_report["approval_block_reason"],
            "stages": primary_report["stages"],
            "soak_time_seconds": primary_report["soak_time_seconds"],
            "soak_elapsed_seconds": primary_report["soak_elapsed_seconds"],
            "drift": primary_report["drift"],
            "slack_webhook_configured": bool(self.alert_dispatcher.slack_webhook_url),
            "guardrails": primary_report["guardrails"],
            "experiment": primary_report["experiment"],
            "timeline": timeline_entries,
            "recent_audit_entries": timeline_entries[-5:] if timeline_entries else [],
        }
