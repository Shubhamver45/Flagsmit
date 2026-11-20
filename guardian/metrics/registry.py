"""
Unified Metric Source Provider and Dispatcher.
Routes guardrail evaluations to Prometheus, Flagsmith Experiment, or mock providers.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional

from guardian.metrics.base import MetricEvaluation, MetricSourceProtocol, MetricStatus


class CompositeMetricProvider:
    def __init__(self):
        self._sources: Dict[str, MetricSourceProtocol] = {}

    def register_source(self, name: str, source: MetricSourceProtocol) -> None:
        self._sources[name.lower()] = source

    def evaluate_guardrail(self, guardrail: Any) -> MetricEvaluation:
        source_name = guardrail.metric_source.lower()
        source = self._sources.get(source_name)
        if not source:
            return MetricEvaluation(
                name=guardrail.name,
                value=None,
                sample_size=0,
                is_breached=False,
                status=MetricStatus.ERROR,
                reason=f"Unknown metric_source: '{guardrail.metric_source}'. Available: {list(self._sources.keys())}",
            )
        return source.evaluate(guardrail)

    def evaluate_all(self, guardrails: List[Any]) -> List[MetricEvaluation]:
        return [self.evaluate_guardrail(g) for g in guardrails]
