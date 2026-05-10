import asyncio
import base64
import contextlib
import json
import unittest
import uuid
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from pptx import Presentation
from pptx.util import Inches
import websockets

from conf.channel import WebChannelConfig
from src.channels.events import OutboundMessage
from src.channels.web import WebChannel
from src.runtime import InboundMessage
from src.runtime.workspace import generated_root, workspace_relative_path


class WebChannelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.inbound_messages: list[InboundMessage] = []

        async def _handler(message: InboundMessage) -> None:
            self.inbound_messages.append(message)

        self.channel = WebChannel(
            config=WebChannelConfig(
                host="127.0.0.1",
                port=0,
                open_browser=False,
                title="CreativeClaw Web Chat",
            ),
            inbound_handler=_handler,
        )
        await self.channel.start()

    async def asyncTearDown(self) -> None:
        await self.channel.stop()

    async def test_web_channel_serves_index_page(self) -> None:
        def fetch_index():
            with urlopen(self.channel.url) as response:  # noqa: S310 - local test server
                return response.status, response.read().decode("utf-8")

        status, body = await asyncio.to_thread(fetch_index)
        self.assertEqual(status, 200)
        self.assertIn("CreativeClaw Web Chat", body)
        self.assertIn("/app.js", body)
        self.assertNotIn("Creative flow in one surface", body)
        self.assertNotIn("A local browser chat surface", body)
        self.assertNotIn("Recent Sessions", body)
        self.assertIn('data-preview-tab="tldraw"', body)
        self.assertIn('data-preview-tab="html"', body)
        self.assertIn('data-preview-tab="ppt"', body)
        self.assertIn("Visual Board", body)
        self.assertIn("Design", body)
        self.assertIn("No Design preview", body)
        self.assertNotIn("No HTML preview", body)
        self.assertIn('aria-label="Send message"', body)
        self.assertIn('aria-label="Attach files"', body)
        self.assertIn('id="session-history"', body)
        self.assertIn('id="session-popover"', body)
        self.assertIn('id="file-input"', body)
        self.assertNotIn("Describe the image, prompt", body)

    async def test_web_channel_serves_pptx_preview_page(self) -> None:
        generated_file = generated_root() / f"web_channel_{uuid.uuid4().hex[:8]}.pptx"
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        textbox = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(1))
        textbox.text = "Quarterly roadmap preview"
        presentation.save(generated_file)

        try:
            relative_path = workspace_relative_path(generated_file)

            def fetch_preview():
                preview_url = f"{self.channel.url}/workspace-preview/{quote(relative_path)}"
                with urlopen(preview_url) as response:  # noqa: S310 - local test server
                    return response.status, response.headers.get("Content-Type", ""), response.read().decode("utf-8")

            status, content_type, body = await asyncio.to_thread(fetch_preview)
            self.assertEqual(status, 200)
            self.assertIn("text/html", content_type)
            self.assertIn("Quarterly roadmap preview", body)
            self.assertIn("Slide 1", body)
        finally:
            with contextlib.suppress(FileNotFoundError):
                generated_file.unlink()

    async def test_web_channel_serves_design_system_catalog_and_preview(self) -> None:
        def fetch_json(path: str):
            with urlopen(f"{self.channel.url}{path}") as response:  # noqa: S310 - local test server
                return response.status, response.headers.get("Content-Type", ""), json.loads(
                    response.read().decode("utf-8")
                )

        def fetch_html(path: str):
            with urlopen(f"{self.channel.url}{path}") as response:  # noqa: S310 - local test server
                return response.status, response.headers.get("Content-Type", ""), response.read().decode("utf-8")

        status, content_type, payload = await asyncio.to_thread(fetch_json, "/api/design-systems")
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        systems = {item["id"]: item for item in payload["designSystems"]}
        self.assertIn("claude", systems)
        self.assertIn("/api/design-systems/claude/preview", systems["claude"]["previewUrl"])

        status, content_type, body = await asyncio.to_thread(fetch_html, "/api/design-systems/claude/showcase")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("Claude Design System Preview", body)

    async def test_web_channel_bridges_websocket_messages_and_artifacts(self) -> None:
        generated_file = generated_root() / f"web_channel_{uuid.uuid4().hex[:8]}.png"
        generated_file.write_bytes(b"fake-image")

        try:
            async with websockets.connect(f"ws://127.0.0.1:{self.channel._port}/ws?session_id=test-session") as websocket:
                ready = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2))
                self.assertEqual(ready["type"], "ready")
                self.assertEqual(ready["sessionId"], "test-session")

                await websocket.send(json.dumps({"type": "chat", "content": "hello web"}))
                started = await self._recv_until(websocket, "task_started")
                self.assertTrue(started["runId"])
                inbound = await asyncio.wait_for(self._consume_inbound(), timeout=2)
                self.assertEqual(inbound.channel, "web")
                self.assertEqual(inbound.chat_id, "test-session")
                self.assertEqual(inbound.text, "hello web")
                self.assertEqual(inbound.metadata["run_id"], started["runId"])
                await self._recv_until(websocket, "task_finished")

                await self.channel.send(
                    OutboundMessage(
                        channel="web",
                        chat_id="test-session",
                        text="working on it",
                        metadata={"display_style": "progress", "stage_title": "Planning"},
                    )
                )
                progress = await self._recv_until(websocket, "progress")
                self.assertEqual(progress["type"], "progress")
                self.assertEqual(progress["metadata"]["stage_title"], "Planning")

                await self.channel.send(
                    OutboundMessage(
                        channel="web",
                        chat_id="test-session",
                        text="final answer",
                        artifact_paths=[str(generated_file)],
                        metadata={"display_style": "final"},
                    )
                )
                final_message = await self._recv_until(websocket, "assistant_message")
                self.assertEqual(final_message["type"], "assistant_message")
                self.assertEqual(final_message["content"], "final answer")
                self.assertEqual(len(final_message["artifacts"]), 1)
                artifact = final_message["artifacts"][0]
                self.assertTrue(artifact["isImage"])

                def fetch_artifact():
                    with urlopen(f"{self.channel.url}{artifact['url']}") as response:  # noqa: S310 - local test server
                        return response.status, response.read()

                status, body = await asyncio.to_thread(fetch_artifact)
                self.assertEqual(status, 200)
                self.assertEqual(body, b"fake-image")
        finally:
            with contextlib.suppress(FileNotFoundError):
                generated_file.unlink()

    async def test_web_channel_accepts_chunked_file_uploads(self) -> None:
        upload_id = f"upload-{uuid.uuid4().hex[:8]}"
        body = b"hello uploaded file"
        uploaded_path: Path | None = None

        async with websockets.connect(f"ws://127.0.0.1:{self.channel._port}/ws?session_id=upload-session") as websocket:
            ready = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2))
            self.assertEqual(ready["type"], "ready")

            await websocket.send(
                json.dumps(
                    {
                        "type": "upload_start",
                        "uploadId": upload_id,
                        "name": "note.txt",
                        "size": len(body),
                        "mimeType": "text/plain",
                    }
                )
            )
            started = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2))
            self.assertEqual(started["type"], "upload_started")
            self.assertEqual(started["uploadId"], upload_id)

            await websocket.send(
                json.dumps(
                    {
                        "type": "upload_chunk",
                        "uploadId": upload_id,
                        "data": base64.b64encode(body).decode("ascii"),
                    }
                )
            )
            chunk = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2))
            self.assertEqual(chunk["type"], "upload_chunk_received")
            self.assertEqual(chunk["received"], len(body))

            await websocket.send(json.dumps({"type": "upload_finish", "uploadId": upload_id}))
            complete = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2))
            self.assertEqual(complete["type"], "upload_complete")
            self.assertEqual(complete["name"], "note.txt")
            uploaded_path = Path(complete["path"])
            self.assertEqual(uploaded_path.read_bytes(), body)

            await websocket.send(
                json.dumps(
                    {
                        "type": "chat",
                        "content": "use this uploaded note",
                        "attachments": [
                            {
                                "name": "note.txt",
                                "path": complete["path"],
                                "mimeType": "text/plain",
                                "description": "uploaded test note",
                            }
                        ],
                    }
                )
            )
            await self._recv_until(websocket, "task_started")
            inbound = await asyncio.wait_for(self._consume_inbound(), timeout=2)
            self.assertEqual(inbound.text, "use this uploaded note")
            self.assertEqual(len(inbound.attachments), 1)
            self.assertEqual(Path(inbound.attachments[0].path).resolve(), uploaded_path.resolve())
            self.assertEqual(inbound.attachments[0].name, "note.txt")
            self.assertEqual(inbound.attachments[0].mime_type, "text/plain")
            self.assertEqual(inbound.attachments[0].description, "uploaded test note")
            await self._recv_until(websocket, "task_finished")

        if uploaded_path is not None:
            with contextlib.suppress(FileNotFoundError):
                uploaded_path.unlink()
            with contextlib.suppress(OSError):
                uploaded_path.parent.rmdir()

    async def test_web_channel_stop_cancels_active_run_without_blocking_read_loop(self) -> None:
        started = asyncio.Event()
        released = asyncio.Event()

        async def _blocking_handler(message: InboundMessage) -> None:
            self.inbound_messages.append(message)
            started.set()
            await released.wait()

        self.channel.inbound_handler = _blocking_handler

        async with websockets.connect(f"ws://127.0.0.1:{self.channel._port}/ws?session_id=stop-session") as websocket:
            ready = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2))
            self.assertEqual(ready["type"], "ready")

            run_id = f"run-{uuid.uuid4().hex}"
            await websocket.send(json.dumps({"type": "chat", "content": "long task", "runId": run_id}))
            started_payload = await self._recv_until(websocket, "task_started")
            self.assertEqual(started_payload["runId"], run_id)
            await asyncio.wait_for(started.wait(), timeout=2)

            await websocket.send(json.dumps({"type": "stop", "runId": run_id}))
            stopping = await self._recv_until(websocket, "task_stopping")
            self.assertEqual(stopping["runId"], run_id)
            finished = await self._recv_until(websocket, "task_finished")
            self.assertEqual(finished["runId"], run_id)
            self.assertEqual(finished["reason"], "cancelled")

            next_run_id = f"run-{uuid.uuid4().hex}"
            await websocket.send(json.dumps({"type": "chat", "content": "next task", "runId": next_run_id}))
            next_started = await self._recv_until(websocket, "task_started")
            self.assertEqual(next_started["runId"], next_run_id)

        released.set()

    async def _consume_inbound(self) -> InboundMessage:
        for _ in range(20):
            if self.inbound_messages:
                return self.inbound_messages.pop(0)
            await asyncio.sleep(0.01)
        raise AssertionError("Inbound message was not received.")

    async def _recv_until(self, websocket, expected_type: str) -> dict[str, object]:
        for _ in range(10):
            payload = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2))
            if payload.get("type") == expected_type:
                return payload
        raise AssertionError(f"Did not receive payload type {expected_type!r}.")


if __name__ == "__main__":
    unittest.main()
