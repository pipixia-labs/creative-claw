import unittest
import asyncio
from types import SimpleNamespace

from src.runtime.step_events import (
    CreativeClawStepEventPlugin,
    append_orchestration_step_event,
    assistant_delta_streaming_active,
    configure_step_event_publisher,
    publish_assistant_delta,
    publish_orchestration_step_event,
    reset_step_event_history,
)
from src.runtime.tool_context import route_context


class StepEventPluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.messages = []

        async def _publisher(message):
            self.messages.append(message)

        configure_step_event_publisher(_publisher)

    async def asyncTearDown(self) -> None:
        configure_step_event_publisher(None)

    async def test_plugin_publishes_realtime_tool_start_and_finish(self) -> None:
        plugin = CreativeClawStepEventPlugin()
        invocation = SimpleNamespace(invocation_id="inv-1")
        tool = SimpleNamespace(name="read_file")
        tool_context = SimpleNamespace(
            invocation_id="inv-1",
            session=SimpleNamespace(id="session-1", state={"turn_index": 4}),
        )

        with route_context("cli", "chat-1"):
            await plugin.before_run_callback(invocation_context=invocation)
            await plugin.before_tool_callback(
                tool=tool,
                tool_args={"path": "README.md"},
                tool_context=tool_context,
            )
            await plugin.after_tool_callback(
                tool=tool,
                tool_args={"path": "README.md"},
                tool_context=tool_context,
                result="line one\nline two\nline three",
            )
            await plugin.after_run_callback(invocation_context=invocation)

        self.assertEqual(len(self.messages), 2)
        self.assertEqual(self.messages[0].metadata["stage_title"], "Reading context")
        self.assertEqual(self.messages[0].metadata["debug_title"], "read_file")
        self.assertEqual(self.messages[0].metadata["turn_index"], 4)
        self.assertEqual(self.messages[0].text, "The system is reading relevant workspace content.")
        self.assertIn("Status: started", self.messages[0].metadata["debug_detail"])
        self.assertIn("Args: path=README.md", self.messages[0].metadata["debug_detail"])
        self.assertEqual(self.messages[1].text, "The system is reading relevant workspace content.")
        self.assertEqual(len(self.messages[1].metadata["debug_events"]), 2)
        self.assertEqual(self.messages[1].metadata["debug_events"][0]["title"], "read_file")
        self.assertEqual(self.messages[1].metadata["debug_events"][1]["title"], "read_file")
        self.assertIn("Result: Read succeeded", self.messages[1].metadata["debug_detail"])

    async def test_plugin_ignores_unknown_tool_names(self) -> None:
        plugin = CreativeClawStepEventPlugin()
        invocation = SimpleNamespace(invocation_id="inv-2")
        tool = SimpleNamespace(name="run_expert")
        tool_context = SimpleNamespace(
            invocation_id="inv-2",
            session=SimpleNamespace(id="session-2"),
        )

        with route_context("cli", "chat-2"):
            await plugin.before_run_callback(invocation_context=invocation)
            await plugin.before_tool_callback(
                tool=tool,
                tool_args={"agent_name": "KnowledgeAgent"},
                tool_context=tool_context,
            )
            await plugin.after_run_callback(invocation_context=invocation)

        self.assertEqual(self.messages, [])

    async def test_orchestration_event_is_published_realtime(self) -> None:
        with route_context("cli", "chat-3"):
            reset_step_event_history(session_id="session-3")
            publish_orchestration_step_event(
                session_id="session-3",
                title="Call Expert Agent",
                detail="Calling `ImageGenerationAgent` for the current step.",
                stage="expert_execution",
            )
            await asyncio.sleep(0)

        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0].metadata["stage_title"], "Generating content")
        self.assertEqual(self.messages[0].metadata["debug_title"], "Call Expert Agent")
        self.assertEqual(self.messages[0].text, "The system is using a specialist capability for this step.")
        self.assertIn("Calling `ImageGenerationAgent`", self.messages[0].metadata["debug_detail"])

    async def test_assistant_delta_only_publishes_for_web_channel(self) -> None:
        with route_context("cli", "chat-cli"):
            self.assertFalse(assistant_delta_streaming_active())
            published = await publish_assistant_delta(session_id="session-cli", delta="Hello", turn_index=1)
        self.assertFalse(published)
        self.assertEqual(self.messages, [])

        with route_context("web", "chat-web"):
            self.assertTrue(assistant_delta_streaming_active())
            published = await publish_assistant_delta(session_id="session-web", delta="Hello", turn_index=2)

        self.assertTrue(published)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0].text, "Hello")
        self.assertEqual(self.messages[0].metadata["display_style"], "assistant_delta")
        self.assertEqual(self.messages[0].metadata["turn_index"], 2)

    async def test_orchestration_tool_titles_are_normalized_for_user_progress(self) -> None:
        with route_context("cli", "chat-normalized"):
            reset_step_event_history(session_id="session-normalized")
            publish_orchestration_step_event(
                session_id="session-normalized",
                title="List Session Files",
                detail="Session file snapshot loaded, uploaded=0; generated=0.",
                stage="inspection",
            )
            await asyncio.sleep(0)

        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0].metadata["stage_title"], "Checking context")
        self.assertEqual(
            self.messages[0].text,
            "The system is reviewing this conversation's files and previous outputs.",
        )
        self.assertEqual(self.messages[0].metadata["debug_title"], "List Session Files")
        self.assertIn("Session file snapshot", self.messages[0].metadata["debug_detail"])

    async def test_append_orchestration_step_event_updates_state_and_publishes(self) -> None:
        state = {"sid": "session-append", "turn_index": 3, "orchestration_events": []}

        with route_context("cli", "chat-append"):
            reset_step_event_history(session_id="session-append", turn_index=3)
            append_orchestration_step_event(
                state,
                title="PPT Image Generation",
                detail="Status: started\nArgs: slide=1; asset_id=slide_01_visual",
                stage="image_processing",
            )
            await asyncio.sleep(0)

        self.assertEqual(state["orchestration_events"][0]["title"], "PPT Image Generation")
        self.assertEqual(state["orchestration_events"][0]["user_title"], "Processing images")
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0].metadata["stage_title"], "Processing images")
        self.assertEqual(self.messages[0].metadata["debug_title"], "PPT Image Generation")
        self.assertEqual(self.messages[0].metadata["turn_index"], 3)
        self.assertEqual(self.messages[0].text, "The system is working with image assets.")
        self.assertIn("slide_01_visual", self.messages[0].metadata["debug_detail"])

    async def test_orchestration_event_history_is_scoped_by_turn_index(self) -> None:
        with route_context("cli", "chat-3"):
            reset_step_event_history(session_id="session-3", turn_index=1)
            publish_orchestration_step_event(
                session_id="session-3",
                turn_index=1,
                title="First Turn Expert",
                detail="Running the first request.",
                stage="expert_execution",
            )
            publish_orchestration_step_event(
                session_id="session-3",
                turn_index=2,
                title="Second Turn Expert",
                detail="Running the second request.",
                stage="expert_execution",
            )
            await asyncio.sleep(0)

        self.assertEqual(len(self.messages), 2)
        self.assertEqual(self.messages[0].metadata["turn_index"], 1)
        self.assertEqual(self.messages[1].metadata["turn_index"], 2)
        self.assertEqual(self.messages[0].metadata["debug_title"], "First Turn Expert")
        self.assertEqual(self.messages[1].metadata["debug_title"], "Second Turn Expert")
        self.assertEqual(self.messages[0].text, "The system is using a specialist capability for this step.")
        self.assertEqual(self.messages[1].text, "The system is using a specialist capability for this step.")
        self.assertNotIn("First Turn Expert", self.messages[1].metadata["debug_detail"])

    async def test_plugin_and_orchestration_events_share_same_history(self) -> None:
        plugin = CreativeClawStepEventPlugin()
        invocation = SimpleNamespace(invocation_id="inv-4")
        tool = SimpleNamespace(name="read_file")
        tool_context = SimpleNamespace(
            invocation_id="inv-4",
            session=SimpleNamespace(id="session-4"),
        )

        with route_context("cli", "chat-4"):
            reset_step_event_history(session_id="session-4")
            publish_orchestration_step_event(
                session_id="session-4",
                title="Call Expert Agent",
                detail="Calling `KnowledgeAgent` for the current step.",
                stage="expert_execution",
            )
            await asyncio.sleep(0)
            await plugin.before_tool_callback(
                tool=tool,
                tool_args={"path": "README.md"},
                tool_context=tool_context,
            )

        self.assertEqual(len(self.messages), 2)
        self.assertEqual(self.messages[1].text, "The system is reading relevant workspace content.")
        self.assertEqual(self.messages[1].metadata["debug_events"][0]["title"], "Call Expert Agent")
        self.assertEqual(self.messages[1].metadata["debug_events"][1]["title"], "read_file")
