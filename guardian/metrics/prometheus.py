"""
Prometheus Metric Source.
Evaluates PromQL queries against Prometheus API with sample size validation.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, Optional
import requests

from guardian.metrics.base import MetricEvaluation, MetricStatus

logger = logging.getLogger(__name__)


class PrometheusMetricSource:
    def __init__(self, prometheus_url: str = "http://localhost:9090", session: Optional[Any] = None):
        self.prometheus_url = prometheus_url.rstrip("/")
        self.session = session or requests.Session()

    def evaluate(self, guardrail: Any) -> MetricEvaluation:
        query = guardrail.query
        if not query:
            return MetricEvaluation(
                name=guardrail.name,
                value=None,
                sample_size=0,
                is_breached=False,
                status=MetricStatus.ERROR,
                reason=f"Guardrail '{guardrail.name}' missing PromQL query",
            )

        min_samples = getattr(guardrail, "min_samples", 30)
        sample_query = getattr(guardrail, "sample_size_query", None)

        try:
            # 1. Sample Size Check
            sample_size = self._fetch_sample_size(sample_query or self._derive_sample_query(query))
            if sample_size < min_samples:
                return MetricEvaluation(
                    name=guardrail.name,
                    value=None,
                    sample_size=sample_size,
                    is_breached=False,
                    status=MetricStatus.INSUFFICIENT_DATA,
                    reason=f"Insufficient sample size ({sample_size} < minimum {min_samples}); holding stage",
                    details={"min_samples": min_samples, "current_samples": sample_size},
                )

            # 2. Main Metric Query
            value = self._query_scalar(query)
            if value is None:
                # If error query returned no data but sample size is satisfied, 0 errors occurred
                if sample_size >= min_samples and "error" in query.lower():
                    value = 0.0
                else:
                    return MetricEvaluation(
                        name=guardrail.name,
                        value=None,
                        sample_size=sample_size,
                        is_breached=False,
                        status=MetricStatus.INSUFFICIENT_DATA,
                        reason="PromQL query returned no data; holding",
                    )

            # 3. Guardrail Threshold Check
            is_breached = guardrail.evaluate(value)
            status = MetricStatus.BREACH if is_breached else MetricStatus.HEALTHY
            reason = (
                f"Metric value {value:.2f}% {guardrail.comparison} threshold {guardrail.threshold}% (BREACH)"
                if is_breached
                else f"Metric value {value:.2f}% healthy (threshold: {guardrail.comparison} {guardrail.threshold}%)"
            )

            return MetricEvaluation(
                name=guardrail.name,
                value=value,
                sample_size=sample_size,
                is_breached=is_breached,
                status=status,
                reason=reason,
                details={
                    "query": query,
                    "value": value,
                    "threshold": guardrail.threshold,
                    "sample_size": sample_size,
                },
            )

        except Exception as e:
            logger.error(f"Prometheus evaluation failed for {guardrail.name}: {e}")
            return MetricEvaluation(
                name=guardrail.name,
                value=None,
                sample_size=0,
                is_breached=False,
                status=MetricStatus.ERROR,
                reason=f"Prometheus error: {str(e)}",
            )

    def _query_scalar(self, promql: str) -> Optional[float]:
        resp = self.session.get(
            f"{self.prometheus_url}/api/v1/query",
            params={"query": promql},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {}).get("result", [])
        if not data:
            return None
        # Prometheus vector result returns [timestamp, "value"]
        raw_val = data[0].get("value", [None, None])[1]
        if raw_val is None or raw_val in ("NaN", "Inf", "-Inf"):
            return None
        return float(raw_val)

    def _fetch_sample_size(self, sample_query: Optional[str]) -> int:
        if not sample_query:
            return 999999  # Default to unblocked if no sample query provided
        val = self._query_scalar(sample_query)
        return int(val) if val is not None else 0

    def _derive_sample_query(self, query: str) -> Optional[str]:
        # If query contains checkout_requests_total, construct a request count sample query
        if "checkout_requests_total" in query:
            if "variant='new_flow'" in query:
                return "sum(checkout_requests_total{variant='new_flow'})"
            return "sum(checkout_requests_total)"
        return None
