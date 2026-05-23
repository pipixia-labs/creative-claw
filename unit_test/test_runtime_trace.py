import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from src.agents.orchestrator.orchestrator_agent import Orchestrator
from src.runtime.runtime_trace import (
    CreativeClawRuntimeTracePlugin,
    RUNTIME_TRACE_ENV_VAR,
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

    def test_model_response_trace_aggregates_content_parts(self) -> None:
        plugin = CreativeClawRuntimeTracePlugin()
        callback_context = SimpleNamespace(
            agent_name="CreativeClawOrchestrator",
            invocation_id="inv-1",
            state={},
        )
        final_response = SimpleNamespace(
            model_version="deepseek-v4-pro",
            partial=False,
            content={
                "role": "model",
                "parts": [
                    {"text": "The", "thought": True},
                    {"text": " image", "thought": True},
                    {"text": "Final answer."},
                ],
            },
            finish_reason="STOP",
        )

        async def _run_callback() -> None:
            await plugin.after_model_callback(
                callback_context=callback_context,
                llm_response=final_response,
            )

        with patch.dict(os.environ, {RUNTIME_TRACE_ENV_VAR: "1"}, clear=False):
            with patch("src.runtime.runtime_trace.logger.info") as logger_info:
                asyncio.run(_run_callback())

        payload = json.loads(logger_info.call_args.args[2])
        content = payload["response"]["content"]
        self.assertNotIn("parts", content)
        self.assertEqual(content["role"], "model")
        self.assertEqual(content["thought_text"], "The image")
        self.assertEqual(content["text"], "Final answer.")

    def test_model_request_trace_aggregates_content_parts(self) -> None:
        plugin = CreativeClawRuntimeTracePlugin()
        callback_context = SimpleNamespace(
            agent_name="CreativeClawOrchestrator",
            invocation_id="inv-1",
            state={},
        )
        llm_request = SimpleNamespace(
            contents=[
                {
                    "role": "model",
                    "parts": [
                        {
                            "function_call": {
                                "name": "edit_file",
                                "args": {
                                    "path": "src/app.py",
                                    "old_text": "before",
                                    "new_text": "after",
                                },
                            }
                        }
                    ],
                },
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "name": "edit_file",
                                "response": {"result": "ok"},
                            }
                        }
                    ],
                },
                {
                    "role": "model",
                    "parts": [
                        {"text": "I", "thought": True},
                        {"text": " still", "thought": True},
                        {"text": "Done."},
                    ],
                },
            ]
        )

        async def _run_callback() -> None:
            await plugin.before_model_callback(
                callback_context=callback_context,
                llm_request=llm_request,
            )

        with patch.dict(os.environ, {RUNTIME_TRACE_ENV_VAR: "1"}, clear=False):
            with patch("src.runtime.runtime_trace.logger.info") as logger_info:
                asyncio.run(_run_callback())

        payload = json.loads(logger_info.call_args.args[2])
        contents = payload["request"]["contents"]
        self.assertNotIn("parts", contents[0])
        self.assertEqual(contents[0]["function_calls"][0]["name"], "edit_file")
        self.assertEqual(contents[1]["function_responses"][0]["response"], {"result": "ok"})
        self.assertEqual(contents[2]["thought_text"], "I still")
        self.assertEqual(contents[2]["text"], "Done.")

    def test_user_message_trace_aggregates_content_parts(self) -> None:
        plugin = CreativeClawRuntimeTracePlugin()
        invocation_context = SimpleNamespace(
            app_name="CreativeClaw",
            user_id="user-1",
            invocation_id="inv-1",
            session=SimpleNamespace(id="session-1"),
            agent=SimpleNamespace(name="CreativeClawOrchestrator"),
        )
        user_message = {
            "role": "user",
            "parts": [
                {"text": "describe this"},
                {"inline_data": {"mime_type": "image/png", "data": "abcd"}},
            ],
        }

        async def _run_callback() -> None:
            await plugin.on_user_message_callback(
                invocation_context=invocation_context,
                user_message=user_message,
            )

        with patch.dict(os.environ, {RUNTIME_TRACE_ENV_VAR: "1"}, clear=False):
            with patch("src.runtime.runtime_trace.logger.info") as logger_info:
                asyncio.run(_run_callback())

        payload = json.loads(logger_info.call_args.args[2])
        message = payload["message"]
        self.assertNotIn("parts", message)
        self.assertEqual(message["text"], "describe this")
        self.assertEqual(
            message["other_content"],
            [{"inline_data": {"mime_type": "image/png", "data": "<data:4 chars>"}}],
        )

    def test_nested_trace_payload_aggregates_content_parts(self) -> None:
        rendered = serialize_trace_payload(
            {
                "history": [
                    {
                        "role": "model",
                        "parts": [
                            {"text": "thinking", "thought": True},
                            {
                                "function_response": {
                                    "name": "lookup",
                                    "response": {"api_key": "sk-secret", "value": 1},
                                }
                            },
                        ],
                    }
                ]
            }
        )

        payload = json.loads(rendered)
        content = payload["history"][0]
        self.assertNotIn("parts", content)
        self.assertEqual(content["thought_text"], "thinking")
        self.assertEqual(content["function_responses"][0]["response"]["api_key"], "[REDACTED]")

    def test_trace_payload_aggregates_genai_content_objects(self) -> None:
        rendered = serialize_trace_payload(
            {
                "message": Content(
                    role="model",
                    parts=[
                        Part(text="hello"),
                        Part(text=" hidden", thought=True),
                    ],
                )
            }
        )

        payload = json.loads(rendered)
        message = payload["message"]
        self.assertNotIn("parts", message)
        self.assertEqual(message["text"], "hello")
        self.assertEqual(message["thought_text"], " hidden")

    def test_runtime_trace_payload_redacts_secrets_without_truncating(self) -> None:
        with patch.dict(
            os.environ,
            {RUNTIME_TRACE_ENV_VAR: "1"},
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
        self.assertIn("x" * 1000, rendered)
        self.assertNotIn("<runtime trace truncated", rendered)
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
