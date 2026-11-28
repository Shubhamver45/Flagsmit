"""
Push Metric Source.
Stores ingested request/error counts in a thread-safe rolling time window
so Guardian can enforce error-rate guardrails without Prometheus.
"""

from __future__ import annotations
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from guardian.metrics.base import MetricEvaluation, MetricStatus


class PushMetricSource:
    """In-memory rolling-window error-rate metric source for pushed app counts."""

    def __init__(self, clock: Callable[[], float] = time.time, max_retention_seconds: float = 3600.0):
        self._clock = clock
        self._max_retention_seconds = max_retention_seconds
        self._lock = threading.Lock()
        # metric_name -> list of (timestamp, requests, errors)
        self._samples: Dict[str, List[Tuple[float, int, int]]] = {}

    def ingest(
        self,
        metric: str,
        requests: int,
        errors: int,
        timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record a batch of request/error counts for a metric."""
        if not isinstance(metric, str) or not metric.strip():
            raise ValueError("metric must be a non-empty string")
        if isinstance(requests, bool) or not isinstance(requests, int) or requests < 0:
            raise ValueError("requests must be a non-negative integer")
        if isinstance(errors, bool) or not isinstance(errors, int) or errors < 0:
            raise ValueError("errors must be a non-negative integer")
        if errors > requests:
            raise ValueError("errors cannot exceed requests")

        ts = float(timestamp) if timestamp is not None else self._clock()
        key = metric.strip()
        cutoff = self._clock() - self._max_retention_seconds

        with self._lock:
            bucket = self._samples.setdefault(key, [])
            bucket.append((ts, requests, errors))
            # Prune stale history beyond retention window
            self._samples[key] = [entry for entry in bucket if entry[0] >= cutoff]

        return {
            "status": "ingested",
            "metric": key,
            "requests": requests,
            "errors": errors,
            "timestamp": ts,
        }

    def evaluate(self, guardrail: Any) -> MetricEvaluation:
        """Compute error rate over the guardrail's rolling window and check threshold."""
        metric_key = (
            getattr(guardrail, "metric", None)
            or getattr(guardrail, "query", None)
            or guardrail.name
        )
        window_seconds = float(getattr(guardrail, "window_seconds", 60.0) or 60.0)
        min_samples = int(getattr(guardrail, "min_samples", 30))

        now = self._clock()
        cutoff = now - window_seconds

        with self._lock:
            entries = [e for e in self._samples.get(metric_key, []) if e[0] >= cutoff]

        total_requests = sum(e[1] for e in entries)
        total_errors = sum(e[2] for e in entries)

        if total_requests < min_samples:
            return MetricEvaluation(
                name=guardrail.name,
                value=None,
                sample_size=total_requests,
                is_breached=False,
                status=MetricStatus.INSUFFICIENT_DATA,
                reason=f"Insufficient sample size ({total_requests} < minimum {min_samples}); holding stage",
                details={
                    "metric": metric_key,
                    "min_samples": min_samples,
                    "current_samples": total_requests,
                    "window_seconds": window_seconds,
                },
            )

        error_rate = (total_errors / total_requests) * 100.0
        is_breached = guardrail.evaluate(error_rate)
        status = MetricStatus.BREACH if is_breached else MetricStatus.HEALTHY
        reason = (
            f"Metric value {error_rate:.2f}% {guardrail.comparison} threshold {guardrail.threshold}% (BREACH)"
            if is_breached
            else f"Metric value {error_rate:.2f}% healthy (threshold: {guardrail.comparison} {guardrail.threshold}%)"
        )

        return MetricEvaluation(
            name=guardrail.name,
            value=error_rate,
            sample_size=total_requests,
            is_breached=is_breached,
            status=status,
            reason=reason,
            details={
                "metric": metric_key,
                "requests": total_requests,
                "errors": total_errors,
                "window_seconds": window_seconds,
                "threshold": guardrail.threshold,
                "comparison": guardrail.comparison,
            },
        )
