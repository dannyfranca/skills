---
name: mcp-cli
description: Call an approved HTTPS MCP endpoint through a scoped CLI. Use when another skill needs MCP without registering server schemas in the agent context.
---

# MCP CLI

Use `scripts/mcp.py` from this skill. The calling skill owns the endpoint, transport, tool choice, and investigation workflow.

```bash
python3 "$HOME/.agents/skills/mcp-cli/scripts/mcp.py" --transport streamable-http --url "https://approved.kraken.zone/mcp" tools
python3 "$HOME/.agents/skills/mcp-cli/scripts/mcp.py" --transport streamable-http --url "https://approved.kraken.zone/mcp" describe tool_name
python3 "$HOME/.agents/skills/mcp-cli/scripts/mcp.py" --transport streamable-http --url "https://approved.kraken.zone/mcp" call tool_name --args '{"key":"value"}'
```

Pass credentials by environment-variable reference, never by value:

```bash
python3 "$HOME/.agents/skills/mcp-cli/scripts/mcp.py" --transport streamable-http --url "https://mcp.linearb.io/mcp" --header-env "x-api-key=LINEARB_API_KEY" tools
```

Run with full network access, outside the sandbox; internal endpoints require VPN. Retry one network or certificate failure outside the sandbox before reporting a blocker.

Use only an endpoint fixed by a trusted calling skill. `tools` returns names only; `describe` loads one schema; `call` exposes one result. The CLI accepts `*.kraken.zone` and explicitly allowlisted vendor endpoints. The calling skill owns mutation approval and safety.

Use `--transport sse` only for legacy MCP endpoints that advertise a session-specific message URL.
