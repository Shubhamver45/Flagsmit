"""
Flagsmith Experiment Results Metric Source.
Extracts Bayesian guardrail metrics (chance-to-be-worse), checks SRM warnings,
respects the 300-second refresh cooldown, and enforces minimum sample sizes.
"""

from __future__ import annotations
import logging
import time
from typing import Any, Dict, Optional
import requests

from guardian.metrics.base import MetricEvaluation, MetricStatus

logger = logging.getLogger(__name__)

# Minimum cooldown between refresh calls enforced by Flagsmith Admin API
REFRESH_COOLDOWN_SECONDS = 300.0


class FlagsmithExperimentMetricSource:
    def __init__(
        self,
        api_url: str,
        admin_api_key: str,
        environment_key: str,
        session: Optional[Any] = None,
    ):
        api_url = api_url.rstrip("/")
        if not api_url.endswith("/api/v1"):
            api_url = f"{api_url}/api/v1"
        self.api_url = api_url
        self.admin_api_key = admin_api_key
        self.environment_key = environment_key
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Api-Key {self.admin_api_key}",
            "Content-Type": "application/json",
        })
        self._last_refresh_requested: Dict[int, float] = {}
        self._cached_results: Dict[int, Dict[str, Any]] = {}

    def notify_stage_started(self, stage_name: str, guardrails: Optional[List[Any]] = None) -> None:
        """
        Respects the 5-minute results refresh limit:
        Called once at the start of a stage's soak window to trigger a background
        results recalculation if the 300-second cooldown has elapsed.
        """
        exp_ids = set()
        if guardrails:
            for g in guardrails:
                eid = getattr(g, "experiment_id", None)
                if eid:
                    exp_ids.add(eid)
        for eid in exp_ids:
            self._maybe_trigger_refresh(eid)

    def _resolve_experiment_id(self, guardrail: Any) -> Optional[int]:
        eid = getattr(guardrail, "experiment_id", None)
        if eid:
            return eid
        try:
            url = f"{self.api_url}/environments/{self.environment_key}/experiments/"
            resp = self.session.get(url, timeout=5)
            if resp.ok:
                results = resp.json().get("results", [])
                for exp in results:
                    if exp.get("status") == "running":
                        return exp["id"]
                if results:
                    return results[0]["id"]
        except Exception as e:
            logger.debug(f"Could not auto-resolve experiment ID: {e}")
        return None

    def evaluate(self, guardrail: Any) -> MetricEvaluation:
        experiment_id = self._resolve_experiment_id(guardrail)
        if not experiment_id:
            return MetricEvaluation(
                name=guardrail.name,
                value=None,
                sample_size=0,
                is_breached=False,
                status=MetricStatus.INSUFFICIENT_DATA,
                reason="No active experiment found in Flagsmith environment; holding stage",
                details={"is_collecting_data": True},
            )

        min_samples = getattr(guardrail, "min_samples", 50)
        action_on_srm = getattr(guardrail, "action_on_srm", "ROLLBACK").upper()  # ROLLBACK or HOLD

        try:
            # 1. Trigger background refresh if 300s cooldown has passed
            self._maybe_trigger_refresh(experiment_id)

            # 2. Fetch current experiment results
            res_data = self._fetch_results(experiment_id)
            if not res_data or not res_data.get("results") or not res_data["results"].get("payload"):
                return MetricEvaluation(
                    name=guardrail.name,
                    value=None,
                    sample_size=0,
                    is_breached=False,
                    status=MetricStatus.INSUFFICIENT_DATA,
                    reason="Flagsmith is still collecting data (no experiment results computed yet); holding stage",
                    details={"experiment_id": experiment_id, "is_collecting_data": True},
                )

            as_of = res_data["results"].get("as_of")
            payload = res_data["results"]["payload"]

            # 3. SRM (Sample Ratio Mismatch) Warning Check
            srm_p_value = payload.get("srm_p_value")
            srm_warning = (srm_p_value is not None and srm_p_value < 0.001)
            if srm_warning:
                is_breached = (action_on_srm == "ROLLBACK")
                status = MetricStatus.SRM_BREACH if not is_breached else MetricStatus.BREACH
                reason = (
                    f"Sample Ratio Mismatch warning! Chi-squared p-value {srm_p_value:.6f} < 0.001. "
                    f"Traffic assignment corrupt. Action: {action_on_srm}."
                )
                return MetricEvaluation(
                    name=guardrail.name,
                    value=None,
                    sample_size=0,
                    is_breached=is_breached,
                    status=status,
                    reason=reason,
                    details={
                        "as_of": as_of,
                        "srm_p_value": srm_p_value,
                        "srm_warning": True,
                        "action": action_on_srm,
                    },
                )

            # 4. Find the target metric in results payload
            metrics_list = payload.get("metrics", [])
            target_metric = None
            for m in metrics_list:
                if m.get("metric_id") == getattr(guardrail, "metric_id", None) or len(metrics_list) == 1:
                    target_metric = m
                    break

            if not target_metric:
                return MetricEvaluation(
                    name=guardrail.name,
                    value=None,
                    sample_size=0,
                    is_breached=False,
                    status=MetricStatus.INSUFFICIENT_DATA,
                    reason=f"Metric not found in experiment {experiment_id} results payload",
                    details={"as_of": as_of, "is_collecting_data": True},
                )

            # 5. Check variants & collecting data threshold
            # Flagsmith requires at least 50 identities and 5 conversions per arm
            variants = target_metric.get("variants", {})
            total_samples = sum(v.get("n", 0) for v in variants.values())
            control_arm = variants.get("control", {})
            variation_arm = variants.get("variation", {}) or variants.get("treatment", {})
            if not variation_arm:
                for k, v in variants.items():
                    if k != "control":
                        variation_arm = v
                        break
            control_n = control_arm.get("n", 0)
            variation_n = variation_arm.get("n", 0)
            control_conv = control_arm.get("sum", 0.0)
            variation_conv = variation_arm.get("sum", 0.0)

            inference = target_metric.get("inference", {})
            treatment_inf = None
            for v_key, inf in inference.items():
                if v_key != "control" and inf:
                    treatment_inf = inf
                    break

            is_collecting = (
                control_n < 50
                or variation_n < 50
                or control_conv < 5
                or variation_conv < 5
                or not treatment_inf
                or "chance_to_win" not in treatment_inf
            )

            if is_collecting:
                return MetricEvaluation(
                    name=guardrail.name,
                    value=None,
                    sample_size=total_samples,
                    is_breached=False,
                    status=MetricStatus.INSUFFICIENT_DATA,
                    reason=(
                        f"Flagsmith is collecting data ({control_n} control, {variation_n} variation identities; "
                        f"{int(control_conv)} control, {int(variation_conv)} variation conversions). "
                        f"Minimum 50 identities and 5 conversions per arm required."
                    ),
                    details={
                        "as_of": as_of,
                        "is_collecting_data": True,
                        "control_n": control_n,
                        "variation_n": variation_n,
                        "control_conversions": control_conv,
                        "variation_conversions": variation_conv,
                        "total_samples": total_samples,
                        "srm_p_value": srm_p_value,
                        "srm_warning": False,
                    },
                )

            # 6. Extract Chance-to-be-Worse from real Flagsmith Bayesian inference
            chance_to_win = float(treatment_inf["chance_to_win"])
            chance_to_be_worse = round((1.0 - chance_to_win) * 100.0, 2)  # Percentage 0 - 100%

            # 7. Evaluate against threshold
            breached = guardrail.evaluate(chance_to_be_worse)
            action = getattr(guardrail, "action", getattr(guardrail, "action_on_srm", "ROLLBACK")).upper()
            is_breached = breached and (action == "ROLLBACK")
            status = MetricStatus.BREACH if breached else MetricStatus.HEALTHY
            action_tag = "BREACH" if is_breached else "HOLD"
            reason = (
                f"Chance-to-be-worse {chance_to_be_worse:.1f}% {guardrail.comparison} threshold {guardrail.threshold}% ({action_tag})"
                if breached
                else f"Chance-to-be-worse {chance_to_be_worse:.1f}% healthy (threshold: {guardrail.comparison} {guardrail.threshold}%)"
            )

            return MetricEvaluation(
                name=guardrail.name,
                value=chance_to_be_worse,
                sample_size=total_samples,
                is_breached=is_breached,
                status=status,
                reason=reason,
                details={
                    "as_of": as_of,
                    "is_collecting_data": False,
                    "chance_to_win": chance_to_win,
                    "chance_to_be_worse": chance_to_be_worse,
                    "lift": treatment_inf.get("lift"),
                    "ci_low": treatment_inf.get("ci_low"),
                    "ci_high": treatment_inf.get("ci_high"),
                    "total_samples": total_samples,
                    "control_n": control_n,
                    "variation_n": variation_n,
                    "control_conversions": control_conv,
                    "variation_conversions": variation_conv,
                    "srm_p_value": srm_p_value,
                    "srm_warning": False,
                },
            )

        except Exception as e:
            logger.error(f"Flagsmith experiment metric evaluation failed for {guardrail.name}: {e}")
            return MetricEvaluation(
                name=guardrail.name,
                value=None,
                sample_size=0,
                is_breached=False,
                status=MetricStatus.ERROR,
                reason=f"Flagsmith experiment error: {str(e)}",
            )

    def _maybe_trigger_refresh(self, experiment_id: int) -> None:
        now = time.time()
        last_req = self._last_refresh_requested.get(experiment_id, 0.0)
        if now - last_req >= REFRESH_COOLDOWN_SECONDS:
            url = f"{self.api_url}/environments/{self.environment_key}/experiments/{experiment_id}/results/refresh/"
            try:
                resp = self.session.post(url, timeout=5)
                if resp.status_code in (202, 200):
                    self._last_refresh_requested[experiment_id] = now
                elif resp.status_code == 429:
                    # Respect rate limit without throwing
                    self._last_refresh_requested[experiment_id] = now
            except Exception as e:
                logger.debug(f"Refresh trigger error: {e}")

    def _fetch_results(self, experiment_id: int) -> Dict[str, Any]:
        url = f"{self.api_url}/environments/{self.environment_key}/experiments/{experiment_id}/results/"
        resp = self.session.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        self._cached_results[experiment_id] = data
        return data
