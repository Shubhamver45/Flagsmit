#!/usr/bin/env python3
"""
Verification Script: Test Guardian Reusability on a Freshly Created Flagsmith Project and Flag.
Demonstrates that Guardian is completely decoupled from the demo checkout setup and
can manage any arbitrary project and flag on Flagsmith.

========================================================================================
METRIC SOURCE ARCHITECTURE & PROMPT 18 VERIFICATION NOTE:
========================================================================================
1. Historical / Prior Metric Source:
   - Previously in Prompt 18, this test used a simulated metric source (MockMetricSource)
     which hardcoded 0.2% (healthy) and 14.5% (breach).
   - This was labeled as simulated in the README and script header as requested.

2. Real Traffic & Prometheus Metric Source (Added per Prompt 19 / User Request):
   - We run a real in-process HTTP microservice ('RecommendationMicroservice') on port 5002.
   - It exposes standard Prometheus metrics at /metrics:
       * recommendation_requests_total (Counter)
       * recommendation_errors_total (Counter)
   - Prometheus is configured via demo/prometheus.yml with scrape job 'second-project-app'
     scraping host.docker.internal:5002 with a 2s scrape_interval.
   - Guardian queries real PromQL via PrometheusMetricSource:
       ((sum(rate(recommendation_errors_total[15s])) or vector(0)) /
        (sum(rate(recommendation_requests_total[15s])) > 0 or vector(1))) * 100
   - Real HTTP traffic is pumped into /recommend.
   - Fault injection: /recommend switches to returning HTTP 500 errors, driving the real
     Prometheus error rate above the 5.0% guardrail threshold and triggering an
     instant automated rollback to 0.0% on Flagsmith.
========================================================================================
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from dotenv import dotenv_values
import yaml

from guardian.config import load_config
from guardian.flagsmith_client import FlagsmithAdminClient
from guardian.audit_log import AuditLogger
from guardian.actions.alerts import AlertDispatcher
from guardian.metrics.registry import CompositeMetricProvider
from guardian.metrics.prometheus import PrometheusMetricSource
from guardian.engine import GuardianEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("test_second_project")

APP_PORT = 5002
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")


class RecommendationAppServer:
    """Lightweight in-process HTTP microservice exposing Prometheus metrics on port 5002."""

    def __init__(self, port: int = APP_PORT):
        self.port = port
        self.requests_total = 0
        self.errors_total = 0
        self.fault_active = False
        self._lock = threading.Lock()
        self.server: http.server.HTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self):
        handler_cls = self._make_handler()
        self.server = http.server.HTTPServer(("0.0.0.0", self.port), handler_cls)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info(f"Recommendation microservice started on port {self.port}")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            logger.info("Recommendation microservice stopped.")

    def record_request(self, is_error: bool = False):
        with self._lock:
            self.requests_total += 1
            if is_error:
                self.errors_total += 1

    def set_fault(self, active: bool):
        with self._lock:
            self.fault_active = active

    def _make_handler(app_self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/metrics":
                    with app_self._lock:
                        reqs = app_self.requests_total
                        errs = app_self.errors_total
                    content = (
                        f"# HELP recommendation_requests_total Total requests to recommendation service\n"
                        f"# TYPE recommendation_requests_total counter\n"
                        f"recommendation_requests_total {reqs}\n\n"
                        f"# HELP recommendation_errors_total Total failed recommendation requests\n"
                        f"# TYPE recommendation_errors_total counter\n"
                        f"recommendation_errors_total {errs}\n"
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                elif self.path == "/recommend":
                    with app_self._lock:
                        is_fault = app_self.fault_active
                    if is_fault:
                        app_self.record_request(is_error=True)
                        self.send_response(500)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b'{"error": "recommendation model failed"}')
                    else:
                        app_self.record_request(is_error=False)
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b'{"recommendations": ["prod_101", "prod_202"]}')
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass

        return Handler


def generate_traffic(app: RecommendationAppServer, stop_event: threading.Event):
    """Continuously generates HTTP traffic against the recommendation microservice."""
    while not stop_event.is_set():
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{APP_PORT}/recommend")
            with urllib.request.urlopen(req, timeout=1.0) as _:
                pass
        except Exception:
            pass
        time.sleep(0.04)  # ~25 requests/sec


def wait_for_prometheus_target():
    """Verify that Prometheus is scraping host.docker.internal:5002 successfully."""
    url = f"{PROMETHEUS_URL}/api/v1/targets"
    for _ in range(15):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode())
                active = data.get("data", {}).get("activeTargets", [])
                for t in active:
                    if t.get("labels", {}).get("job") == "second-project-app":
                        if t.get("health") == "up":
                            return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def main():
    print("========================================================================")
    print("🚀 GUARDIAN REUSABILITY VERIFICATION: FRESH PROJECT & REAL METRIC TRAFFIC")
    print("========================================================================")

    env = dotenv_values(".env")
    flagsmith_url = env.get("FLAGSMITH_URL", "http://localhost:8000/api/v1")
    admin_api_key = env.get("FLAGSMITH_ADMIN_API_KEY")
    if not admin_api_key:
        print("❌ Error: FLAGSMITH_ADMIN_API_KEY missing from .env", file=sys.stderr)
        sys.exit(1)

    # 1. Start Recommendation Microservice on port 5002
    print("\n--- Step 1: Starting In-Process Recommendation Microservice on Port 5002 ---")
    app = RecommendationAppServer(port=APP_PORT)
    app.start()

    stop_traffic = threading.Event()
    traffic_thread = threading.Thread(target=generate_traffic, args=(app, stop_traffic), daemon=True)
    traffic_thread.start()

    try:
        print("  • Pumping healthy HTTP traffic into http://127.0.0.1:5002/recommend...")
        time.sleep(2.0)

        # 2. Verify Prometheus Target is UP
        print("\n--- Step 2: Verifying Prometheus Scrapes host.docker.internal:5002 ---")
        if wait_for_prometheus_target():
            print("  ✔ Verified Prometheus target 'second-project-app' is UP and scraping every 2s.")
        else:
            print("  ⚠ Prometheus target 'second-project-app' not confirmed UP yet. Proceeding...")

        # 3. Discover Organisation and Setup Second Project in Flagsmith
        print("\n--- Step 3: Provisioning Second Project, Environment, and Flag in Flagsmith ---")
        setup_code = """
