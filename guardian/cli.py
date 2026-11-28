"""
Guardian CLI Command-Line Interface.
Provides single-line CLI commands for operators, including `guardian approve`.
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request
import urllib.error


def call_api(endpoint: str, base_url: str = "http://localhost:8080", data: dict = None, token: str = None) -> dict:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    headers = {"Content-Type": "application/json"}
    
    api_token = token or os.environ.get("GUARDIAN_API_TOKEN") or os.environ.get("GUARDIAN_TOKEN")
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    body = json.dumps(data or {}).encode("utf-8") if data is not None else None

    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            err_json = json.loads(error_body)
            msg = err_json.get("detail", error_body)
        except Exception:
            msg = error_body
        print(f"Error ({e.code}): {msg}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Connection failed to {url}: {e}", file=sys.stderr)
        sys.exit(1)


def handle_init(args: argparse.Namespace) -> None:
    print("========================================================================")
    print("🛡️  GUARDIAN ROLLOUT CONTROLLER INITIALIZATION WIZARD")
    print("========================================================================")
    print("Configure Guardian to protect and automate phased rollouts in Flagsmith.\n")

    # Determine default values from environment or CLI
    flagsmith_url = (
        args.flagsmith_url
        or os.environ.get("FLAGSMITH_URL")
        or "https://edge.api.flagsmith.com/api/v1"
    )
    admin_api_key = args.admin_api_key or os.environ.get("FLAGSMITH_ADMIN_API_KEY", "")
    env_key = args.environment_key or os.environ.get("FLAGSMITH_ENVIRONMENT_KEY", "")
    flag_name = args.flag_name or os.environ.get("FLAG_NAME") or os.environ.get("FLAGSMITH_FLAG_NAME", "")
    prometheus_url = args.prometheus_url or os.environ.get("PROMETHEUS_URL") or "http://localhost:9090"
    stages_str = args.stages or "1, 5, 25, 100"
    soak_seconds = args.soak

    # If interactive and values are missing:
    is_interactive = sys.stdin.isatty() and not getattr(args, "non_interactive", False)

    if is_interactive and (not flag_name or not admin_api_key or not env_key):
        def prompt(msg: str, default: str) -> str:
            val = input(f"{msg} [{default}]: ").strip()
            return val if val else default

        flagsmith_url = prompt("Flagsmith API URL", flagsmith_url)
        admin_api_key = prompt("Flagsmith Admin / Master API Key", admin_api_key)
        env_key = prompt("Flagsmith Environment Client Key", env_key)
        flag_name = prompt("Target Feature Flag Name", flag_name)
        prometheus_url = prompt("Prometheus URL (optional)", prometheus_url)
        stages_str = prompt("Phased Rollout Stages (%)", stages_str)
        soak_input = prompt("Soak Duration per stage in seconds", str(soak_seconds))
        try:
            soak_seconds = int(soak_input)
        except ValueError:
            soak_seconds = 30

    if not flag_name:
        print("❌ Error: Target flag name is required (use --flag-name or set FLAG_NAME env var)", file=sys.stderr)
        sys.exit(1)

    # Clean flagsmith_url
    flagsmith_url = flagsmith_url.rstrip("/")
    if not flagsmith_url.endswith("/api/v1"):
        flagsmith_url = f"{flagsmith_url}/api/v1"

    # Clean stages
    stages = []
    for s in stages_str.split(","):
        s = s.strip()
        if s:
            stages.append(int(s))
    if not stages:
        stages = [1, 5, 25, 100]

    # Verification against Flagsmith API
    project_id = None
    env_id = None
    if not getattr(args, "skip_verify", False) and admin_api_key and env_key:
        print(f"\n==> Verifying connection to Flagsmith API at {flagsmith_url}...")
        try:
            req = urllib.request.Request(
                f"{flagsmith_url}/environments/{env_key}/",
                headers={"Authorization": f"Api-Key {admin_api_key}"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                env_id = data.get("id")
                project_id = data.get("project")
                env_name = data.get("name", "Unknown")
                print(f"✔ Successfully authenticated with Flagsmith!")
                print(f"  • Environment : {env_name} (ID: {env_id})")
                print(f"  • Project ID  : {project_id}")

            # Verify flag exists in project
            if project_id:
                req_feat = urllib.request.Request(
                    f"{flagsmith_url}/projects/{project_id}/features/?q={flag_name}",
                    headers={"Authorization": f"Api-Key {admin_api_key}"},
                )
                with urllib.request.urlopen(req_feat, timeout=8) as resp_feat:
                    feats = json.loads(resp_feat.read().decode("utf-8")).get("results", [])
                    found = any(f["name"] == flag_name for f in feats)
                    if found:
                        print(f"✔ Verified feature flag '{flag_name}' exists in project {project_id}.")
                    else:
                        print(f"ℹ Feature flag '{flag_name}' not yet found in project {project_id}. (Guardian will control it once created).")
        except Exception as e:
            print(f"⚠️  Note: Could not verify with Flagsmith: {e}")

    # Build config yaml string
    stages_yaml = "\n".join(f"  - {s}" for s in stages)
    config_content = f"""# Guardian Rollout Configuration
