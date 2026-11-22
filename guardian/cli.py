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


def main():
    parser = argparse.ArgumentParser(description="Guardian CLI Operator Interface")
    parser.add_argument("--url", default="http://localhost:8080", help="Guardian service URL (default: http://localhost:8080)")
    parser.add_argument("--token", default=None, help="Guardian API token (or set GUARDIAN_API_TOKEN env var)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Approve subcommand
    approve_parser = subparsers.add_parser("approve", help="Approve final 100% promotion when awaiting approval")
    approve_parser.add_argument("--by", default="operator", help="Approver identity")
    approve_parser.add_argument("--reason", default="Approved final promotion", help="Approval rationale")

    # Status subcommand
    subparsers.add_parser("status", help="Get current rollout status")

    # Rollback subcommand
    rollback_parser = subparsers.add_parser("rollback", help="Force instant manual rollback to 0%")
    rollback_parser.add_argument("--reason", default="Manual operator rollback", help="Rollback reason")

    # Reset subcommand
    subparsers.add_parser("reset", help="Reset rollout back to initial PENDING state (demo use)")

    args = parser.parse_args()

    if args.command == "approve":
        print(f"==> Sending approval to Guardian at {args.url}...")
        res = call_api("/approve", base_url=args.url, data={"approved_by": args.by, "reason": args.reason}, token=args.token)
        print(f"✅ Success: {res.get('message')}")
        print(f"State: {res.get('current_state')} | Rollout: {res.get('current_percentage')}%")

    elif args.command == "status":
        res = call_api("/status", base_url=args.url, token=args.token)
        print(f"Flag   : {res.get('flag_name')}")
        print(f"State  : {res.get('state')} (Stage index: {res.get('stage_index')})")
        print(f"Rollout: {res.get('percentage')}%")
        print(f"Stages : {res.get('stages')}")

    elif args.command == "rollback":
        print(f"==> Sending rollback to Guardian at {args.url}...")
        res = call_api("/rollback", base_url=args.url, data={"reason": args.reason}, token=args.token)
        print(f"🛑 Rolled Back: {res.get('message')}")
        print(f"State: {res.get('current_state')} | Rollout: {res.get('current_percentage')}%")

    elif args.command == "reset":
        print(f"==> Sending reset to Guardian at {args.url}...")
        res = call_api("/reset", base_url=args.url, data={}, token=args.token)
        print(f"🔄 Reset: {res.get('message')}")
        print(f"State: {res.get('current_state')} | Rollout: {res.get('current_percentage')}%")


if __name__ == "__main__":
    main()
