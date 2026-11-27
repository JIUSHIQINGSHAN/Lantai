# DSH Lantai session attribution

This review bundle replaces the shipped `include:mcp-lantai` MCP client entry with a Host-owned bridge that adds one attribution object to every MCP `tools/call` request:

```json
{
  "params": {
    "_meta": {
      "io.lantai/attribution": {
        "version": 1,
        "request_session_id": "<ToolExecution.agent.id>",
        "request_id": "<ToolExecution.callId>"
      }
    }
  }
}
```

The bundle is **disabled by default** and this worktree does not install or activate it. It supports one statically configured `stdio` or loopback/HTTP(S) Streamable HTTP server, stable `mcp__<server>__<tool>` names, DSH output-schema validation and content projection through `createMcpToolDefinition`, per-call cancellation and timeout, bounded startup/teardown, tool-list refresh, and bounded reconnect attempts. Tool arguments are passed through unchanged; the bridge never writes attribution into the model argument object and never modifies `tools/execute` identity fields. Attribution is an untrusted label and does not grant access.

## Install and switch

Before switching, deploy the backend implementation with schema v46 and the reviewed `scripts/lantai_mcp_bridge.py` to the existing bridge location, retaining its original adjacent `lantai_remote.conf` and a rollback copy. This repository does not copy or read that config. Merely reusing the old bridge command without updating its script silently discards protocol `_meta`; metadata support in the Host alone cannot repair that hop. Confirm the new bridge/backend chain in isolation first. These are deployment prerequisites; this worktree has not performed them.

1. Install this directory from **Plugins > Add plugin** (or use the plugin manager with the absolute workspace directory). Do not enable the row yet.
2. In the profile, disable the shipped row whose id is `include:mcp-lantai`.
3. Configure the inserted `lantai-attribution` row with the same server transport settings, then enable it. The inserted row remains disabled until explicitly enabled.
4. Start a new session and verify that the Lantai server sees the `_meta` object. Keep only one of the two rows enabled; otherwise tool registration fails instead of shadowing another bridge.

The default patch points at `http://127.0.0.1:3000/mcp` as a harmless loopback placeholder and uses `failOnStartupError: true`. Change it to the actual local loopback endpoint or a static stdio command. `${credential:...}` templates and `expandTemplates: true` are intentionally rejected: this bundle does not read the profile, credentials, or ambient DSH configuration. Do not put secrets in the package or patch. HTTP headers are sent to the configured endpoint and are redacted from error text.

Turning off or unloading the row closes the MCP transport, waits within `closeTimeoutMs`, unregisters its tools, and cancels pending calls. If cleanup cannot be confirmed, the bridge stops reconnecting and reports that the Host should be restarted before replacement. Discovery failures and duplicate tool lists preserve the last registered generation; registration conflicts fail without publishing a partial set.

Discovery retains the installed SDK's pagination, schema/header checks, cache handling and 64-page limit. Repeated continuation cursors fail the entire refresh instead of allowing the SDK's repeated-page shortcut to publish an incomplete tool set. One timeout covers all discovery pages, and a failed refresh keeps the previously registered tools.

The Python REST bridge reports HTTP/domain failures with MCP `isError`, while keeping HTTP error bodies and credentials out of responses. Notifications produce no response frame. Its HTTP dispatch is synchronous: cancelling the SDK wait does **not** prove an already accepted write was undone; check the persisted result before retrying. Attribution is attached to explicit tool calls; this bundle does not automatically capture every dialogue turn.

## Verification

For a reviewed local six-tool entry, `integrations/mirasim-lantai/configure.py --host dsh --home <absolute-data-directory>` previews a disabled stdio row for this adapter. It uses the selected Python environment and current checkout without reading a profile or embedding credentials. See the sibling package's `ADAPTERS.md` for the scope and configuration contract.

`tests/test_adapter_claude_codex_dsh.py` also drives that generated entry through the actual installed registry, SDK and isolated SQLite. Select the DSH package root containing `node_modules/@deepseek-ai/dsh-system-prompt` as `DSH_INSTALL_DIR` (in current desktop builds this is the `dsh` directory inside `app.asar`, rather than the ASAR root). The test supplies synthetic credentials; production credentials stay outside generated files.

The tests use the Electron runtime selected by `DSH_NODE_EXECUTABLE` and resolve packages from `DSH_INSTALL_DIR`, so the bundle has no absolute installation-path dependency:

```powershell
& ./integrations/dsh-lantai/tests/run-installed.ps1
```

Use the installed Electron executable rather than `node` from `PATH` when reading the ASAR installation. The real local HTTP JSON-RPC fixture exercises the SDK transport, DSH registry, concurrent calls, attribution fields, immutable arguments, schema validation, cancellation, timeout, redaction, atomic discovery refresh, duplicate-list rollback, and teardown.

`tests/test_dsh_attribution_chain.py` additionally runs this adapter through the installed SDK, a real stdio Python bridge, actual FastAPI routes, and isolated SQLite/FTS/Chroma storage. This crosses an external installed runtime and is opt-in: set `LANTAI_TEST_DSH_CHAIN=1`, `DSH_NODE_EXECUTABLE`, and `DSH_INSTALL_DIR`, then run from the repository's test environment. Without the opt-in, the test reports an environment skip and starts no service. If Electron cannot run a basic Node expression, diagnose that runtime before running the chain; a timeout is a failed test, not permission to change product behavior. To disable dotenv loading before importing the application:

```powershell
python -c "import pydantic_settings.sources as s; s.DotEnvSettingsSource._read_env_files=lambda self:{}; import pytest; raise SystemExit(pytest.main(['tests/test_dsh_attribution_chain.py','-q']))"
```

The chain test skips explicitly when installation variables are absent. It uses synthetic Host execution objects and a synthetic authenticated Principal; it verifies the adapter and storage chain, not a real GUI turn or live authentication. Concurrent A/B calls share a stdio bridge whose REST requests are processed serially. The test also covers historical-origin conflicts, agentless calls, dedup into an existing item, preserved read scope, rejected input, and failure propagation. The child receives only in-memory synthetic server settings and restricts connections to loopback; deployed conf/profile files are not opened.
