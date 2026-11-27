"""Standalone MCP App resource for the read-only Xuanjing topology explorer."""

from copy import deepcopy
from importlib.resources import files

RESOURCE_URI = "ui://lantai/xuanjing-observatory"
RESOURCE_MIME_TYPE = "text/html;profile=mcp-app"

RESOURCE_METADATA = {
    "ui": {
        "csp": {
            "connectDomains": [],
            "resourceDomains": [],
            "frameDomains": [],
            "baseUriDomains": [],
        },
        "prefersBorder": True,
    }
}

RESOURCE_DESCRIPTOR = {
    "uri": RESOURCE_URI,
    "name": "悬镜拓扑观测台",
    "description": "只读探索悬镜记忆节点、关系边与时间脉络。",
    "mimeType": RESOURCE_MIME_TYPE,
    "_meta": RESOURCE_METADATA,
}

TOOL_UI_METADATA = {
    "ui": {
        "resourceUri": RESOURCE_URI,
        "visibility": ["model", "app"],
    }
}


def list_xuanjing_app_resources() -> list[dict[str, object]]:
    """Return this app's resource declaration for the MCP App registry."""
    return [deepcopy(RESOURCE_DESCRIPTOR)]


def read_xuanjing_app_resource(uri: str) -> dict[str, list[dict[str, object]]]:
    """Read the packaged HTML resource using the MCP `resources/read` shape."""
    if uri != RESOURCE_URI:
        raise ValueError(f"unknown Xuanjing app resource: {uri}")

    html = files("lantai.api").joinpath("ui", "xuanjing_mcp_app.html").read_text(encoding="utf-8")
    return {
        "contents": [
            {
                "uri": RESOURCE_URI,
                "mimeType": RESOURCE_MIME_TYPE,
                "text": html,
                "_meta": deepcopy(RESOURCE_METADATA),
            }
        ]
    }
