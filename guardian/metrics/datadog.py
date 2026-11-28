"""
Datadog Metrics Adapter.
Evaluates guardrails against Datadog /api/v1/query timeseries data.
"""

from __future__ import annotations
import os
import time
from typing import Any, Optional
import requests

from guardian.metrics.base import MetricEvaluation, MetricStatus


class DatadogMetricSource:
    def __init__(
        self,
        api_key: Optional[str] = None,
        app_key: Optional[str] = None,
        site: Optional[str] = None,
        session: Optional[Any] = None,
        timeout: float = 5.0,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get("DD_API_KEY", "")
        self.app_key = app_key if app_key is not None else os.environ.get("DD_APP_KEY", "")
        self.site = (site or os.environ.get("DD_SITE") or "datadoghq.com").strip()
        self.session = session or requests.Session()
        self.timeout = timeout

    def _query_latest(self, query: str, window_seconds: float) -> Optional[float]:
        now = int(time.time())
        start = now - max(60, int(window_seconds))
        url = f"https://api.{self.site}/api/v1/query"
        headers = {
            "DD-API-KEY": self.api_key,
            "DD-APPLICATION-KEY": self.app_key,
        }
        resp = self.session.get(
            url,
            params={"from": start, "to": now, "query": query},
            headers=headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        series = payload.get("series") or []
        if not series:
            return None
        points = [pt[1] for pt in (series[0].get("pointlist") or []) if len(pt) >= 2 and pt[1] is not None]
        if not points:
            return None
        return float(points[-1])

    def evaluate(self, guardrail: Any) -> MetricEvaluation:
        if not self.api_key or not self.app_key:
            return MetricEvaluation(
                name=guardrail.name,
                value=None,
                sample_size=0,
                is_breached=False,
                status=MetricStatus.ERROR,
                reason="Datadog credentials missing (set DD_API_KEY and DD_APP_KEY)",
            )
        if not getattr(guardrail, "query", None):
            return MetricEvaluation(
                name=guardrail.name,
                value=None,
                sample_size=0,
                is_breached=False,
                status=MetricStatus.ERROR,
                reason=f"Guardrail '{guardrail.name}' missing Datadog 'query'",
            )

        window = float(getattr(guardrail, "window_seconds", 60.0) or 60.0)
        try:
            sample_size = getattr(guardrail, "min_samples", 30)
            if getattr(guardrail, "sample_size_query", None):
                s_val = self._query_latest(guardrail.sample_size_query, window)
                sample_size = int(s_val) if s_val is not None else 0
                if sample_size < guardrail.min_samples:
                    return MetricEvaluation(
                        name=guardrail.name,
                        value=None,
                        sample_size=sample_size,
                        is_breached=False,
                        status=MetricStatus.INSUFFICIENT_DATA,
                        reason=f"Insufficient Datadog samples ({sample_size} < {guardrail.min_samples}); holding rollout",
                    )

            value = self._query_latest(guardrail.query, window)
            if value is None:
                return MetricEvaluation(
                    name=guardrail.name,
                    value=None,
                    sample_size=sample_size,
                    is_breached=False,
                    status=MetricStatus.INSUFFICIENT_DATA,
                    reason=f"Datadog query returned no data points for '{guardrail.name}'",
                )

            breached = guardrail.evaluate(value)
            status = MetricStatus.BREACH if breached else MetricStatus.HEALTHY
            unit = getattr(guardrail, "unit", "%")
            reason = (
                f"Datadog metric {value:.2f}{unit} breached {guardrail.comparison} {guardrail.threshold}{unit}"
                if breached
                else f"Datadog metric {value:.2f}{unit} healthy (threshold: {guardrail.comparison} {guardrail.threshold}{unit})"
            )
            return MetricEvaluation(
                name=guardrail.name,
                value=value,
                sample_size=sample_size,
                is_breached=breached,
                status=status,
                reason=reason,
                details={"site": self.site, "query": guardrail.query, "value": value},
            )
        except Exception as e:
            return MetricEvaluation(
                name=guardrail.name,
                value=None,
                sample_size=0,
                is_breached=False,
                status=MetricStatus.ERROR,
                reason=f"Datadog query failed: {e}",
            )
