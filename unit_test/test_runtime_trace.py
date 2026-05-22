import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService

from src.agents.orchestrator.orchestrator_agent import Orchestrator
from src.runtime.runtime_trace import (
    CreativeClawRuntimeTracePlugin,
    RUNTIME_TRACE_ENV_VAR,
    RUNTIME_TRACE_MAX_CHARS_ENV_VAR,
    RUNTIME_TRACE_RAW_EVENTS_ENV_VAR,
    RUNTIME_TRACE_STREAM_DELTAS_ENV_VAR,
    runtime_trace_raw_events_enabled,
    runtime_trace_enabled,
    runtime_trace_stream_deltas_enabled,
    serialize_trace_payload,
    trace_runtime_event,
)


class RuntimeTraceTests(unittest.TestCase):
    def test_runtime_trace_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {RUNTIME_TRACE_ENV_VAR: ""}, clear=False):
            self.assertFalse(runtime_trace_enabled())
            with patch("src.runtime.runtime_trace.logger.info") as logger_info:
                self.assertFalse(trace_runtime_event("tool.start", {"tool": "read_file"}))

        logger_info.assert_not_called()

    def test_runtime_trace_logs_when_enabled(self) -> None:
        with patch.dict(os.environ, {RUNTIME_TRACE_ENV_VAR: "1"}, clear=False):
            with patch("src.runtime.runtime_trace.logger.info") as logger_info:
                self.assertTrue(trace_runtime_event("tool.start", {"tool": "read_file"}))

        logger_info.assert_called_once()
        self.assertEqual(logger_info.call_args.args[1], "tool.start")

    def test_raw_runner_events_are_opt_in(self) -> None:
        plugin = CreativeClawRuntimeTracePlugin()
        invocation_context = SimpleNamespace(
            app_name="CreativeClaw",
            user_id="user-1",
            invocation_id="inv-1",
            session=SimpleNamespace(id="session-1"),
            agent=SimpleNamespace(name="CreativeClawOrchestrator"),
        )

        async def _run_callback() -> None:
            await plugin.on_event_callback(
                invocation_context=invocation_context,
                event=SimpleNamespace(author="CreativeClawOrchestrator", partial=True),
            )

        with patch.dict(
            os.environ,
            {
                RUNTIME_TRACE_ENV_VAR: "1",
                RUNTIME_TRACE_RAW_EVENTS_ENV_VAR: "",
            },
            clear=False,
        ):
            self.assertFalse(runtime_trace_raw_events_enabled())
            with patch("src.runtime.runtime_trace.logger.info") as logger_info:
                asyncio.run(_run_callback())

        logger_info.assert_not_called()

        with patch.dict(
            os.environ,
            {
                RUNTIME_TRACE_ENV_VAR: "1",
                RUNTIME_TRACE_RAW_EVENTS_ENV_VAR: "1",
            },
            clear=False,
        ):
            self.assertTrue(runtime_trace_raw_events_enabled())
            with patch("src.runtime.runtime_trace.logger.info") as logger_info:
                asyncio.run(_run_callback())

        logger_info.assert_called_once()
        self.assertEqual(logger_info.call_args.args[1], "runner.event")

    def test_partial_model_responses_are_opt_in(self) -> None:
        plugin = CreativeClawRuntimeTracePlugin()
        callback_context = SimpleNamespace(
            agent_name="CreativeClawOrchestrator",
            invocation_id="inv-1",
            state={},
        )
        partial_response = SimpleNamespace(
            model_version="deepseek-v4-pro",
            partial=True,
            content={"parts": [{"text": "The", "thought": True}]},
        )

        async def _run_callback() -> None:
            await plugin.after_model_callback(
                callback_context=callback_context,
                llm_response=partial_response,
            )

        with patch.dict(
            os.environ,
            {
                RUNTIME_TRACE_ENV_VAR: "1",
                RUNTIME_TRACE_STREAM_DELTAS_ENV_VAR: "",
            },
            clear=False,
        ):
            self.assertFalse(runtime_trace_stream_deltas_enabled())
            with patch("src.runtime.runtime_trace.logger.info") as logger_info:
                asyncio.run(_run_callback())

        logger_info.assert_not_called()

        with patch.dict(
            os.environ,
            {
                RUNTIME_TRACE_ENV_VAR: "1",
                RUNTIME_TRACE_STREAM_DELTAS_ENV_VAR: "1",
            },
            clear=False,
        ):
            self.assertTrue(runtime_trace_stream_deltas_enabled())
            with patch("src.runtime.runtime_trace.logger.info") as logger_info:
                asyncio.run(_run_callback())

        logger_info.assert_called_once()
        self.assertEqual(logger_info.call_args.args[1], "model.response")

    def test_final_model_response_logs_when_trace_is_enabled(self) -> None:
        plugin = CreativeClawRuntimeTracePlugin()
        callback_context = SimpleNamespace(
            agent_name="CreativeClawOrchestrator",
            invocation_id="inv-1",
            state={},
        )
        final_response = SimpleNamespace(
            model_version="deepseek-v4-pro",
            partial=False,
            content={"parts": [{"text": "done"}]},
        )

        async def _run_callback() -> None:
            await plugin.after_model_callback(
                callback_context=callback_context,
                llm_response=final_response,
            )

        with patch.dict(
            os.environ,
            {
                RUNTIME_TRACE_ENV_VAR: "1",
                RUNTIME_TRACE_STREAM_DELTAS_ENV_VAR: "",
            },
            clear=False,
        ):
            with patch("src.runtime.runtime_trace.logger.info") as logger_info:
                asyncio.run(_run_callback())

        logger_info.assert_called_once()
        self.assertEqual(logger_info.call_args.args[1], "model.response")

    def test_runtime_trace_payload_redacts_secrets_and_truncates(self) -> None:
        with patch.dict(
            os.environ,
            {
                RUNTIME_TRACE_ENV_VAR: "1",
                RUNTIME_TRACE_MAX_CHARS_ENV_VAR: "500",
            },
            clear=False,
        ):
            rendered = serialize_trace_payload(
                {
                    "api_key": "sk-secret",
                    "prompt_token_count": 123,
                    "text": "x" * 1000,
                }
            )

        self.assertIn('"api_key": "[REDACTED]"', rendered)
        self.assertIn('"prompt_token_count": 123', rendered)
        self.assertIn("<runtime trace truncated", rendered)
        self.assertNotIn("sk-secret", rendered)

    def test_trace_plugin_logs_tool_lifecycle_when_enabled(self) -> None:
        plugin = CreativeClawRuntimeTracePlugin()
        tool = SimpleNamespace(name="read_file")
        tool_context = SimpleNamespace(
            invocation_id="inv-1",
            session=SimpleNamespace(id="session-1", state={"turn_index": 2, "step": 3}),
        )

        async def _run_callbacks() -> None:
            await plugin.before_tool_callback(
                tool=tool,
                tool_args={"path": "README.md", "access_token": "secret"},
                tool_context=tool_context,
            )
            await plugin.after_tool_callback(
                tool=tool,
                tool_args={"path": "README.md"},
                tool_context=tool_context,
                result={"status": "success"},
            )

        with patch.dict(os.environ, {RUNTIME_TRACE_ENV_VAR: "true"}, clear=False):
            with patch("src.runtime.runtime_trace.logger.info") as logger_info:
                asyncio.run(_run_callbacks())

        event_types = [call.args[1] for call in logger_info.call_args_list]
        self.assertEqual(event_types, ["tool.start", "tool.finish"])
        combined_payload = "\n".join(str(call.args[2]) for call in logger_info.call_args_list)
        self.assertIn('"access_token": "[REDACTED]"', combined_payload)

    def test_orchestrator_registers_runtime_trace_plugin(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )

        plugin_names = [plugin.name for plugin in orchestrator.app.plugins]

        self.assertIn("creative_claw_runtime_trace", plugin_names)


if __name__ == "__main__":
    unittest.main()
