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
        self.api_url = api_url.rstrip("/")
        self.admin_api_key = admin_api_key
        self.environment_key = environment_key
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Api-Key {self.admin_api_key}",
            "Content-Type": "application/json",
        })
        self._last_refresh_requested: Dict[int, float] = {}
        self._cached_results: Dict[int, Dict[str, Any]] = {}

    def evaluate(self, guardrail: Any) -> MetricEvaluation:
        experiment_id = getattr(guardrail, "experiment_id", None)
        if not experiment_id:
            return MetricEvaluation(
                name=guardrail.name,
                value=None,
                sample_size=0,
                is_breached=False,
                status=MetricStatus.ERROR,
                reason=f"Guardrail '{guardrail.name}' requires experiment_id for Flagsmith Experiment source",
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
                    reason="Experiment results not yet computed by warehouse; holding stage",
                    details={"experiment_id": experiment_id},
                )

            payload = res_data["results"]["payload"]

            # 3. SRM (Sample Ratio Mismatch) Warning Check
            srm_p_value = payload.get("srm_p_value")
            if srm_p_value is not None and srm_p_value < 0.001:
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
                    details={"srm_p_value": srm_p_value, "action": action_on_srm},
                )

            # 4. Find the target metric in results payload
            metrics_list = payload.get("metrics", [])
            target_metric = None
            for m in metrics_list:
                # Match by metric_id or name
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
                )

            # 5. Enforce Minimum Sample Size
            variants = target_metric.get("variants", {})
            total_samples = sum(v.get("n", 0) for v in variants.values())
            if total_samples < min_samples:
                return MetricEvaluation(
                    name=guardrail.name,
                    value=None,
                    sample_size=total_samples,
                    is_breached=False,
                    status=MetricStatus.INSUFFICIENT_DATA,
                    reason=f"Insufficient sample size ({total_samples} < minimum {min_samples}); holding stage",
                    details={"total_samples": total_samples, "min_samples": min_samples},
                )

            # 6. Extract Chance-to-be-Worse
            inference = target_metric.get("inference", {})
            # Look for the treatment variant inference (non-control)
            treatment_inf = None
            for v_key, inf in inference.items():
                if v_key != "control" and inf:
                    treatment_inf = inf
                    break

            if not treatment_inf or "chance_to_win" not in treatment_inf:
                return MetricEvaluation(
                    name=guardrail.name,
                    value=None,
                    sample_size=total_samples,
                    is_breached=False,
                    status=MetricStatus.INSUFFICIENT_DATA,
                    reason="Inference statistical calculations not yet available; holding",
                )

            chance_to_win = float(treatment_inf["chance_to_win"])
            # Chance to be worse is the posterior probability that treatment degrades the metric
            chance_to_be_worse = (1.0 - chance_to_win) * 100.0  # Percentage 0 - 100%

            # 7. Evaluate against threshold
            is_breached = guardrail.evaluate(chance_to_be_worse)
            status = MetricStatus.BREACH if is_breached else MetricStatus.HEALTHY
            reason = (
                f"Chance-to-be-worse {chance_to_be_worse:.1f}% {guardrail.comparison} threshold {guardrail.threshold}% (BREACH)"
                if is_breached
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
                    "chance_to_win": chance_to_win,
                    "chance_to_be_worse": chance_to_be_worse,
                    "lift": treatment_inf.get("lift"),
                    "total_samples": total_samples,
                    "srm_p_value": srm_p_value,
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
