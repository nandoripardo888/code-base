"""Experimental MCP Apps image viewer. No public image server or persistent cache."""

from __future__ import annotations

import base64
from importlib.resources import files

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

from code_harness.errors import InvalidArgumentError
from code_harness.paths import PathGuard
from code_harness.tools.read import IMAGE_MIME_TYPES, MAX_IMAGE_BYTES

UI_URI = "ui://code-harness/image-card-v3.html"
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 8
UI_META = {
    "ui": {"prefersBorder": False, "csp": {"connectDomains": [], "resourceDomains": []}},
    "openai/widgetPrefersBorder": False,
    "openai/widgetDescription": (
        "Minimal image preview or carousel. Clicking an image requests host fullscreen. "
        "Do not claim it appeared unless the user confirms."
    ),
    "openai/widgetCSP": {"connect_domains": [], "resource_domains": []},
}


def register_image_resource(server: MCPServer) -> None:
    @server.resource(
        UI_URI,
        name="image_card",
        description="Experimental minimal image preview",
        mime_type="text/html;profile=mcp-app",
        meta=UI_META,
    )
    def image_card_html() -> str:
        return files("code_harness.mcp").joinpath("image_card.html").read_text(encoding="utf-8")


def image_card(
    guard: PathGuard, path: str | list[str], title: str | None, titles: list[str] | None = None
) -> CallToolResult:
    paths = [path] if isinstance(path, str) else path
    if not 1 <= len(paths) <= MAX_IMAGES:
        raise InvalidArgumentError(f"Provide 1 to {MAX_IMAGES} image paths.")
    if title is not None and (not title.strip() or len(title) > 160):
        raise InvalidArgumentError("title must contain 1 to 160 characters.")
    if titles is not None and (
        len(titles) != len(paths) or any(not label.strip() or len(label) > 160 for label in titles)
    ):
        raise InvalidArgumentError(
            "titles must match the image count, each with 1 to 160 characters."
        )
    images = []
    total = 0
    for item in paths:
        payload, size = _read_image(guard, item)
        total += size
        if total > MAX_TOTAL_BYTES:
            raise InvalidArgumentError("Combined images must be at most 8 MiB.")
        images.append(payload)
    caption = (
        title.strip()
        if title is not None
        else (images[0]["title"] if len(images) == 1 else "Imagens")
    )
    if titles is not None:
        for payload, label in zip(images, titles, strict=True):
            payload["title"] = label.strip()
    elif len(images) == 1:
        images[0]["title"] = caption
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    f"Image card prepared: {caption} ({len(images)} image(s)). "
                    "Client rendering is unverified; ask the user whether the image is visible."
                ),
            )
        ],
        _meta={"imageCard": {"title": caption, "images": images}},
    )


def _read_image(guard: PathGuard, path: str) -> tuple[dict[str, str], int]:
    resolved = guard.resolve(path, kind="file")
    mime = IMAGE_MIME_TYPES.get(resolved.suffix.lower())
    if mime is None:
        raise InvalidArgumentError("ShowImage supports PNG, JPEG, GIF and WebP files only.")
    # Bound the actual read, including files that grow after stat().
    with resolved.open("rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise InvalidArgumentError("Image must be nonempty and at most 4 MiB.")
    payload = {
        "title": resolved.stem[:160],
        "mimeType": mime,
        "dataUrl": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}",
    }
    return payload, len(data)
