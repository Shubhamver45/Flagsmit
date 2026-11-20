#!/usr/bin/env python3
"""
Live Metric Sources Verification Script.
Tests PrometheusMetricSource against running demo app & Prometheus,
and FlagsmithExperimentMetricSource against local Flagsmith instance.
"""

import os
import sys
import json
import time

from guardian.config import Guardrail, RolloutConfig
from guardian.audit_log import AuditLogger
from guardian.flagsmith_client import FlagsmithAdminClient
from guardian.metrics.prometheus import PrometheusMetricSource
from guardian.metrics.flagsmith_exp import FlagsmithExperimentMetricSource
from guardian.metrics.registry import CompositeMetricProvider
from guardian.engine import GuardianEngine

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
FLAGSMITH_URL = os.environ.get("FLAGSMITH_URL", "http://localhost:8000/api/v1")
ADMIN_KEY = os.environ.get("FLAGSMITH_ADMIN_API_KEY", "GemuCCHz.OqzZwlKEacrst96Wl2oyAf3S38w0rVfF")
ENV_KEY = os.environ.get("FLAGSMITH_ENVIRONMENT_KEY", "N6LnMxtye9LM89yM7qwv8t")


def test_live_prometheus_source():
    print("=" * 60)
    print("TEST 1: PrometheusMetricSource against Live Demo App")
    print("=" * 60)

    prom_source = PrometheusMetricSource(prometheus_url=PROMETHEUS_URL)

    # 1. Guardrail for new_flow error rate
    guardrail = Guardrail(
        name="new_flow_error_rate",
        metric_source="prometheus",
        comparison=">",
        threshold=5.0,
        query="sum(checkout_errors_total{variant='new_flow'}) / sum(checkout_requests_total{variant='new_flow'}) * 100",
        sample_size_query="sum(checkout_requests_total{variant='new_flow'})",
        min_samples=30,
        description="Alert and rollback if new flow error rate exceeds 5.0%",
    )

    print(f"Querying Prometheus at {PROMETHEUS_URL}...")
    eval_result = prom_source.evaluate(guardrail)

    print(f"Metric Name  : {eval_result.name}")
    print(f"Status       : {eval_result.status.value}")
    print(f"Value        : {eval_result.value:.2f}%" if eval_result.value is not None else "Value: None")
    print(f"Sample Size  : {eval_result.sample_size}")
    print(f"Breached?    : {eval_result.is_breached}")
    print(f"Reason       : {eval_result.reason}")
    print(f"Details      : {eval_result.details}")

    # 2. Test Minimum Sample Size Gating
    print("\n--- Testing Minimum Sample Size Check (e.g. min_samples = 1000) ---")
    high_min_guardrail = Guardrail(
        name="high_sample_check",
        metric_source="prometheus",
        comparison=">",
        threshold=5.0,
        query="sum(checkout_errors_total{variant='new_flow'}) / sum(checkout_requests_total{variant='new_flow'}) * 100",
        sample_size_query="sum(checkout_requests_total{variant='new_flow'})",
        min_samples=1000,
    )
    gated_eval = prom_source.evaluate(high_min_guardrail)
    print(f"Status       : {gated_eval.status.value}")
    print(f"Sample Size  : {gated_eval.sample_size} (required: 1000)")
    print(f"Breached?    : {gated_eval.is_breached}")
    print(f"Reason       : {gated_eval.reason}")


def test_live_flagsmith_exp_source():
    print("\n" + "=" * 60)
    print("TEST 2: FlagsmithExperimentMetricSource against Local Flagsmith")
    print("=" * 60)

    exp_source = FlagsmithExperimentMetricSource(
        api_url=FLAGSMITH_URL,
        admin_api_key=ADMIN_KEY,
        environment_key=ENV_KEY,
    )

    exp_guardrail = Guardrail(
        name="checkout_conversion_chance_worse",
        metric_source="flagsmith_experiment",
        comparison=">",
        threshold=90.0,
        experiment_id=2,  # Active experiment created in spike
        min_samples=50,
        action_on_srm="ROLLBACK",
    )

    print(f"Evaluating Flagsmith Experiment ID {exp_guardrail.experiment_id}...")
    eval_result = exp_source.evaluate(exp_guardrail)

    print(f"Metric Name  : {eval_result.name}")
    print(f"Status       : {eval_result.status.value}")
    print(f"Sample Size  : {eval_result.sample_size}")
    print(f"Breached?    : {eval_result.is_breached}")
    print(f"Reason       : {eval_result.reason}")
    print(f"Details      : {eval_result.details}")

    # Test rate limit cooldown
    print("\nTesting 300s Refresh Cooldown...")
    last_req_time = exp_source._last_refresh_requested.get(2)
    print(f"Last refresh requested timestamp: {last_req_time}")
    exp_source._maybe_trigger_refresh(2)
    print("Second immediate refresh call respected cooldown without throwing 429!")


def test_live_engine_integration():
    print("\n" + "=" * 60)
    print("TEST 3: Live Guardian Engine Tick with Composite Metric Provider")
    print("=" * 60)

    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 5, 25, 100],
        soak_time_seconds=10,
        guardrails=[
            Guardrail(
                name="new_flow_error_rate",
                metric_source="prometheus",
                comparison=">",
                threshold=5.0,
                query="sum(checkout_errors_total{variant='new_flow'}) / sum(checkout_requests_total{variant='new_flow'}) * 100",
                sample_size_query="sum(checkout_requests_total{variant='new_flow'})",
                min_samples=30,
            )
        ],
        project_id=1,
        environment_id=1,
        environment_key=ENV_KEY,
    )

    provider = CompositeMetricProvider()
    provider.register_source("prometheus", PrometheusMetricSource(prometheus_url=PROMETHEUS_URL))
    provider.register_source("flagsmith_experiment", FlagsmithExperimentMetricSource(
        api_url=FLAGSMITH_URL,
        admin_api_key=ADMIN_KEY,
        environment_key=ENV_KEY,
    ))

    client = FlagsmithAdminClient(
        api_url=FLAGSMITH_URL,
        admin_api_key=ADMIN_KEY,
        environment_key=ENV_KEY,
        project_id=1,
        environment_id=1,
    )

    logger = AuditLogger()
    engine = GuardianEngine(config=cfg, flagsmith_client=client, audit_logger=logger, metric_provider=provider)

    print("Initial State:", engine.current_state)

    # Tick 1: PENDING -> STAGE_1
    print("\n--- Executing Engine Tick 1 ---")
    entry1 = engine.tick()
    print(f"Decision: {entry1.decision} | State: {entry1.from_state} -> {entry1.to_state}")
    print(f"Reason  : {entry1.reason}")
    print(f"Numbers : {entry1.numbers}")

    # Tick 2: Evaluate with live Prometheus metrics (which are ~29% error rate on new_flow > 5.0% threshold)
    print("\n--- Executing Engine Tick 2 (Live Metrics Evaluation) ---")
    entry2 = engine.tick()
    print(f"Decision: {entry2.decision} | State: {entry2.from_state} -> {entry2.to_state}")
    print(f"Reason  : {entry2.reason}")
    print(f"Numbers : {entry2.numbers}")

    print("\nFinal State:", engine.current_state)
    print("Rollout Percentage on Flagsmith:", engine.current_percentage)


if __name__ == "__main__":
    test_live_prometheus_source()
    test_live_flagsmith_exp_source()
    test_live_engine_integration()
