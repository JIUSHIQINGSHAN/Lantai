# Remote Lantai MCP bridge

`lantai-mcp-bridge` exposes a remote Lantai server to an MCP host over UTF-8 stdio. It does not read `.env`, open a local database, or start a local server.

Set `LANTAI_BRIDGE_BASE_URL` to the HTTPS API root, and set exactly one of `LANTAI_BRIDGE_BEARER_TOKEN` or `LANTAI_BRIDGE_API_KEY`. Do not put credentials on the command line, in a profile file committed to source control, or in logs. Request timeout defaults to ten seconds; `LANTAI_BRIDGE_TIMEOUT_S` may be set from 0.05 to 55 seconds.

Example MCP server entry (provide the token through your host's secret manager):

```json
{
  "mcpServers": {
    "lantai-remote": {
      "command": "lantai-mcp-bridge",
      "env": {
        "LANTAI_BRIDGE_BASE_URL": "https://lantai.example.invalid",
        "LANTAI_BRIDGE_BEARER_TOKEN": "<secret-manager-injected>"
      }
    }
  }
}
```

Plain HTTP is rejected. For local development only, use a literal `127.0.0.1` or `[::1]` base URL and explicitly set `LANTAI_BRIDGE_ALLOW_LOOPBACK_HTTP=1`; DNS names, redirects, URL credentials, query strings and fragments remain rejected. `X-Session-Id` is an attribution/echo label on supported calls, never an authorization boundary.

The standalone bridge intentionally has its own allowlisted tool mapping. After changing exposed tools, schemas or routes, run `tests/test_mcp_bridge.py`; it starts a real stdio subprocess and a loopback HTTP peer without external network access.
