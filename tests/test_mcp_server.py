"""
Tests for the optional Guardian MCP server (guardian mcp).
Confirms no final 100% approval tool exists, tool descriptions state the
human-approval boundary, auth uses GUARDIAN_API_TOKEN, and request_approval
only marks approval requested.
"""

from unittest.mock import MagicMock, patch
import asyncio
import pytest

from guardian.mcp_server import APPROVAL_BOUNDARY_NOTE, TOOL_DEFINITIONS, GuardianMCPClient

try:
    import mcp  # noqa: F401
    HAS_MCP = True
except ImportError:
    HAS_MCP = False


def test_no_approval_tool_and_descriptions_state_human_boundary():
    expected_tools = {
        "get_rollout_status",
        "start_rollout",
        "pause",
        "resume",
        "rollback",
        "request_approval",
    }
    assert set(TOOL_DEFINITIONS.keys()) == expected_tools

    # Ensure no tool exists to approve 100% promotion
    for name in TOOL_DEFINITIONS:
        assert not name.startswith("approve"), f"Forbidden approval tool found: {name}"
        assert "approve_promotion" not in name

    # Every tool description must state that a human must approve in the dashboard or CLI
    for name, desc in TOOL_DEFINITIONS.items():
        assert "a human must approve in the dashboard or cli" in desc.lower(), (
            f"Tool '{name}' description missing human approval boundary: {desc}"
        )
        assert APPROVAL_BOUNDARY_NOTE in desc


def test_mcp_client_uses_env_token_and_request_approval_never_approves(monkeypatch):
    monkeypatch.setenv("GUARDIAN_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "mcp-secret-token")
    client = GuardianMCPClient()

    fake_get_resp = MagicMock()
    fake_get_resp.json.return_value = {
        "flag_name": "new_checkout",
        "state": "AWAITING_APPROVAL",
        "percentage": 25.0,
        "is_eligible_for_approval": True,
    }
    fake_post_resp = MagicMock()
    fake_post_resp.json.return_value = {"status": "paused", "current_state": "PAUSED"}

    with patch("guardian.mcp_server.requests.get", return_value=fake_get_resp) as mock_get, \
         patch("guardian.mcp_server.requests.post", return_value=fake_post_resp) as mock_post:
        res_req = client.request_approval(flag_name="new_checkout", note="Ready for review")
        assert res_req["status"] == "approval requested"
        assert "a human must approve in the dashboard or CLI" in res_req["message"]
        # request_approval must never POST to /approve
        mock_post.assert_not_called()
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer mcp-secret-token"

        client.pause(flag_name="new_checkout")
        assert mock_post.call_args.args[0].endswith("/pause")
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer mcp-secret-token"


@pytest.mark.skipif(not HAS_MCP, reason="optional mcp extra not installed")
def test_fastmcp_server_registers_only_safe_tools():
    from guardian.mcp_server import create_mcp_server

    server = create_mcp_server(base_url="http://127.0.0.1:8080", api_token="tok")
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "get_rollout_status",
        "start_rollout",
        "pause",
        "resume",
        "rollback",
        "request_approval",
    }
    assert "approve" not in names
    for t in tools:
        assert "a human must approve in the dashboard or cli" in (t.description or "").lower()
