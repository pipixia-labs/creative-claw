"""Local browser-based web chat channel for CreativeClaw."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import html
import json
import mimetypes
import re
import tempfile
import uuid
import webbrowser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http import HTTPStatus
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from websockets.exceptions import ConnectionClosed
from websockets.datastructures import Headers
from websockets.legacy.server import WebSocketServerProtocol, serve

from conf.channel import WebChannelConfig
from src.logger import logger
from src.runtime import InboundMessage
from src.runtime.workspace import looks_like_image, resolve_workspace_path, workspace_relative_path

from .base import BaseChannel
from .events import OutboundMessage


STATIC_PACKAGE = "src.webchat.static"
INDEX_FILE = "index.html"
PPTX_PREVIEW_ERROR_TITLE = "PPTX preview unavailable"
UPLOAD_SIZE_LIMIT = 100 * 1024 * 1024
UPLOAD_ROOT = Path(tempfile.gettempdir()) / "creative-claw-web-uploads"


@dataclass(slots=True)
class _ClientConnection:
    """Connected browser client information."""

    websocket: WebSocketServerProtocol
    session_id: str
    client_id: str


@dataclass(slots=True)
class _PendingUpload:
    """One file upload currently being streamed from a browser client."""

    path: Path
    original_name: str
    mime_type: str
    expected_size: int
    received_size: int = 0


def _guess_content_type(filename: str) -> str:
    """Guess one response content type from a file name."""
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _normalize_static_path(raw_path: str) -> str:
    """Normalize one static asset path and reject traversal."""
    path = raw_path.split("?", 1)[0].split("#", 1)[0] or "/"
    if path == "/":
        return INDEX_FILE
    pure = PurePosixPath(path.lstrip("/"))
    if pure.is_absolute() or ".." in pure.parts:
        return INDEX_FILE
    normalized = pure.as_posix()
    return normalized or INDEX_FILE


def _normalize_workspace_relative_path(raw_path: str) -> str | None:
    """Normalize one workspace asset URL path and reject traversal."""
    decoded = unquote(raw_path or "").strip()
    pure = PurePosixPath(decoded.lstrip("/"))
    if pure.is_absolute() or ".." in pure.parts:
        return None
    normalized = pure.as_posix().strip()
    return normalized or None


def _safe_upload_segment(value: str, *, fallback: str) -> str:
    """Return one filesystem-safe upload path segment."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned[:120] or fallback