# Generated by `guardian init`
flag_name: "{flag_name}"
environment_key: "{env_key}"

# Phased rollout percentages
stages:
{stages_yaml}

# Minimum soak time (seconds) to monitor healthy metrics before advancing
soak_time_seconds: {soak_seconds}

# Require explicit operator approval before final 100% promotion
require_approval_before_final: true

# Guardrail metric triggers
guardrails:
  # Example 1: Prometheus error rate guardrail (rolls back to 0% if > 5.0%)
  - name: "{flag_name}_error_rate"
    metric_source: "prometheus"
    comparison: ">"
    threshold: 5.0
    query: "((sum(rate(checkout_errors_total{{variant='new_flow'}}[15s])) or vector(0)) / (sum(rate(checkout_requests_total{{variant='new_flow'}}[15s])) > 0 or vector(1))) * 100"
    sample_size_query: "sum(checkout_requests_total)"
    min_samples: 25
    description: "Rollback to 0% if error rate exceeds 5.0%"

  # Example 2: Flagsmith Experimentation Bayesian statistical guardrail
  - name: "checkout_ab_chance_to_be_worse"
    metric_source: "flagsmith_experiment"
    metric_id: 1
    comparison: ">"
    threshold: 10.0
    min_samples: 50
    action_on_srm: "HOLD"
    description: "Hold rollout if chance-to-be-worse > 10.0% or if SRM warning fires"
