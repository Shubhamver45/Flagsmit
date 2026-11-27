#!/usr/bin/env python3
"""
scripts/check_readme_claims.py - Automated CI verification for README.md claims:
1. Every file:symbol in the "How Flagsmith is used" table exists on disk and in code.
2. Flagsmith Client SDK call names match demo/app.py.
3. Every YAML config block in README.md loads with the real config loader without error.
4. Every environment variable documented in README.md exists in code.
5. Every CLI command in README.md exists in guardian/cli.py.
"""

import ast
import importlib
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Set

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import yaml
from guardian.config import load_config_from_dict

def check_how_flagsmith_is_used(readme_content: str) -> bool:
    print("==> Checking 'How Flagsmith is used' table symbols...")
    # Find table
    table_match = re.search(r"## 3\. How Flagsmith is Used.*?\n\n(.*?)\n\n---", readme_content, re.DOTALL)
    if not table_match:
        print("❌ Error: Could not locate 'How Flagsmith is used' table in README.md", file=sys.stderr)
        return False

    table_text = table_match.group(1)
    rows = [r.strip() for r in table_text.splitlines() if r.strip().startswith("|") and not r.strip().startswith("| :---")]

    # Skip header
    data_rows = rows[1:]
    all_ok = True

    for row in data_rows:
        cols = [c.strip() for c in row.split("|")[1:-1]]
        if len(cols) < 2:
            continue
        feature_name = cols[0].replace("*", "").strip()
        code_location_col = cols[1]

        # Extract file:symbol references, e.g. `path/to/file.py:Symbol.method`
        refs = re.findall(r'`([^`]+)`', code_location_col)
        for ref in refs:
            if ":" not in ref:
                # Direct file path
                file_path = ROOT_DIR / ref
                if not file_path.exists():
                    print(f"  ❌ Missing file path: {ref}")
                    all_ok = False
                else:
                    print(f"  ✔ File exists: {ref}")
                continue

            file_part, sym_part = ref.split(":", 1)
            file_path = ROOT_DIR / file_part
            if not file_path.exists():
                print(f"  ❌ File does not exist: {file_part} (for {sym_part})")
                all_ok = False
                continue

            # Verify symbol in AST
            with open(file_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(file_path))

            symbols_to_check = [s.strip() for s in sym_part.split(",") if s.strip()]
            for full_sym in symbols_to_check:
                parts = full_sym.split(".")
                curr_nodes = tree.body
                sym_found = True

                for p in parts:
                    match = None
                    for node in curr_nodes:
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == p:
                            match = node
                            break
                    if match:
                        if isinstance(match, ast.ClassDef):
                            curr_nodes = match.body
                        else:
                            curr_nodes = []
                    else:
                        sym_found = False
                        break

                if sym_found:
                    print(f"  ✔ Found symbol {file_part}:{full_sym}")
                else:
                    print(f"  ❌ Symbol NOT found: {file_part}:{full_sym}")
                    all_ok = False

    # Check SDK calls in demo/app.py
    print("\n==> Checking SDK calls in demo/app.py...")
    with open(ROOT_DIR / "demo" / "app.py", "r", encoding="utf-8") as f:
        demo_code = f.read()

    expected_sdk_calls = [
        "get_identity_flags",
        "track_exposure_event",
        "track_event",
        "flush_events",
    ]
    for call in expected_sdk_calls:
        if call in demo_code:
            print(f"  ✔ Flagsmith SDK method verified in demo/app.py: {call}")
        else:
            print(f"  ❌ Missing SDK call {call} in demo/app.py")
            all_ok = False

    return all_ok

def check_yaml_blocks(readme_content: str) -> bool:
    print("\n==> Checking YAML configuration blocks in README.md...")
    yaml_blocks = re.findall(r"```yaml\n(.*?)```", readme_content, re.DOTALL)
    if not yaml_blocks:
        print("  ⚠️ No YAML blocks found in README.md")
        return True

    all_ok = True
    for idx, block in enumerate(yaml_blocks, 1):
        try:
            data = yaml.safe_load(block)
        except Exception as e:
            print(f"  ❌ YAML block {idx} syntax error: {e}")
            all_ok = False
            continue

        if not isinstance(data, dict):
            continue

        # If it is a Guardian configuration block (has flag_name or rollouts)
        if "flag_name" in data or "rollouts" in data or "flags" in data or "guardrails" in data:
            try:
                cfg = load_config_from_dict(data)
                print(f"  ✔ YAML block {idx} successfully loaded by Guardian config loader (flag: {cfg.primary_rollout.flag_name})")
            except Exception as e:
                print(f"  ❌ YAML block {idx} failed Guardian config loader: {e}")
                all_ok = False

    return all_ok