def _html_response(body: str, *, status: HTTPStatus = HTTPStatus.OK) -> tuple[HTTPStatus, list[tuple[str, str]], bytes]:
    """Build one no-cache HTML response."""
    return (
        status,
        [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-cache")],
        body.encode("utf-8"),
    )


def _simple_preview_error(title: str, message: str) -> str:
    """Render a minimal browser-readable preview error page."""
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{html.escape(title)}</title>
    <style>
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background: #f3f5ef;
        color: #18211d;
        font-family: Avenir Next, Segoe UI, sans-serif;
      }}
      main {{
        width: min(560px, calc(100vw - 48px));
        padding: 28px;
        border: 1px solid rgba(29, 39, 34, 0.14);
        border-radius: 18px;
        background: #fffef9;
        box-shadow: 0 18px 48px rgba(35, 42, 36, 0.12);
      }}
      h1 {{ margin: 0 0 10px; font-size: 22px; }}
      p {{ margin: 0; color: #5b675f; line-height: 1.6; }}
    </style>
  </head>
  <body>
    <main>
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(message)}</p>
    </main>
  </body>
</html>"""


def _pct(value: Any, total: int) -> float:
    """Convert one EMU coordinate into a slide-relative percentage."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    return 0.0 if total <= 0 else max(0.0, numeric / total * 100.0)


def _shape_style(shape: Any, slide_width: int, slide_height: int) -> str:
    """Build absolute-position CSS for one PPTX shape."""
    left = _pct(getattr(shape, "left", 0), slide_width)
    top = _pct(getattr(shape, "top", 0), slide_height)
    width = _pct(getattr(shape, "width", 0), slide_width)
    height = _pct(getattr(shape, "height", 0), slide_height)
    return f"left:{left:.4f}%;top:{top:.4f}%;width:{width:.4f}%;height:{height:.4f}%;"


def _pptx_text_html(shape: Any) -> str:
    """Render text content from one PPTX text shape."""
    text_frame = getattr(shape, "text_frame", None)
    if text_frame is None:
        return ""
    paragraphs: list[str] = []
    for paragraph in text_frame.paragraphs:
        text = "".join(run.text for run in paragraph.runs) or paragraph.text
        stripped = text.strip()
        if stripped:
            paragraphs.append(f"<p>{html.escape(stripped)}</p>")
    return "".join(paragraphs)


def _pptx_table_html(shape: Any) -> str:
    """Render one PPTX table shape into HTML."""
    table = getattr(shape, "table", None)
    if table is None:
        return ""
    rows: list[str] = []
    for row in table.rows:
        cells = "".join(f"<td>{html.escape(cell.text.strip())}</td>" for cell in row.cells)
        rows.append(f"<tr>{cells}</tr>")
    return f"<table>{''.join(rows)}</table>" if rows else ""


def _pptx_image_html(shape: Any) -> str:
    """Render one PPTX picture shape as an embedded image."""
    image = getattr(shape, "image", None)
    if image is None:
        return ""
    mime_type = getattr(image, "content_type", None) or _guess_content_type(getattr(image, "filename", "image"))
    encoded = base64.b64encode(image.blob).decode("ascii")
    alt = html.escape(str(getattr(shape, "name", "") or "slide image"))
    return f'<img src="data:{html.escape(mime_type)};base64,{encoded}" alt="{alt}">'


def _render_pptx_shape(shape: Any, slide_width: int, slide_height: int) -> str:
    """Render one PPTX shape into positioned HTML."""
    content = ""
    if getattr(shape, "has_table", False):
        content = _pptx_table_html(shape)
    elif getattr(shape, "has_text_frame", False):
        content = _pptx_text_html(shape)
    if not content:
        content = _pptx_image_html(shape)
    if not content:
        return ""

    style = _shape_style(shape, slide_width, slide_height)
    return f'<div class="shape" style="{style}">{content}</div>'


def _render_pptx_preview_html(pptx_path: Path) -> str:
    """Render a PPTX file into one standalone HTML preview."""
    try:
        from pptx import Presentation
    except ImportError as exc:  # pragma: no cover - dependency is declared by the project.
        raise RuntimeError("python-pptx is required to preview PPTX files.") from exc

    presentation = Presentation(str(pptx_path))
    slide_width = int(presentation.slide_width) or 12192000
    slide_height = int(presentation.slide_height) or 6858000
    aspect_ratio = f"{slide_width} / {slide_height}"
    slide_items: list[str] = []
    for index, slide in enumerate(presentation.slides, start=1):
        shapes = "".join(
            rendered
            for shape in slide.shapes
            if (rendered := _render_pptx_shape(shape, slide_width, slide_height))
        )
        empty = '<div class="empty-slide">No visible text or images on this slide.</div>' if not shapes else ""
        slide_items.append(
            f"""<section class="slide-wrap">
  <div class="slide-label">Slide {index}</div>
  <div class="slide" style="aspect-ratio:{aspect_ratio};">{shapes}{empty}</div>
</section>"""
        )

    title = html.escape(pptx_path.name)
    slides = "\n".join(slide_items) or '<div class="empty-deck">No slides found.</div>'
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title}</title>
    <style>
      :root {{
        --bg: #eef1ea;
        --paper: #fffef9;
        --ink: #18211d;
        --muted: #68746d;
        --border: rgba(29, 39, 34, 0.16);
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        padding: 24px;
        background: var(--bg);
        color: var(--ink);
        font-family: Avenir Next, Segoe UI, sans-serif;
      }}
      .deck {{
        max-width: 1180px;
        margin: 0 auto;
        display: grid;
        gap: 22px;
      }}
      .slide-label {{
        margin-bottom: 8px;
        color: var(--muted);
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }}
      .slide {{
        position: relative;
        overflow: hidden;
        width: 100%;
        border: 1px solid var(--border);
        border-radius: 16px;
        background: var(--paper);
        box-shadow: 0 18px 46px rgba(35, 42, 36, 0.13);
      }}
      .shape {{
        position: absolute;
        overflow: hidden;
        color: var(--ink);
      }}
      .shape p {{
        margin: 0 0 0.35em;
        font-size: clamp(10px, 1.8vw, 30px);
        line-height: 1.24;
        white-space: pre-wrap;
      }}
      .shape img {{
        width: 100%;
        height: 100%;
        object-fit: contain;
        display: block;
      }}
      .shape table {{
        width: 100%;
        height: 100%;
        border-collapse: collapse;
        font-size: clamp(8px, 1.2vw, 18px);
      }}
      .shape td {{
        border: 1px solid rgba(29, 39, 34, 0.16);
        padding: 0.35em;
        vertical-align: top;
      }}
      .empty-slide,
      .empty-deck {{
        height: 100%;
        display: grid;
        place-items: center;
        color: var(--muted);
        font-size: 14px;
      }}
    </style>
  </head>
  <body>
    <main class="deck">{slides}</main>
  </body>
