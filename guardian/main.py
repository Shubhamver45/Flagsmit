"""
Guardian Main Daemon Service.
Runs the FastAPI status server and periodic background evaluation loop.
Supports multi-rollout configurations, SQLite persistence, and startup reconciliation.
"""

from __future__ import annotations
import argparse
import asyncio
import os
import sys
import time
import uvicorn
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Optional

from guardian.config import GuardianConfig, RolloutConfig, load_config
from guardian.audit_log import AuditLogger
from guardian.flagsmith_client import FlagsmithAdminClient
from guardian.metrics.prometheus import PrometheusMetricSource
from guardian.metrics.flagsmith_exp import FlagsmithExperimentMetricSource
from guardian.metrics.registry import CompositeMetricProvider
from guardian.storage import GuardianStorage
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
    """Periodic evaluation ticker in the background running non-blocking in worker thread."""
    print(f"🛡️  Guardian background evaluation worker active (interval: {interval}s)")
    while True:
        try:
            entry = await asyncio.to_thread(engine.tick)
            if entry.decision != "HOLD":
                print(f"[{entry.decision}] {entry.from_state} ➔ {entry.to_state} | {entry.reason}")
        except Exception as e:
            print(f"⚠️  Guardian tick error: {e}", file=sys.stderr)
        await asyncio.sleep(interval)


def build_engine(
    config_path: str = "config.yaml",
    demo_mode: bool = False,
    demo_soak: Optional[int] = None,
    storage: Optional[GuardianStorage] = None,
) -> GuardianEngine:
    load_env_file()

    flagsmith_url = os.environ.get("FLAGSMITH_URL", "http://localhost:8000/api/v1")
    admin_api_key = os.environ.get("FLAGSMITH_ADMIN_API_KEY", "")
    env_key = os.environ.get("FLAGSMITH_ENVIRONMENT_KEY", "")
    prometheus_url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

    cfg = load_config(config_path)

    # Determine soak time override
    is_demo = demo_mode or (os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes"))
    soak_override = None
    if is_demo:
        soak_override = demo_soak if demo_soak is not None else int(os.environ.get("DEMO_SOAK_SECONDS", "5"))
        print(f"⚡ [DEMO MODE ACTIVE] Accelerated soak duration: {soak_override}s per stage")

    # Apply overrides
    if isinstance(cfg, RolloutConfig):
        overrides: Dict[str, Any] = {}
        if env_key:
            overrides["environment_key"] = env_key
        if os.environ.get("FLAG_NAME") or os.environ.get("FLAGSMITH_FLAG_NAME"):
            overrides["flag_name"] = os.environ.get("FLAG_NAME") or os.environ.get("FLAGSMITH_FLAG_NAME")
        if os.environ.get("SLACK_WEBHOOK_URL"):
            overrides["slack_webhook_url"] = os.environ["SLACK_WEBHOOK_URL"]
        if os.environ.get("GENERIC_WEBHOOK_URL"):
            overrides["generic_webhook_url"] = os.environ["GENERIC_WEBHOOK_URL"]
        if soak_override is not None:
            overrides["soak_time_seconds"] = soak_override
        if overrides:
            cfg = replace(cfg, **overrides)
    elif isinstance(cfg, GuardianConfig):
        updated_rollouts = []
        for r in cfg.rollouts:
            r_overrides: Dict[str, Any] = {}
            if env_key and not r.environment_key:
                r_overrides["environment_key"] = env_key
            if os.environ.get("SLACK_WEBHOOK_URL") and not r.slack_webhook_url:
                r_overrides["slack_webhook_url"] = os.environ["SLACK_WEBHOOK_URL"]
            if os.environ.get("GENERIC_WEBHOOK_URL") and not r.generic_webhook_url:
                r_overrides["generic_webhook_url"] = os.environ["GENERIC_WEBHOOK_URL"]
            if soak_override is not None:
                r_overrides["soak_time_seconds"] = soak_override
            if r_overrides:
                updated_rollouts.append(replace(r, **r_overrides))
            else:
                updated_rollouts.append(r)
        cfg = GuardianConfig(rollouts=updated_rollouts, db_path=cfg.db_path)

    client = FlagsmithAdminClient(
        api_url=flagsmith_url,
        admin_api_key=admin_api_key,
        environment_key=cfg.environment_key,
        project_id=cfg.project_id or (int(os.environ["FLAGSMITH_PROJECT_ID"]) if os.environ.get("FLAGSMITH_PROJECT_ID") else None),
        environment_id=cfg.environment_id or (int(os.environ["FLAGSMITH_ENVIRONMENT_ID"]) if os.environ.get("FLAGSMITH_ENVIRONMENT_ID") else None),
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

    guardian_storage = storage or GuardianStorage(db_path=getattr(cfg, "db_path", None))
    logger = AuditLogger(storage=guardian_storage)

    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=client,
        audit_logger=logger,
        metric_provider=provider,
        storage=guardian_storage,
    )
    return engine


def run_server(
    config_path: str = "config.yaml",
    host: str = "0.0.0.0",
    port: int = 8080,
    interval: float = 5.0,
    demo_mode: bool = False,
    demo_soak: Optional[int] = None,
):
    engine = build_engine(config_path, demo_mode=demo_mode, demo_soak=demo_soak)
    app = create_app(engine)

    @app.on_event("startup")
    async def startup_event():
        asyncio.create_task(background_ticker(engine, interval=interval))

    flag_names = list(engine.rollout_configs.keys())
    print(f"\n========================================================")
    print(f"🛡️  Guardian Service Starting")
    print(f"   Status Page : http://localhost:{port}/")
    print(f"   REST API    : http://localhost:{port}/status")
    print(f"   Database    : {engine.storage.db_path}")
    print(f"   Rollouts ({len(flag_names)}) : {', '.join(flag_names)}")
    print(f"   Primary Flag: {engine.config.flag_name}")
    print(f"   Stages      : {engine.config.stages}%")
    print(f"   Soak Time   : {engine.config.soak_time_seconds}s per stage")
    print(f"========================================================\n")

    uvicorn.run(app, host=host, port=port, log_level="warning")


def main():
    parser = argparse.ArgumentParser(description="Guardian Guarded Rollout Service")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--host", default="0.0.0.0", help="Host address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    parser.add_argument("--interval", type=float, default=3.0, help="Evaluation tick interval seconds (default: 3.0)")
    parser.add_argument("--demo-mode", action="store_true", help="Enable accelerated demo mode with fast soak times")
    parser.add_argument("--demo-soak", type=int, default=None, help="Soak time in seconds when in demo mode (default: 5)")

    args = parser.parse_args()
    run_server(
        config_path=args.config,
        host=args.host,
        port=args.port,
        interval=args.interval,
        demo_mode=args.demo_mode,
        demo_soak=args.demo_soak,
    )


if __name__ == "__main__":
    main()