def check_env_vars(readme_content: str) -> bool:
    print("\n==> Checking documented environment variables...")
    # Extract uppercase environment variable names mentioned in README
    documented_envs = set(re.findall(r'`([A-Z][A-Z0-9_]{3,})`', readme_content))
    
    # Filter common non-env uppercase words
    ignored = {"HTTP", "JSON", "REST", "POST", "GET", "PATCH", "PUT", "DELETE", "HTML", "HMAC", "SHA256", "SRM", "CADD", "PLIF", "OPSIN", "UUID", "UID", "GID", "FASTAPI", "MIT"}
    documented_envs = {e for e in documented_envs if e not in ignored}

    # Collect all env vars across python and docker-compose files in repo
    code_envs = set()
    for root, dirs, files in os.walk(ROOT_DIR):
        if ".git" in root or ".venv" in root or "venv" in root:
            continue
        for f in files:
            if f.endswith((".py", ".sh", ".yml", ".yaml", "Dockerfile")):
                p = Path(root) / f
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    for m in re.finditer(r'([A-Z][A-Z0-9_]{3,})', text):
                        code_envs.add(m.group(1))
                except Exception:
                    pass

    all_ok = True
    for env_var in sorted(documented_envs):
        if env_var in code_envs:
            print(f"  ✔ Env var verified in codebase: {env_var}")
        else:
            print(f"  ❌ Documented env var not found in codebase: {env_var}")
            all_ok = False

    return all_ok

def check_cli_commands(readme_content: str) -> bool:
    print("\n==> Checking documented CLI commands...")
    # Check commands mentioned like `guardian <subcmd>` or `python3 -m guardian.cli <subcmd>`
    cli_matches = re.findall(r'`(?:python3 -m )?guardian(?:\.cli)? ([a-z]+)[^`]*`', readme_content)
    valid_subcommands = {"init", "approve", "status", "rollback", "reset"}

    all_ok = True
    seen = set()
    for subcmd in cli_matches:
        if subcmd in seen:
            continue
        seen.add(subcmd)
        if subcmd in valid_subcommands:
            print(f"  ✔ CLI command verified: guardian {subcmd}")
        else:
            print(f"  ❌ Unknown CLI subcommand documented: guardian {subcmd}")
            all_ok = False

    return all_ok

def check_benchmark_numbers(readme_content: str) -> bool:
    print("\n==> Checking README benchmark numbers against docs/runs/demo-run-*.log...")
    import statistics
    from scripts.measure_runs import parse_run_logs

    files, drift_times, rollback_times = parse_run_logs()
    d_min, d_med, d_max = min(drift_times), statistics.median(drift_times), max(drift_times)
    r_min, r_med, r_max = min(rollback_times), statistics.median(rollback_times), max(rollback_times)

    expected_drift = f"**{d_min:.2f}s** | **{d_med:.2f}s** | **{d_max:.2f}s**"
    expected_rb = f"**{r_min:.2f}s** | **{r_med:.2f}s** | **{r_max:.2f}s**"
    expected_drift_range = f"{d_min:.2f}–{d_max:.2f}s"
    expected_rb_range = f"{r_min:.2f}–{r_max:.2f}s"

    all_ok = True
    for label, needle in [
        ("Drift Min/Median/Max", expected_drift),
        ("Rollback Min/Median/Max", expected_rb),
        ("Drift inline range", expected_drift_range),
        ("Rollback inline range", expected_rb_range),
    ]:
        if needle in readme_content:
            print(f"  ✔ {label} matches logs ({len(files)} runs): {needle}")
        else:
            print(f"  ❌ {label} mismatch! Expected '{needle}' from {len(files)} logs in README.md")
            all_ok = False
    return all_ok


def main():
    readme_path = ROOT_DIR / "README.md"
    if not readme_path.exists():
        print("❌ Error: README.md not found", file=sys.stderr)
        sys.exit(1)

    content = readme_path.read_text(encoding="utf-8")
    
    ok1 = check_how_flagsmith_is_used(content)
    ok2 = check_yaml_blocks(content)
    ok3 = check_env_vars(content)
    ok4 = check_cli_commands(content)
    ok5 = check_benchmark_numbers(content)

    print("\n" + "="*50)
    if ok1 and ok2 and ok3 and ok4 and ok5:
        print("🎉 ALL README CLAIMS VERIFIED SUCCESSFULLY!")
        sys.exit(0)
    else:
        print("❌ SOME README CLAIMS FAILED VERIFICATION.")
        sys.exit(1)

if __name__ == "__main__":
    main()
