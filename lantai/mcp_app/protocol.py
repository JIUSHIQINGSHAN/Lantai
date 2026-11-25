"""MCP Apps resource registration for the stdio MCP server."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from importlib.resources import files

MCP_APPS_EXTENSION = "io.modelcontextprotocol/ui"
MCP_APPS_MIME_TYPE = "text/html;profile=mcp-app"
MCP_APP_RESOURCE_URI = "ui://lantai/memory-panel"
MCP_APP_TOOL_NAMES = frozenset(
    {"search", "core_memory_get", "mem_recent", "crystals_list", "crystal_decide"}
)
_CSP = {
    "connectDomains": [],
    "resourceDomains": [],
    "frameDomains": [],
    "baseUriDomains": [],
}


@dataclass(slots=True)
class McpAppSession:
    """Negotiated extension state scoped to one stdio connection."""

    supports_mcp_apps: bool = False


def client_supports_mcp_apps(capabilities: object) -> bool:
    if not isinstance(capabilities, dict):
        return False
    extensions = capabilities.get("extensions")
    if not isinstance(extensions, dict):
        return False
    app_caps = extensions.get(MCP_APPS_EXTENSION)
    if not isinstance(app_caps, dict):
        return False
    mime_types = app_caps.get("mimeTypes")
    return isinstance(mime_types, list) and MCP_APPS_MIME_TYPE in mime_types


def app_tools(active_tools: dict, *, enabled: bool) -> list[dict]:
    """Copy the active registry and attach UI metadata without changing schemas."""
    tools = []
    for name, definition in active_tools.items():
        tool = deepcopy({"name": name, **definition})
        if enabled and name in MCP_APP_TOOL_NAMES:
            tool.setdefault("_meta", {}).setdefault("ui", {})["resourceUri"] = MCP_APP_RESOURCE_URI
        tools.append(tool)
    return tools


def app_resources(*, enabled: bool) -> list[dict]:
    if not enabled:
        return []
    return [
        {
            "uri": MCP_APP_RESOURCE_URI,
            "name": "兰台记忆面板",
            "description": "记忆总览、语义检索与结晶候选裁决",
            "mimeType": MCP_APPS_MIME_TYPE,
        }
    ]


def read_app_resource(uri: object) -> dict:
    if uri != MCP_APP_RESOURCE_URI:
        raise ValueError("unknown resource")

    package = files("lantai.mcp_app")
    template = package.joinpath("ui/index.html").read_text(encoding="utf-8")
    styles = package.joinpath("ui/styles.css").read_text(encoding="utf-8")
    script = package.joinpath("ui/app.js").read_text(encoding="utf-8")
    if (
        template.count("<!-- LANTAI_STYLES -->") != 1
        or template.count("<!-- LANTAI_SCRIPT -->") != 1
    ):
        raise RuntimeError("invalid MCP App resource template")
    html = template.replace("<!-- LANTAI_STYLES -->", f"<style>\n{styles}\n</style>")
    html = html.replace("<!-- LANTAI_SCRIPT -->", f"<script>\n{script}\n</script>")
    return {
        "uri": MCP_APP_RESOURCE_URI,
        "mimeType": MCP_APPS_MIME_TYPE,
        "text": html,
        "_meta": {"ui": {"csp": deepcopy(_CSP)}},
    }
