"""
Guardian Main Daemon Service.
Runs the FastAPI status server and periodic background evaluation loop.
"""

from __future__ import annotations
import argparse
import asyncio
import os
import sys
import time
import uvicorn
from pathlib import Path

from guardian.config import load_config
from guardian.audit_log import AuditLogger
from guardian.flagsmith_client import FlagsmithAdminClient
from guardian.metrics.prometheus import PrometheusMetricSource
from guardian.metrics.flagsmith_exp import FlagsmithExperimentMetricSource
from guardian.metrics.registry import CompositeMetricProvider
from guardian.engine import GuardianEngine
from guardian.api import create_app


def load_env_file(filepath: str = ".env") -> None:
    """Lightweight .env loader without external dependencies."""
    p = Path(filepath)
    if not p.exists():
        return
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            if k not in os.environ:
                os.environ[k] = v


async def background_ticker(engine: GuardianEngine, interval: float = 5.0) -> None:
    """Periodic evaluation ticker in the background."""
    print(f"🛡️  Guardian background evaluation worker active (interval: {interval}s)")
    while True:
        try:
            entry = engine.tick()
            if entry.decision != "HOLD":
                print(f"[{entry.decision}] {entry.from_state} ➔ {entry.to_state} | {entry.reason}")
        except Exception as e:
            print(f"⚠️  Guardian tick error: {e}", file=sys.stderr)
        await asyncio.sleep(interval)


def build_engine(config_path: str = "config.yaml") -> GuardianEngine:
    load_env_file()

    flagsmith_url = os.environ.get("FLAGSMITH_URL", "http://localhost:8000/api/v1")
    admin_api_key = os.environ.get("FLAGSMITH_ADMIN_API_KEY", "")
    env_key = os.environ.get("FLAGSMITH_ENVIRONMENT_KEY", "")
    prometheus_url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

    cfg = load_config(config_path)

    # Allow env overrides for project / env keys
    if env_key:
        cfg.environment_key = env_key

    client = FlagsmithAdminClient(
        api_url=flagsmith_url,
        admin_api_key=admin_api_key,
        environment_key=cfg.environment_key,
        project_id=cfg.project_id,
        environment_id=cfg.environment_id,
    )

    provider = CompositeMetricProvider()
    provider.register_source("prometheus", PrometheusMetricSource(prometheus_url=prometheus_url))
    provider.register_source(
        "flagsmith_experiment",
        FlagsmithExperimentMetricSource(
            api_url=flagsmith_url,
            admin_api_key=admin_api_key,
            environment_key=cfg.environment_key,
        ),
    )

    logger = AuditLogger()
    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=client,
        audit_logger=logger,
        metric_provider=provider,
    )
    return engine


def run_server(config_path: str = "config.yaml", host: str = "0.0.0.0", port: int = 8080, interval: float = 5.0):
    engine = build_engine(config_path)
    app = create_app(engine)

    @app.on_event("startup")
    async def startup_event():
        asyncio.create_task(background_ticker(engine, interval=interval))

    print(f"\n========================================================")
    print(f"🛡️  Guardian Service Starting")
    print(f"   Status Page : http://localhost:{port}/")
    print(f"   REST API    : http://localhost:{port}/status")
    print(f"   Flag Target : {engine.config.flag_name}")
    print(f"   Stages      : {engine.config.stages}%")
    print(f"========================================================\n")

    uvicorn.run(app, host=host, port=port, log_level="warning")


def main():
    parser = argparse.ArgumentParser(description="Guardian Guarded Rollout Service")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--host", default="0.0.0.0", help="Host address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    parser.add_argument("--interval", type=float, default=5.0, help="Evaluation tick interval seconds (default: 5.0)")

    args = parser.parse_args()
    run_server(config_path=args.config, host=args.host, port=args.port, interval=args.interval)


if __name__ == "__main__":
    main()
