"""
Guardian CLI Command-Line Interface.
Provides single-line CLI commands for operators, including `guardian approve`.
"""

from __future__ import annotations
import argparse
import json
import sys
import urllib.request
import urllib.error


def call_api(endpoint: str, base_url: str = "http://localhost:8080", data: dict = None) -> dict:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data or {}).encode("utf-8") if data is not None else None

    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
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

    args = parser.parse_args()

    if args.command == "approve":
        print(f"==> Sending approval to Guardian at {args.url}...")
        res = call_api("/approve", base_url=args.url, data={"approved_by": args.by, "reason": args.reason})
        print(f"✅ Success: {res.get('message')}")
        print(f"State: {res.get('current_state')} | Rollout: {res.get('current_percentage')}%")

    elif args.command == "status":
        res = call_api("/status", base_url=args.url)
        print(f"Flag   : {res.get('flag_name')}")
        print(f"State  : {res.get('state')} (Stage index: {res.get('stage_index')})")
        print(f"Rollout: {res.get('percentage')}%")
        print(f"Stages : {res.get('stages')}")

    elif args.command == "rollback":
        print(f"==> Sending rollback to Guardian at {args.url}...")
        res = call_api("/rollback", base_url=args.url, data={"reason": args.reason})
        print(f"🛑 Rolled Back: {res.get('message')}")
        print(f"State: {res.get('current_state')} | Rollout: {res.get('current_percentage')}%")


if __name__ == "__main__":
    main()