"""

    output_path = args.output or "config.yaml"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(config_content)
    print(f"\n✔ Configuration written to {output_path}!")

    if getattr(args, "save_env", False):
        env_path = getattr(args, "env_file", ".env") or ".env"
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        env_dict = {
            "FLAGSMITH_URL": flagsmith_url,
            "FLAGSMITH_ADMIN_API_KEY": admin_api_key,
            "FLAGSMITH_ENVIRONMENT_KEY": env_key,
            "FLAG_NAME": flag_name,
            "PROMETHEUS_URL": prometheus_url,
        }

        existing_keys = set()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in env_dict:
                    new_lines.append(f"{k}={env_dict[k]}\n")
                    existing_keys.add(k)
                    continue
            new_lines.append(line)

        for k, v in env_dict.items():
            if k not in existing_keys:
                new_lines.append(f"{k}={v}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        print(f"✔ Environment variables saved to {env_path}!")

    print("\n------------------------------------------------------------------------")
    print("🚀 NEXT STEPS TO RUN GUARDIAN:")
    print("------------------------------------------------------------------------")
    print(f"1. Start Guardian daemon:")
    print(f"   python3 -m guardian.main --port 8080")
    print(f"   (or via Docker: docker run -p 8080:8080 -v $(pwd)/{output_path}:/app/config.yaml ghcr.io/shubhamver45/flagsmith-guardian:latest)")
    print(f"2. Inspect live status in browser or CLI:")
    print(f"   open http://localhost:8080/  OR  python3 -m guardian.cli status")
    print(f"3. When ready for final promotion:")
    print(f"   python3 -m guardian.cli approve --by operator --reason 'Ready for 100%'")
    print("========================================================================\n")


def main():
    parser = argparse.ArgumentParser(description="Guardian CLI Operator Interface")
    parser.add_argument("--url", default="http://localhost:8080", help="Guardian service URL (default: http://localhost:8080)")
    parser.add_argument("--token", default=None, help="Guardian API token (or set GUARDIAN_API_TOKEN env var)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Init subcommand
    init_parser = subparsers.add_parser("init", help="Initialize Guardian configuration for Flagsmith")
    init_parser.add_argument("--flagsmith-url", default=None, help="Flagsmith API URL (or FLAGSMITH_URL env var)")
    init_parser.add_argument("--admin-api-key", default=None, help="Flagsmith Admin/Master API key (or FLAGSMITH_ADMIN_API_KEY env var)")
    init_parser.add_argument("--environment-key", default=None, help="Flagsmith Environment client key (or FLAGSMITH_ENVIRONMENT_KEY env var)")
    init_parser.add_argument("--flag-name", default=None, help="Feature flag to manage (or FLAG_NAME env var)")
    init_parser.add_argument("--prometheus-url", default=None, help="Prometheus URL (or PROMETHEUS_URL env var)")
    init_parser.add_argument("--stages", default="1, 5, 25, 100", help="Phased rollout percentage stages (comma-separated, default: 1,5,25,100)")
    init_parser.add_argument("--soak", type=int, default=30, help="Soak time in seconds per stage (default: 30)")
    init_parser.add_argument("-o", "--output", default="config.yaml", help="Output YAML config file (default: config.yaml)")
    init_parser.add_argument("--save-env", action="store_true", help="Also save connection parameters into .env file")
    init_parser.add_argument("--env-file", default=".env", help="Path to .env file to update when --save-env is used")
    init_parser.add_argument("--skip-verify", action="store_true", help="Skip remote verification against Flagsmith API")
    init_parser.add_argument("--non-interactive", action="store_true", help="Run without interactive prompts")

    # Approve subcommand
    approve_parser = subparsers.add_parser("approve", help="Approve final 100%% promotion when awaiting approval")
    approve_parser.add_argument("--flag", default=None, help="Target feature flag name")
    approve_parser.add_argument("--by", default="operator", help="Approver identity")
    approve_parser.add_argument("--reason", default="Approved final promotion", help="Approval rationale")

    # Status subcommand
    status_parser = subparsers.add_parser("status", help="Get current rollout status")
    status_parser.add_argument("--flag", default=None, help="Target feature flag name")

    # Rollback subcommand
    rollback_parser = subparsers.add_parser("rollback", help="Force instant manual rollback to 0%%")
    rollback_parser.add_argument("--flag", default=None, help="Target feature flag name")
    rollback_parser.add_argument("--reason", default="Manual operator rollback", help="Rollback reason")

    # Reset subcommand
    reset_parser = subparsers.add_parser("reset", help="Reset rollout back to initial PENDING state (demo use)")
    reset_parser.add_argument("--flag", default=None, help="Target feature flag name")

    # Optional MCP server subcommand
    subparsers.add_parser("mcp", help="Run optional MCP server over stdio (requires guardian[mcp])")

    # Doctor subcommand
    doctor_parser = subparsers.add_parser("doctor", help="Check Python, config, Flagsmith, Prometheus, and DB readiness")
    doctor_parser.add_argument("--config", default=os.environ.get("GUARDIAN_CONFIG_PATH", "config.yaml"), help="Path to Guardian YAML config")

    # Config subcommand
    cfg_parser = subparsers.add_parser("config", help="Configuration utilities")
    cfg_sub = cfg_parser.add_subparsers(dest="config_action", required=True)
    cfg_val = cfg_sub.add_parser("validate", help="Validate Guardian YAML configuration")
    cfg_val.add_argument("--config", default=os.environ.get("GUARDIAN_CONFIG_PATH", "config.yaml"), help="Path to Guardian YAML config")

    # Flags subcommand
    flags_parser = subparsers.add_parser("flags", help="Discover Flagsmith feature flags")
    flags_sub = flags_parser.add_subparsers(dest="flags_action", required=True)
    flags_sub.add_parser("list", help="List feature flags from Guardian / Flagsmith")

    # Rollout subcommand
    rollout_parser = subparsers.add_parser("rollout", help="Manage rollouts (status, pause, resume)")
    rollout_sub = rollout_parser.add_subparsers(dest="rollout_action", required=True)
    ro_status = rollout_sub.add_parser("status", help="Get rollout status")
    ro_status.add_argument("--flag", default=None, help="Target feature flag name")
    ro_pause = rollout_sub.add_parser("pause", help="Pause an active rollout")
    ro_pause.add_argument("--flag", default=None, help="Target feature flag name")
    ro_resume = rollout_sub.add_parser("resume", help="Resume a paused rollout")
    ro_resume.add_argument("--flag", default=None, help="Target feature flag name")

    # Audit subcommand
    audit_parser = subparsers.add_parser("audit", help="Inspect and verify audit trail")
    audit_sub = audit_parser.add_subparsers(dest="audit_action", required=True)
    aud_list = audit_sub.add_parser("list", help="List recent audit entries")
    aud_list.add_argument("--limit", type=int, default=20, help="Maximum entries to display")
    audit_sub.add_parser("verify", help="Verify tamper-evident audit hash chain")

    # Demo subcommand
    demo_parser = subparsers.add_parser("demo", help="Launch or inspect local Flagsmith Guardian demo")
    demo_parser.add_argument("--serve", action="store_true", help="Keep demo stack running after walkthrough")

    args = parser.parse_args()

    if args.command == "init":
        handle_init(args)

    elif args.command == "approve":
        print(f"==> Sending approval to Guardian at {args.url}...")
        data = {"approved_by": args.by, "reason": args.reason}
        if args.flag:
            data["flag_name"] = args.flag
        res = call_api("/approve", base_url=args.url, data=data, token=args.token)
        print(f"✅ Success: {res.get('message')}")
        print(f"State: {res.get('current_state')} | Rollout: {res.get('current_percentage')}%")

    elif args.command == "status":
        endpoint = f"/status?flag_name={args.flag}" if getattr(args, "flag", None) else "/status"
        res = call_api(endpoint, base_url=args.url, token=args.token)
        print(f"Flag   : {res.get('flag_name')}")
        print(f"State  : {res.get('state')} (Stage index: {res.get('stage_index')})")
        print(f"Rollout: {res.get('percentage')}%")
        print(f"Stages : {res.get('stages')}")
        if res.get("drift", {}).get("detected"):
            print(f"⚠️  DRIFT: {res['drift'].get('message')}")

    elif args.command == "rollback":
        print(f"==> Sending rollback to Guardian at {args.url}...")
        data = {"reason": args.reason}
        if args.flag:
            data["flag_name"] = args.flag
        res = call_api("/rollback", base_url=args.url, data=data, token=args.token)
        print(f"🛑 Rolled Back: {res.get('message')}")
        print(f"State: {res.get('current_state')} | Rollout: {res.get('current_percentage')}%")

    elif args.command == "reset":
        print(f"==> Sending reset to Guardian at {args.url}...")
        data = {}
        if args.flag:
            data["flag_name"] = args.flag
        res = call_api("/reset", base_url=args.url, data=data, token=args.token)
        print(f"🔄 Reset: {res.get('message')}")
        print(f"State: {res.get('current_state')} | Rollout: {res.get('current_percentage')}%")

    elif args.command == "mcp":
        try:
            from guardian.mcp_server import run_mcp_server
            run_mcp_server(base_url=args.url, api_token=args.token)
        except ImportError:
            print(
                "Error: optional MCP dependencies are not installed. "
                "Install them with: pip install 'flagsmith-guardian[mcp]'",
                file=sys.stderr,
            )
            sys.exit(1)

    elif args.command == "config" and args.config_action == "validate":
        from guardian.config import load_config
        try:
            cfg = load_config(args.config)
            print(f"✔ Config valid: {args.config} ({len(cfg.rollouts)} rollout(s))")
            for r in cfg.rollouts:
                print(f"  • {r.flag_name}: stages={r.stages}, soak={r.soak_time_seconds}s, guardrails={len(r.guardrails)}")
        except Exception as e:
            print(f"❌ Invalid config ({args.config}): {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "doctor":
        _run_doctor(args.config)

    elif args.command == "flags" and args.flags_action == "list":
        res = call_api("/flagsmith/features", base_url=args.url, token=args.token)
        feats = res.get("features") or res.get("results") or []
        if not feats:
            print("No feature flags found.")
        for f in feats:
            print(f"• {f.get('name')} (id={f.get('id')}, type={f.get('type', 'STANDARD')})")

    elif args.command == "rollout":
        if args.rollout_action == "status":
            endpoint = f"/status?flag_name={args.flag}" if args.flag else "/status"
            res = call_api(endpoint, base_url=args.url, token=args.token)
            print(f"Flag: {res.get('flag_name')} | State: {res.get('state')} | Rollout: {res.get('percentage')}%")
        elif args.rollout_action == "pause":
            data = {"flag_name": args.flag} if args.flag else {}
            res = call_api("/pause", base_url=args.url, data=data, token=args.token)
            print(f"⏸️  {res.get('message')} (State: {res.get('current_state')})")
        elif args.rollout_action == "resume":
            data = {"flag_name": args.flag} if args.flag else {}
            res = call_api("/resume", base_url=args.url, data=data, token=args.token)
            print(f"▶️  {res.get('message')} (State: {res.get('current_state')})")

    elif args.command == "audit":
        if args.audit_action == "list":
            res = call_api("/status", base_url=args.url, token=args.token)
            entries = (res.get("timeline") or [])[-args.limit:]
            for e in entries:
                print(f"[{e.get('timestamp')}] {e.get('flag_name')} {e.get('from_state')} -> {e.get('to_state')} ({e.get('decision')}): {e.get('reason')}")
        elif args.audit_action == "verify":
            res = call_api("/audit/verify", base_url=args.url, token=args.token)
            status_icon = "✔" if res.get("valid") else "❌"
            print(f"{status_icon} {res.get('reason')} (checked={res.get('checked_entries')})")
            if not res.get("valid"):
                sys.exit(1)

    elif args.command == "demo":
        import subprocess
        from pathlib import Path
        script = Path("scripts/demo.sh")
        if not script.exists():
            print("Run `docker compose -f compose.demo.yml up --build` from a cloned Flagsmith Guardian repo.", file=sys.stderr)
            sys.exit(1)
        cmd = ["bash", str(script)] + (["--serve"] if args.serve else [])
        sys.exit(subprocess.call(cmd))


def _run_doctor(config_path: str) -> None:
    """Run local diagnostic checks for Guardian readiness."""
    from pathlib import Path
    from guardian.config import load_config
    from guardian.main import load_env_file

    load_env_file()
    print("🩺 Guardian Environment Doctor")
    ok = True
    print(f"  ✔ Python: {sys.version.split()[0]}")

    if Path(config_path).exists():
        try:
            cfg = load_config(config_path)
            print(f"  ✔ Config: {config_path} ({len(cfg.rollouts)} rollout(s))")
        except Exception as e:
            print(f"  ❌ Config invalid ({config_path}): {e}")
            ok = False
    else:
        print(f"  ⚠️  Config not found: {config_path} (run `guardian init` or copy `guardian.example.yaml`)")

    fs_url = os.environ.get("FLAGSMITH_URL", "")
    if fs_url:
        try:
            req = urllib.request.Request(fs_url.rstrip("/") + "/")
            with urllib.request.urlopen(req, timeout=4) as r:
                print(f"  ✔ Flagsmith API reachable: {fs_url} (HTTP {r.status})")
        except Exception as e:
            print(f"  ⚠️  Flagsmith API unreachable ({fs_url}): {e}")
    else:
        print("  ⚠️  FLAGSMITH_URL not set")

    prom_url = os.environ.get("PROMETHEUS_URL", "")
    if prom_url:
        try:
            with urllib.request.urlopen(prom_url.rstrip("/") + "/-/healthy", timeout=4) as r:
                print(f"  ✔ Prometheus reachable: {prom_url} (HTTP {r.status})")
        except Exception as e:
            print(f"  ⚠️  Prometheus unreachable ({prom_url}): {e}")

    token_set = bool(os.environ.get("GUARDIAN_API_TOKEN"))
    secret_set = bool(os.environ.get("FLAGSMITH_WEBHOOK_SECRET"))
    print(f"  {'✔' if token_set else '⚠️ '} GUARDIAN_API_TOKEN: {'set' if token_set else 'unset (required in production)'}")
    print(f"  {'✔' if secret_set else '⚠️ '} FLAGSMITH_WEBHOOK_SECRET: {'set' if secret_set else 'unset (required in production)'}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