</html>"""


class WebChannel(BaseChannel):
    """Serve one local browser chat surface over HTTP and WebSocket."""

    name = "web"

    def __init__(
        self,
        *,
        config: WebChannelConfig,
        inbound_handler: Callable[[InboundMessage], Awaitable[None]],
    ) -> None:
        super().__init__()
        self.config = config
        self.inbound_handler = inbound_handler
        self._server = None
        self._server_task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._sessions: dict[str, list[_ClientConnection]] = {}
        self._client_seq = 0
        self._host = config.host
        self._port = config.port
        self._pending_uploads: dict[str, _PendingUpload] = {}

    @property
    def url(self) -> str:
        """Return the local browser URL."""
        return f"http://{self._host}:{self._port}"

    async def wait_until_started(self) -> None:
        """Wait until the HTTP/WebSocket server is listening."""
        await self._started.wait()

    async def start(self) -> None:
        """Start the Web chat service in the background."""
        if self._running:
            return
        self._running = True
        self._started = asyncio.Event()
        self._server_task = asyncio.create_task(self._run_server(), name="creative-claw-webchat")
        started_waiter = asyncio.create_task(self._started.wait(), name="creative-claw-webchat-started")
        done, pending = await asyncio.wait(
            {started_waiter, self._server_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for pending_task in pending:
            pending_task.cancel()
        if self._server_task in done and not self._started.is_set():
            self._running = False
            await self._server_task
            raise RuntimeError("Web chat server stopped before reporting readiness.")
        logger.info("CreativeClaw Web chat listening on {}", self.url)
        if self.config.open_browser:
            with contextlib.suppress(Exception):
                webbrowser.open(self.url)

    async def stop(self) -> None:
        """Stop the Web chat service and close active client sockets."""
        self._running = False
        connections = [conn for conns in self._sessions.values() for conn in conns]
        for conn in connections:
            with contextlib.suppress(Exception):
                await conn.websocket.close(code=1001, reason="server shutdown")
        self._sessions.clear()
        self._cleanup_pending_uploads()

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        if self._server_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._server_task
            self._server_task = None

    async def send(self, message: OutboundMessage) -> None:
        """Send one outbound message to browser clients."""
        await self._broadcast(message.chat_id, self._build_event(message))

    async def _run_server(self) -> None:
        """Run the combined HTTP and WebSocket server."""
        try:
            self._server = await serve(
                self._handle_websocket,
                self.config.host,
                self.config.port,
                process_request=self._process_request,
                max_size=2**20,
            )
            socket = self._server.sockets[0]
            host, port = socket.getsockname()[:2]
            self._host = host
            self._port = port
            self._started.set()
            await self._server.wait_closed()
        except Exception as exc:
            logger.opt(exception=exc).error("CreativeClaw Web chat failed during startup or runtime.")
            raise

    def _build_event(self, message: OutboundMessage) -> dict[str, Any]:
        """Convert one outbound message into a browser payload."""
        metadata = dict(message.metadata or {})
        payload_type = "assistant_message"
        if metadata.get("display_style") == "progress":
            payload_type = "progress"
        elif str(message.text or "").startswith("Error:"):
            payload_type = "error"

        return {
            "type": payload_type,
            "content": message.text,
            "format": "markdown",
            "artifacts": self._build_artifacts(message.artifact_paths),
            "metadata": metadata,
        }

    def _build_artifacts(self, artifact_paths: list[str]) -> list[dict[str, Any]]:
        """Build browser-facing artifact metadata for one outbound message."""
        artifacts: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for raw_path in artifact_paths:
            cleaned_path = str(raw_path or "").strip()
            if not cleaned_path:
                continue
            try:
                resolved = resolve_workspace_path(cleaned_path)
            except Exception:
                continue
            if not resolved.exists() or not resolved.is_file():
                continue
            relative_path = workspace_relative_path(resolved)
            url = f"/workspace/{quote(relative_path)}"
            if url in seen_urls:
                continue
            seen_urls.add(url)
            artifacts.append(
                {
                    "name": resolved.name,
                    "path": relative_path,
                    "url": url,
                    "isImage": looks_like_image(resolved),
                    "mimeType": _guess_content_type(resolved.name),
                }
            )
        return artifacts

    async def _broadcast(self, session_id: str, payload: dict[str, Any]) -> None:
        """Broadcast one payload to all browser tabs in the same session."""
        connections = list(self._sessions.get(session_id, []))
        if not connections:
            return
        encoded = json.dumps(payload, ensure_ascii=False)
        stale: list[_ClientConnection] = []
        for conn in connections:
            try:
                await conn.websocket.send(encoded)
            except ConnectionClosed:
                stale.append(conn)
        for conn in stale:
            self._drop_connection(conn)

    async def _handle_websocket(self, websocket: WebSocketServerProtocol) -> None:
        """Handle one browser websocket session."""
        parsed = urlparse(websocket.path)
        if parsed.path != "/ws":
            await websocket.close(code=1008, reason="invalid path")
            return

        query = parse_qs(parsed.query)
        session_id = (query.get("session_id") or ["default"])[0].strip() or "default"
        client_id = self._next_client_id()
        conn = _ClientConnection(websocket=websocket, session_id=session_id, client_id=client_id)
        self._sessions.setdefault(session_id, []).append(conn)
        await websocket.send(
            json.dumps(
                {
                    "type": "ready",
                    "sessionId": session_id,
                    "clientId": client_id,
                    "title": self.config.title,
                },
                ensure_ascii=False,
            )
        )
        try:
            async for raw in websocket:
                await self._handle_client_message(conn, raw)
        except ConnectionClosed:
            pass
        finally:
            self._drop_connection(conn)

    async def _handle_client_message(self, conn: _ClientConnection, raw: str) -> None:
        """Normalize one browser-originated message into an inbound runtime event."""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            await conn.websocket.send(
                json.dumps({"type": "error", "message": "Invalid JSON payload"}, ensure_ascii=False)
            )
            return

        event_type = payload.get("type")
        if event_type == "upload_start":
            await self._handle_upload_start(conn, payload)
            return
        if event_type == "upload_chunk":
            await self._handle_upload_chunk(conn, payload)
            return
        if event_type == "upload_finish":
            await self._handle_upload_finish(conn, payload)
            return
        if event_type == "upload_cancel":
            await self._handle_upload_cancel(conn, payload)
            return

        if event_type != "chat":
            await conn.websocket.send(
                json.dumps({"type": "error", "message": "Unsupported event type"}, ensure_ascii=False)
            )
            return

        content = str(payload.get("content") or "").strip()
        if not content:
            await conn.websocket.send(
                json.dumps({"type": "error", "message": "Message content is required"}, ensure_ascii=False)
            )
            return

        await self.inbound_handler(
            InboundMessage(
                channel=self.name,
                sender_id=conn.client_id,
                chat_id=conn.session_id,
                text=content,
                metadata={"client_id": conn.client_id},
            )
        )

    async def _handle_upload_start(self, conn: _ClientConnection, payload: dict[str, Any]) -> None:
        """Initialize a streamed browser file upload."""
        upload_id = str(payload.get("uploadId") or "").strip()
        original_name = str(payload.get("name") or "attachment").strip()
        mime_type = str(payload.get("mimeType") or "").strip()
        try:
            expected_size = int(payload.get("size") or 0)
        except (TypeError, ValueError):
            expected_size = 0

        if not upload_id or expected_size < 0 or expected_size > UPLOAD_SIZE_LIMIT:
            await self._send_upload_error(conn, upload_id, "Upload is missing an id or exceeds the size limit.")
            return

        key = self._upload_key(conn, upload_id)
        self._cleanup_pending_upload(key)
        safe_session = _safe_upload_segment(conn.session_id, fallback="session")
        safe_upload_id = _safe_upload_segment(upload_id, fallback=uuid.uuid4().hex)
        safe_name = _safe_upload_segment(Path(original_name).name, fallback="attachment")
        upload_dir = UPLOAD_ROOT / safe_session / safe_upload_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = upload_dir / safe_name
        upload_path.write_bytes(b"")
        self._pending_uploads[key] = _PendingUpload(
            path=upload_path,
            original_name=original_name,
            mime_type=mime_type,
            expected_size=expected_size,
        )
        await conn.websocket.send(
            json.dumps(
                {
                    "type": "upload_started",
                    "uploadId": upload_id,
                    "name": original_name,
                    "size": expected_size,
                },
                ensure_ascii=False,
            )
        )

    async def _handle_upload_chunk(self, conn: _ClientConnection, payload: dict[str, Any]) -> None:
        """Append one base64 encoded upload chunk to a pending file."""
        upload_id = str(payload.get("uploadId") or "").strip()
        key = self._upload_key(conn, upload_id)
        pending = self._pending_uploads.get(key)
        if pending is None:
            await self._send_upload_error(conn, upload_id, "Upload was not started.")
            return

        try:
            chunk = base64.b64decode(str(payload.get("data") or ""), validate=True)
        except Exception:
            self._cleanup_pending_upload(key)
            await self._send_upload_error(conn, upload_id, "Upload chunk was not valid base64.")
            return

        next_size = pending.received_size + len(chunk)
        if next_size > pending.expected_size or next_size > UPLOAD_SIZE_LIMIT:
            self._cleanup_pending_upload(key)
            await self._send_upload_error(conn, upload_id, "Upload exceeded the expected size.")
            return

        with pending.path.open("ab") as file_obj:
            file_obj.write(chunk)
        pending.received_size = next_size
        await conn.websocket.send(
            json.dumps(
                {
                    "type": "upload_chunk_received",
                    "uploadId": upload_id,
                    "received": pending.received_size,
                },
                ensure_ascii=False,
            )
        )

    async def _handle_upload_finish(self, conn: _ClientConnection, payload: dict[str, Any]) -> None:
        """Finalize a streamed browser file upload and return the local path."""
        upload_id = str(payload.get("uploadId") or "").strip()
        key = self._upload_key(conn, upload_id)
        pending = self._pending_uploads.pop(key, None)
        if pending is None:
            await self._send_upload_error(conn, upload_id, "Upload was not started.")
            return
        if pending.received_size != pending.expected_size:
            self._cleanup_upload_file(pending.path)
            await self._send_upload_error(conn, upload_id, "Upload finished before all bytes were received.")
            return

        await conn.websocket.send(
            json.dumps(
                {
                    "type": "upload_complete",
                    "uploadId": upload_id,
                    "name": pending.original_name,
                    "path": str(pending.path),
                    "size": pending.received_size,
                    "mimeType": pending.mime_type,
                },
                ensure_ascii=False,
            )
        )

    async def _handle_upload_cancel(self, conn: _ClientConnection, payload: dict[str, Any]) -> None:
        """Cancel a pending browser file upload."""
        upload_id = str(payload.get("uploadId") or "").strip()
        self._cleanup_pending_upload(self._upload_key(conn, upload_id))
        await conn.websocket.send(
            json.dumps({"type": "upload_cancelled", "uploadId": upload_id}, ensure_ascii=False)
        )

    async def _send_upload_error(self, conn: _ClientConnection, upload_id: str, message: str) -> None:
        """Send one upload-scoped error to a browser client."""
        await conn.websocket.send(
            json.dumps({"type": "upload_error", "uploadId": upload_id, "message": message}, ensure_ascii=False)
        )

    def _upload_key(self, conn: _ClientConnection, upload_id: str) -> str:
        """Return the internal key for a client-scoped upload."""
        return f"{conn.client_id}:{upload_id}"

    def _cleanup_pending_upload(self, key: str) -> None:
        """Remove one pending upload and its partially written file."""
        pending = self._pending_uploads.pop(key, None)
        if pending is not None:
            self._cleanup_upload_file(pending.path)

    def _cleanup_pending_uploads(self, *, client_id: str | None = None) -> None:
        """Remove pending upload files, optionally only for one client."""
        keys = list(self._pending_uploads)
        for key in keys:
            if client_id is None or key.startswith(f"{client_id}:"):
                self._cleanup_pending_upload(key)

    def _cleanup_upload_file(self, path: Path) -> None:
        """Delete one partial upload file and its empty parent directory."""
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        with contextlib.suppress(OSError):
            path.parent.rmdir()

    async def _process_request(
        self,
        path: str,
        _request_headers: Headers,
    ) -> tuple[HTTPStatus, list[tuple[str, str]], bytes] | None:
        """Serve static assets and workspace files from the websocket server."""
        parsed = urlparse(path)
        if parsed.path == "/ws":
            return None

        if parsed.path.startswith("/workspace-preview/"):
            return self._serve_workspace_preview(parsed.path.removeprefix("/workspace-preview/"))

        if parsed.path.startswith("/workspace/"):
            return self._serve_workspace_asset(parsed.path.removeprefix("/workspace/"))

        resolved_path = _normalize_static_path(parsed.path)
        if not self._asset_exists(resolved_path):
            return (
                HTTPStatus.NOT_FOUND,
                [("Content-Type", "text/plain; charset=utf-8")],
                b"Not Found",
            )

        body = self._read_asset(resolved_path)
        headers = [
            ("Content-Type", self._content_type_header(resolved_path)),
            ("Cache-Control", "no-cache"),
        ]
        if resolved_path == INDEX_FILE:
            body = body.replace(b"__CREATIVE_CLAW_TITLE__", self.config.title.encode("utf-8"))
        return HTTPStatus.OK, headers, body

    def _serve_workspace_asset(self, raw_relative_path: str) -> tuple[HTTPStatus, list[tuple[str, str]], bytes]:
        """Serve one file from the CreativeClaw workspace."""
        normalized = _normalize_workspace_relative_path(raw_relative_path)
        if normalized is None:
            return (
                HTTPStatus.NOT_FOUND,
                [("Content-Type", "text/plain; charset=utf-8")],
                b"Not Found",
            )
        try:
            resolved = resolve_workspace_path(normalized)
        except Exception:
            return (
                HTTPStatus.NOT_FOUND,
                [("Content-Type", "text/plain; charset=utf-8")],
                b"Not Found",
            )
        if not resolved.exists() or not resolved.is_file():
            return (
                HTTPStatus.NOT_FOUND,
                [("Content-Type", "text/plain; charset=utf-8")],
                b"Not Found",
            )
        return (
            HTTPStatus.OK,
            [("Content-Type", self._content_type_header(resolved.name)), ("Cache-Control", "no-cache")],
            resolved.read_bytes(),
        )

    def _serve_workspace_preview(self, raw_relative_path: str) -> tuple[HTTPStatus, list[tuple[str, str]], bytes]:
        """Serve one browser-renderable preview for a workspace file."""
        resolved = self._resolve_workspace_file(raw_relative_path)
        if resolved is None:
            return (
                HTTPStatus.NOT_FOUND,
                [("Content-Type", "text/plain; charset=utf-8")],
                b"Not Found",
            )
        if resolved.suffix.lower() != ".pptx":
            return _html_response(
                _simple_preview_error(
                    "Preview unsupported",
                    f"No inline preview is available for {resolved.name}. Open the original file instead.",
                ),
                status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )

        try:
            return _html_response(_render_pptx_preview_html(resolved))
        except Exception as exc:
            logger.opt(exception=exc).warning("Failed to render PPTX preview for {}", resolved)
            return _html_response(
                _simple_preview_error(PPTX_PREVIEW_ERROR_TITLE, f"Could not render {resolved.name}: {exc}"),
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _resolve_workspace_file(self, raw_relative_path: str) -> Path | None:
        """Resolve one normalized workspace file path for browser routes."""
        normalized = _normalize_workspace_relative_path(raw_relative_path)
        if normalized is None:
            return None
        try:
            resolved = resolve_workspace_path(normalized)
        except Exception:
            return None
        if not resolved.exists() or not resolved.is_file():
            return None
        return resolved

    def _asset_exists(self, relative_path: str) -> bool:
        """Return whether one packaged static asset exists."""
        return resources.files(STATIC_PACKAGE).joinpath(relative_path).is_file()

    def _read_asset(self, relative_path: str) -> bytes:
        """Read one packaged static asset."""
        return resources.files(STATIC_PACKAGE).joinpath(relative_path).read_bytes()

    def _content_type_header(self, filename: str) -> str:
        """Build one response content type header."""
        content_type = _guess_content_type(filename)
        if filename.endswith((".html", ".js", ".css", ".json", ".svg")):
            return f"{content_type}; charset=utf-8"
        return content_type

    def _next_client_id(self) -> str:
        """Generate the next browser client identifier."""
        self._client_seq += 1
        return f"web-client-{self._client_seq}"

    def _drop_connection(self, conn: _ClientConnection) -> None:
        """Remove one closed browser connection from the session registry."""
        self._cleanup_pending_uploads(client_id=conn.client_id)
        session_connections = self._sessions.get(conn.session_id)
        if not session_connections:
            return
        self._sessions[conn.session_id] = [item for item in session_connections if item is not conn]
        if not self._sessions[conn.session_id]:
            self._sessions.pop(conn.session_id, None)
