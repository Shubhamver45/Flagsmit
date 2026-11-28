# Connecting Guardian to Coding Agents via MCP (`guardian mcp`)

Guardian includes an optional [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server so coding agents like **Claude Code** can inspect rollouts, pause/resume, trigger emergency rollbacks, and request human review — **without ever allowing an AI agent to approve a 100% production promotion**.

---

## 1. Install the Optional Extra

The MCP server is packaged as an optional extra so Guardian's default container image and dependencies remain unchanged:

```bash
pip install "flagsmith-guardian[mcp]"
# Or from a local clone:
pip install -e ".[mcp]"
```

---

## 2. Available MCP Tools & Human-Approval Boundary

`guardian mcp` exposes:
- `get_rollout_status` — read current state, percentage, guardrails, experiment gate, and drift status.
- `start_rollout` — start or re-arm a rollout (`PENDING`).
- `pause` — pause an active rollout at its current percentage.
- `resume` — resume a paused rollout.
- `rollback` — immediately roll the Flagsmith flag back to `0%`.
- `request_approval` — marks the rollout as `approval requested` and returns `"a human must approve in the dashboard or CLI"`.

> [!IMPORTANT]
> There is intentionally **no** `approve` tool in `guardian mcp`. Final 100% promotion can only be approved by a human operator via the Guardian web dashboard or `guardian approve` CLI.

---

## 3. Connect to Claude Code

Ensure Guardian is running (e.g. `http://127.0.0.1:8080`) and export your API token, then register the server with Claude Code:

```bash
claude mcp add flagsmith-guardian \
  -e GUARDIAN_URL=http://127.0.0.1:8080 \
  -e GUARDIAN_API_TOKEN=your-guardian-api-token \
  -- guardian mcp
```

Or add it to your project's `.mcp.json` (never commit real tokens):

```json
{
  "mcpServers": {
    "flagsmith-guardian": {
      "command": "guardian",
      "args": ["mcp"],
      "env": {
        "GUARDIAN_URL": "http://127.0.0.1:8080",
        "GUARDIAN_API_TOKEN": "${GUARDIAN_API_TOKEN}"
      }
    }
  }
}
```
