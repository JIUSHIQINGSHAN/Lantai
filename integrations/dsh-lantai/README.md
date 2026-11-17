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

1. Install this directory from **Plugins > Add plugin** (or use the plugin manager with the absolute workspace directory). Do not enable the row yet.
2. In the profile, disable the shipped row whose id is `include:mcp-lantai`.
3. Configure the inserted `lantai-attribution` row with the same server transport settings, then enable it. The inserted row remains disabled until explicitly enabled.
4. Start a new session and verify that the Lantai server sees the `_meta` object. Keep only one of the two rows enabled; otherwise tool registration fails instead of shadowing another bridge.

The default patch points at `http://127.0.0.1:3000/mcp` as a harmless loopback placeholder and uses `failOnStartupError: true`. Change it to the actual local loopback endpoint or a static stdio command. `${credential:...}` templates and `expandTemplates: true` are intentionally rejected: this bundle does not read the profile, credentials, or ambient DSH configuration. Do not put secrets in the package or patch. HTTP headers are sent to the configured endpoint and are redacted from error text.

Turning off or unloading the row closes the MCP transport, waits within `closeTimeoutMs`, unregisters its tools, and cancels pending calls. If cleanup cannot be confirmed, the bridge stops reconnecting and reports that the Host should be restarted before replacement. Discovery failures and duplicate tool lists preserve the last registered generation; registration conflicts fail without publishing a partial set.

## Verification

The tests use the Electron runtime selected by `DSH_NODE_EXECUTABLE` and resolve packages from `DSH_INSTALL_DIR`, so the bundle has no absolute installation-path dependency:

```powershell
& ./integrations/dsh-lantai/tests/run-installed.ps1
```

Use the installed Electron executable rather than `node` from `PATH` when reading the ASAR installation. The real local HTTP JSON-RPC fixture exercises the SDK transport, DSH registry, concurrent calls, attribution fields, immutable arguments, schema validation, cancellation, timeout, redaction, atomic discovery refresh, duplicate-list rollback, and teardown.