from organisations.models import Organisation
from projects.models import Project
from environments.models import Environment
from features.models import Feature, FeatureState
from features.multivariate.models import MultivariateFeatureOption

org = Organisation.objects.first()
project, _ = Project.objects.get_or_create(
    name="E-Commerce Recommendation Engine",
    organisation=org,
)
env, _ = Environment.objects.get_or_create(
    name="Production",
    project=project,
)
feature, _ = Feature.objects.get_or_create(
    project=project,
    name="v2_recommendations_model",
    defaults={
        "type": "MULTIVARIATE",
        "default_enabled": False,
        "description": "V2 Deep Learning recommendation ranking model",
    }
)
opt, _ = MultivariateFeatureOption.objects.get_or_create(
    feature=feature,
    string_value="variation",
    defaults={"type": "unicode", "default_percentage_allocation": 0}
)
fs, _ = FeatureState.objects.get_or_create(
    feature=feature,
    environment=env,
    defaults={"enabled": False}
)
fs.multivariate_feature_state_values.get_or_create(
    multivariate_feature_option=opt,
    defaults={"percentage_allocation": 0}
)

import json
print("=== SECOND_PROJECT_INFO_START ===")
print(json.dumps({
    "project_id": project.id,
    "project_name": project.name,
    "environment_id": env.id,
    "environment_name": env.name,
    "environment_key": env.api_key,
    "flag_name": feature.name,
}))
print("=== SECOND_PROJECT_INFO_END ===")
"""
        cmd = ["docker", "compose", "exec", "-T", "flagsmith", "python", "manage.py", "shell", "-c", setup_code]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if "=== SECOND_PROJECT_INFO_START ===" not in res.stdout:
            print(f"❌ Failed to set up second project:\n{res.stderr or res.stdout}", file=sys.stderr)
            sys.exit(1)

        info_str = res.stdout.split("=== SECOND_PROJECT_INFO_START ===")[1].split("=== SECOND_PROJECT_INFO_END ===")[0].strip()
        proj_info = json.loads(info_str)
        second_project_id = proj_info["project_id"]
        second_project_name = proj_info["project_name"]
        second_env_id = proj_info["environment_id"]
        second_env_name = proj_info["environment_name"]
        second_env_key = proj_info["environment_key"]
        second_flag_name = proj_info["flag_name"]

        print(f"✔ Second Project Ready!")
        print(f"  • Project Name     : {second_project_name} (ID: {second_project_id})")
        print(f"  • Environment Name : {second_env_name} (ID: {second_env_id})")
        print(f"  • Environment Key  : {second_env_key}")
        print(f"  • Feature Flag     : '{second_flag_name}'")

        # 4. Execute `guardian init` via CLI targeting the new project and flag
        print("\n--- Step 4: Executing `guardian init` CLI for the Second Project ---")
        config_output = "config.second_project.yaml"
        init_cmd = [
            sys.executable, "-m", "guardian.cli", "init",
            "--flagsmith-url", flagsmith_url,
            "--admin-api-key", admin_api_key,
            "--environment-key", second_env_key,
            "--flag-name", second_flag_name,
            "--stages", "1, 10, 50, 100",
            "--soak", "1",
            "-o", config_output,
            "--non-interactive",
        ]
        sub_env = {**os.environ, "PYTHONPATH": "."}
        proc = subprocess.run(init_cmd, capture_output=True, text=True, env=sub_env)
        print(proc.stdout)
        if proc.returncode != 0:
            print(f"❌ `guardian init` failed:\n{proc.stderr}", file=sys.stderr)
            sys.exit(1)

        assert Path(config_output).exists(), f"Expected {config_output} to be created!"
        print(f"✔ Verified {config_output} was generated by `guardian init`.")

        # 5. Customize the guardrail query to evaluate the real recommendation microservice
        print("\n--- Step 5: Configuring Guardrail with Real PromQL Query on Port 5002 ---")
        with open(config_output, "r") as f:
            raw_cfg = yaml.safe_load(f)

        raw_cfg["guardrails"] = [
            {
                "name": "recommendation_error_rate",
                "metric_source": "prometheus",
                "comparison": ">",
                "threshold": 5.0,
                "query": "((sum(rate(recommendation_errors_total[15s])) or vector(0)) / (sum(rate(recommendation_requests_total[15s])) > 0 or vector(1))) * 100",
                "sample_size_query": "sum(recommendation_requests_total)",
                "min_samples": 10,
                "description": "Recommendation service error rate (must stay below 5.0%)",
            }
        ]
        with open(config_output, "w") as f:
            yaml.safe_dump(raw_cfg, f)

        cfg = load_config(config_output)
        assert cfg.flag_name == second_flag_name
        assert cfg.environment_key == second_env_key
        assert cfg.stages == [1, 10, 50, 100]

        client = FlagsmithAdminClient(
            api_url=flagsmith_url,
            admin_api_key=admin_api_key,
            environment_key=second_env_key,
            project_id=second_project_id,
            environment_id=second_env_id,
        )

        # Ensure initial allocation is reset to 0.0%
        client.rollback_to_zero(second_flag_name, reason="Initial verification reset")
        init_pct = client.get_percentage_rollout(second_flag_name)
        print(f"  • Flagsmith Remote Feature Allocation initially: {init_pct}%")
        assert init_pct == 0.0

        # Initialize Real Prometheus Metric Source
        prom_source = PrometheusMetricSource(prometheus_url=PROMETHEUS_URL)
        provider = CompositeMetricProvider()
        provider.register_source("prometheus", prom_source)

        logger_inst = AuditLogger()
        alerts = AlertDispatcher()
        engine = GuardianEngine(
            config=cfg,
            flagsmith_client=client,
            audit_logger=logger_inst,
            metric_provider=provider,
            alert_dispatcher=alerts,
        )

        # 6. Phased Rollout Execution with Real Traffic (Healthy)
        print("\n--- Step 6: Executing Phased Rollout Stages with Real Healthy Traffic ---")
        # Tick 1: PENDING -> STAGE_1 (1%)
        e1 = engine.tick()
        print(f"  [Tick 1] Decision={e1.decision} | State: {engine.current_state} ({engine.current_percentage}%)")
        remote_1 = client.get_percentage_rollout(second_flag_name)
        print(f"  ➔ Verified remote Flagsmith allocation updated to: {remote_1}%")
        assert remote_1 == 1.0

        # Wait soak
        time.sleep(1.2)
        # Tick 2: STAGE_1 -> STAGE_2 (10%)
        e2 = engine.tick()
        print(f"  [Tick 2] Decision={e2.decision} | State: {engine.current_state} ({engine.current_percentage}%)")
        remote_2 = client.get_percentage_rollout(second_flag_name)
        print(f"  ➔ Verified remote Flagsmith allocation updated to: {remote_2}%")
        assert remote_2 == 10.0

        # Wait soak
        time.sleep(1.2)
        # Tick 3: STAGE_2 -> STAGE_3 (50%)
        e3 = engine.tick()
        print(f"  [Tick 3] Decision={e3.decision} | State: {engine.current_state} ({engine.current_percentage}%)")
        remote_3 = client.get_percentage_rollout(second_flag_name)
        print(f"  ➔ Verified remote Flagsmith allocation updated to: {remote_3}%")
        assert remote_3 == 50.0

        # Wait soak
        time.sleep(1.2)
        # Tick 4: STAGE_3 -> AWAITING_APPROVAL (holds at 50%)
        e4 = engine.tick()
        print(f"  [Tick 4] Decision={e4.decision} | State: {engine.current_state} ({engine.current_percentage}%)")
        assert engine.current_state == "AWAITING_APPROVAL"
        remote_hold = client.get_percentage_rollout(second_flag_name)
        print(f"  ➔ Holding at {remote_hold}% before final stage: Human operator approval strictly required.")
        assert remote_hold == 50.0

        # 7. Human Operator Signoff / Promotion
        print("\n--- Step 7: Human Operator Promotion Approval ---")
        app_entry = engine.approve()
        print(f"  [Approve] Decision={app_entry.decision} | State: {engine.current_state} ({engine.current_percentage}%)")
        assert engine.current_state == "PROMOTED"
        remote_promoted = client.get_percentage_rollout(second_flag_name)
        print(f"  ➔ Verified remote Flagsmith allocation promoted to: {remote_promoted}%")
        assert remote_promoted == 100.0

        # 8. Injected Guardrail Failure via Real HTTP 500 Traffic & Real Prometheus PromQL Breach
        print("\n--- Step 8: Injecting Real HTTP 500 Faults into Port 5002 & Awaiting Rollback ---")
        app.set_fault(True)
        print("  • Switched microservice to 100% HTTP 500 errors.")
        print("  • Waiting for Prometheus 15s window to capture error spikes...")

        rolled_back = False
        start_breach = time.time()
        for i in range(12):
            time.sleep(1.5)
            rb_entry = engine.tick()
            print(f"  [Monitoring Tick {i+1}] State: {engine.current_state} ({engine.current_percentage}%) | Decision: {rb_entry.decision} | Reason: {rb_entry.reason[:80]}...")
            if engine.current_state == "ROLLED_BACK":
                duration = time.time() - start_breach
                remote_rollback = client.get_percentage_rollout(second_flag_name)
                print("\n" + "=" * 70)
                print("🎉 SUCCESS: Real PromQL Breach Detected! Instant Automated Rollback Triggered!")
                print(f"  • Time to Rollback  : {duration:.2f}s")
                print(f"  • Decision          : {rb_entry.decision}")
                print(f"  • Flagsmith Remote  : {remote_rollback}% (disabled)")
                print(f"  • Breach Reason     : {rb_entry.reason}")
                print(f"  • Evidence Numbers  : {rb_entry.numbers}")
                print("=" * 70)
                assert remote_rollback == 0.0
                rolled_back = True
                break

        assert rolled_back, "Expected automated rollback to trigger from real Prometheus breach!"

        # Clean up generated config file
        if os.path.exists(config_output):
            os.remove(config_output)

        print("\n========================================================================")
        print("🎉 FULL LIFECYCLE WITH REAL PROMETHEUS SCRAPING SUCCEEDED ON SECOND PROJECT!")
        print(f"  • Second Project ID : {second_project_id} ('{second_project_name}')")
        print(f"  • Environment Key   : {second_env_key}")
        print(f"  • Feature Flag      : '{second_flag_name}'")
        print("  • Verification Flow : PENDING ➔ 1% ➔ 10% ➔ 50% ➔ APPROVAL ➔ 100% ➔ ROLLBACK (0%)")
        print("========================================================================")

    finally:
        stop_traffic.set()
        app.stop()


if __name__ == "__main__":
    main()
