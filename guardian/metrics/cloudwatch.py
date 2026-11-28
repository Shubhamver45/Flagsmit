"""
AWS CloudWatch Metrics Adapter.
Evaluates guardrails against CloudWatch GetMetricStatistics.
Requires optional boto3 dependency (`pip install flagsmith-guardian[aws]`).
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any, Dict, Optional

from guardian.metrics.base import MetricEvaluation, MetricStatus


class CloudWatchMetricSource:
    def __init__(
        self,
        region_name: Optional[str] = None,
        client: Optional[Any] = None,
    ):
        self.region_name = region_name or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        import boto3
        self._client = boto3.client("cloudwatch", region_name=self.region_name)
        return self._client

    @staticmethod
    def _parse_query(raw_query: str) -> Dict[str, Any]:
        raw = raw_query.strip()
        if raw.startswith("{"):
            return json.loads(raw)
        if "/" in raw:
            ns, metric = raw.rsplit("/", 1)
            return {"Namespace": ns, "MetricName": metric, "Statistic": "Average", "Period": 60}
        raise ValueError("CloudWatch query must be JSON or 'Namespace/MetricName'")

    def evaluate(self, guardrail: Any) -> MetricEvaluation:
        raw_query = getattr(guardrail, "query", None)
        if not raw_query:
            return MetricEvaluation(
                name=guardrail.name,
                value=None,
                sample_size=0,
                is_breached=False,
                status=MetricStatus.ERROR,
                reason=f"Guardrail '{guardrail.name}' missing CloudWatch 'query'",
            )

        try:
            spec = self._parse_query(raw_query)
            cw = self._get_client()
        except ImportError:
            return MetricEvaluation(
                name=guardrail.name,
                value=None,
                sample_size=0,
                is_breached=False,
                status=MetricStatus.ERROR,
                reason="boto3 not installed; install with `pip install flagsmith-guardian[aws]`",
            )
        except Exception as e:
            return MetricEvaluation(
                name=guardrail.name,
                value=None,
                sample_size=0,
                is_breached=False,
                status=MetricStatus.ERROR,
                reason=f"CloudWatch client/query error: {e}",
            )

        window = float(getattr(guardrail, "window_seconds", 60.0) or 60.0)
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(seconds=max(60, int(window)))
        stat = spec.get("Statistic", "Average")
        period = int(spec.get("Period", 60))
        stats = list({stat, "SampleCount"})

        try:
            resp = cw.get_metric_statistics(
                Namespace=spec["Namespace"],
                MetricName=spec["MetricName"],
                Dimensions=spec.get("Dimensions", []),
                StartTime=start_time,
                EndTime=end_time,
                Period=period,
                Statistics=stats,
            )
            datapoints = sorted(resp.get("Datapoints") or [], key=lambda d: d.get("Timestamp", end_time))
            if not datapoints:
                return MetricEvaluation(
                    name=guardrail.name,
                    value=None,
                    sample_size=0,
                    is_breached=False,
                    status=MetricStatus.INSUFFICIENT_DATA,
                    reason=f"CloudWatch returned no datapoints for '{guardrail.name}'",
                )

            latest = datapoints[-1]
            sample_size = int(sum(float(d.get("SampleCount", 0)) for d in datapoints))
            if sample_size < guardrail.min_samples:
                return MetricEvaluation(
                    name=guardrail.name,
                    value=None,
                    sample_size=sample_size,
                    is_breached=False,
                    status=MetricStatus.INSUFFICIENT_DATA,
                    reason=f"Insufficient CloudWatch samples ({sample_size} < {guardrail.min_samples}); holding rollout",
                )

            value = float(latest[stat])
            breached = guardrail.evaluate(value)
            status = MetricStatus.BREACH if breached else MetricStatus.HEALTHY
            unit = getattr(guardrail, "unit", "%")
            reason = (
                f"CloudWatch metric {value:.2f}{unit} breached {guardrail.comparison} {guardrail.threshold}{unit}"
                if breached
                else f"CloudWatch metric {value:.2f}{unit} healthy (threshold: {guardrail.comparison} {guardrail.threshold}{unit})"
            )
            return MetricEvaluation(
                name=guardrail.name,
                value=value,
                sample_size=sample_size,
                is_breached=breached,
                status=status,
                reason=reason,
                details={"namespace": spec["Namespace"], "metric": spec["MetricName"], "value": value},
            )
        except Exception as e:
            return MetricEvaluation(
                name=guardrail.name,
                value=None,
                sample_size=0,
                is_breached=False,
                status=MetricStatus.ERROR,
                reason=f"CloudWatch query failed: {e}",
            )
