"""
Optional MCP Server for Flagsmith Guardian.
Exposes read/control tools over stdio while strictly forbidding autonomous
100% promotion approval (human approval in the dashboard or CLI is required).
"""

from __future__ import annotations
import os
from typing import Any, Dict, Optional
import requests

APPROVAL_BOUNDARY_NOTE = (
    "Final 100% promotion cannot be approved via MCP; "
    "a human must approve in the dashboard or CLI."
)

TOOL_DEFINITIONS: Dict[str, str] = {
    "get_rollout_status": (
        "Get current rollout state, allocation percentage, guardrails, experiment gate, "
        f"and drift status. Read-only. {APPROVAL_BOUNDARY_NOTE}"
    ),
    "start_rollout": (
        "Start or re-arm a progressive rollout (resets to PENDING so Guardian advances "
        f"through configured stages). {APPROVAL_BOUNDARY_NOTE}"
    ),
    "pause": (
        "Pause an active rollout at its current percentage. "
        f"{APPROVAL_BOUNDARY_NOTE}"
    ),
    "resume": (
        "Resume a previously paused rollout. "
        f"{APPROVAL_BOUNDARY_NOTE}"
    ),
    "rollback": (
        "Immediately roll back the feature flag to 0% in Flagsmith. "
        f"Emergency stop only. {APPROVAL_BOUNDARY_NOTE}"
    ),
    "request_approval": (
        "Mark a rollout as approval requested when it is awaiting final promotion. "
        f"Does NOT approve 100% promotion; {APPROVAL_BOUNDARY_NOTE}"
    ),
}


class GuardianMCPClient:
    """Thin HTTP client for Guardian API authenticated via GUARDIAN_API_TOKEN."""

    def __init__(self, base_url: Optional[str] = None, api_token: Optional[str] = None, timeout: float = 10.0):
        self.base_url = (base_url or os.environ.get("GUARDIAN_URL") or "http://127.0.0.1:8080").rstrip("/")
        self.api_token = api_token if api_token is not None else os.environ.get("GUARDIAN_API_TOKEN", "")
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def get_rollout_status(self, flag_name: Optional[str] = None) -> Dict[str, Any]:
        params = {"flag_name": flag_name} if flag_name else None
        resp = requests.get(f"{self.base_url}/status", params=params, headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def start_rollout(self, flag_name: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if flag_name:
            body["flag_name"] = flag_name
        resp = requests.post(f"{self.base_url}/reset", json=body, headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def pause(self, flag_name: Optional[str] = None, reason: str = "Paused via MCP") -> Dict[str, Any]:
        body: Dict[str, Any] = {"reason": reason}
        if flag_name:
            body["flag_name"] = flag_name
        resp = requests.post(f"{self.base_url}/pause", json=body, headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def resume(self, flag_name: Optional[str] = None, reason: str = "Resumed via MCP") -> Dict[str, Any]:
        body: Dict[str, Any] = {"reason": reason}
        if flag_name:
            body["flag_name"] = flag_name
        resp = requests.post(f"{self.base_url}/resume", json=body, headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def rollback(self, flag_name: Optional[str] = None, reason: str = "Rollback triggered via MCP") -> Dict[str, Any]:
        body: Dict[str, Any] = {"reason": reason}
        if flag_name:
            body["flag_name"] = flag_name
        resp = requests.post(f"{self.base_url}/rollback", json=body, headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def request_approval(self, flag_name: Optional[str] = None, note: str = "") -> Dict[str, Any]:
        status_data = self.get_rollout_status(flag_name=flag_name)
        return {
            "status": "approval requested",
            "flag_name": status_data.get("flag_name") or flag_name,
            "current_state": status_data.get("state"),
            "current_percentage": status_data.get("percentage"),
            "is_eligible_for_approval": status_data.get("is_eligible_for_approval"),
            "note": note,
            "message": "approval requested: a human must approve in the dashboard or CLI",
        }


def create_mcp_server(base_url: Optional[str] = None, api_token: Optional[str] = None):
    """Build an MCP server exposing Guardian rollout controls without any approval tool."""
    try:
        from mcp.server.mcpserver import MCPServer as _ServerCls
    except ImportError:
        from mcp.server.fastmcp import FastMCP as _ServerCls

    client = GuardianMCPClient(base_url=base_url, api_token=api_token)
    mcp = _ServerCls("flagsmith-guardian")

    @mcp.tool(description=TOOL_DEFINITIONS["get_rollout_status"])
    def get_rollout_status(flag_name: Optional[str] = None) -> Dict[str, Any]:
        return client.get_rollout_status(flag_name=flag_name)

    @mcp.tool(description=TOOL_DEFINITIONS["start_rollout"])
    def start_rollout(flag_name: Optional[str] = None) -> Dict[str, Any]:
        return client.start_rollout(flag_name=flag_name)

    @mcp.tool(description=TOOL_DEFINITIONS["pause"])
    def pause(flag_name: Optional[str] = None, reason: str = "Paused via MCP") -> Dict[str, Any]:
        return client.pause(flag_name=flag_name, reason=reason)

    @mcp.tool(description=TOOL_DEFINITIONS["resume"])
    def resume(flag_name: Optional[str] = None, reason: str = "Resumed via MCP") -> Dict[str, Any]:
        return client.resume(flag_name=flag_name, reason=reason)

    @mcp.tool(description=TOOL_DEFINITIONS["rollback"])
    def rollback(flag_name: Optional[str] = None, reason: str = "Rollback triggered via MCP") -> Dict[str, Any]:
        return client.rollback(flag_name=flag_name, reason=reason)

    @mcp.tool(description=TOOL_DEFINITIONS["request_approval"])
    def request_approval(flag_name: Optional[str] = None, note: str = "") -> Dict[str, Any]:
        return client.request_approval(flag_name=flag_name, note=note)

    return mcp


def run_mcp_server(base_url: Optional[str] = None, api_token: Optional[str] = None) -> None:
    """Run the Guardian MCP server over stdio."""
    server = create_mcp_server(base_url=base_url, api_token=api_token)
    server.run(transport="stdio")
