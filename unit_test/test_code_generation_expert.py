import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.genai.types import Content, Part

from src.agents.experts.code_generation.code_generation_expert import (
    CodeGenerationExpert,
    CodeGenerationOutput,
    CodeGenerationParameters,
)
from src.agents.experts.code_generation.tool import code_generation_tool, strip_code_fence
from src.runtime.expert_dispatcher import normalize_invoke_agent_parameters
from src.runtime.expert_registry import build_expert_contract_summary
from src.runtime.workspace import workspace_relative_path, workspace_root


def _build_ctx(state: dict) -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(
            state=state,
            app_name="test_app",
            user_id="user_1",
            id="session_1",
        ),
    )


class _FakeEvent:
    def __init__(self, text: str, *, final: bool = True, partial: bool = False) -> None:
        self.content = Content(role="model", parts=[Part(text=text)])
        self.partial = partial
        self._final = final

    def is_final_response(self) -> bool:
        return self._final


class _FakePartsEvent:
    def __init__(self, parts: list[Part], *, final: bool = True, partial: bool = False) -> None:
        self.content = Content(role="model", parts=parts)
        self.partial = partial
        self._final = final

    def is_final_response(self) -> bool:
        return self._final


class CodeGenerationExpertTests(unittest.IsolatedAsyncioTestCase):
    def test_strip_code_fence_removes_surrounding_fence(self) -> None:
        self.assertEqual(strip_code_fence("```html\n<div>ok</div>\n```"), "<div>ok</div>")

    def test_code_generation_parameters_schema_normalizes_public_contract(self) -> None:
        parameters = CodeGenerationParameters.model_validate(
            {
                "prompt": "  Build a dashboard  ",
                "language": "",
                "output_path": " generated/design/dashboard.html ",
                "context_files": " src/context.md ",
                "constraints": (" single file ", ""),
            }
        )

        self.assertEqual(parameters.prompt, "Build a dashboard")
        self.assertEqual(parameters.language, "html")
        self.assertEqual(parameters.output_path, "generated/design/dashboard.html")
        self.assertEqual(parameters.context_files, [" src/context.md "])
        self.assertEqual(parameters.constraints, [" single file "])

    def test_code_generation_output_schema_preserves_current_output_contract(self) -> None:
        current_output = CodeGenerationOutput.from_tool_result(
            {
                "status": " SUCCESS ",
                "message": " Generated html code. ",
                "output_files": [{"path": "generated/design/dashboard.html"}],
                "language": " html ",
                "output_path": " generated/design/dashboard.html ",
                "provider": " google_adk ",
                "model_name": " fake-model ",
            }
        ).to_current_output()

        self.assertEqual(current_output["status"], "success")
        self.assertEqual(current_output["message"], "Generated html code.")
        self.assertEqual(current_output["output_text"], "Generated html code.")
        self.assertEqual(current_output["output_files"], [{"path": "generated/design/dashboard.html"}])
        self.assertEqual(current_output["language"], "html")
        self.assertEqual(current_output["retryable"], False)

    def test_registry_accepts_code_generation_parameters(self) -> None:
        parameters = normalize_invoke_agent_parameters(
            agent_name="CodeGenerationExpert",
            prompt='{"prompt":"Build a dashboard","language":"html","output_path":"generated/design/dashboard.html"}',
            state={},
        )

        self.assertEqual(parameters["prompt"], "Build a dashboard")
        self.assertEqual(parameters["language"], "html")
        self.assertEqual(parameters["output_path"], "generated/design/dashboard.html")
        self.assertIn("CodeGenerationExpert", build_expert_contract_summary())

    async def test_code_generation_tool_writes_generated_file(self) -> None:
        captured_request: dict[str, str] = {}

        class _FakeLlmAgent:
            def __init__(self, **kwargs) -> None:
                self.before_model_callback = kwargs["before_model_callback"]

            async def run_async(self, ctx):
                llm_request = SimpleNamespace(contents=[])
                self.before_model_callback(SimpleNamespace(state={}), llm_request)
                captured_request["text"] = llm_request.contents[0].parts[0].text
                yield _FakeEvent("```html\n<!doctype html><html><body>Dashboard</body></html>\n```")

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmp_dir:
            output_path = Path(tmp_dir) / "dashboard.html"
            relative_output_path = workspace_relative_path(output_path)

            with (
                patch("src.runtime.code_artifacts.LlmAgent", _FakeLlmAgent),
                patch("src.runtime.code_artifacts.build_llm", return_value="fake-model"),
                patch(
                    "src.runtime.code_artifacts.resolve_llm_model_name",
                    return_value="fake-model",
                ),
            ):
                result = await code_generation_tool(
                    _build_ctx({"turn_index": 0, "step": 0}),
                    prompt="Create an operations dashboard.",
                    language="html",
                    output_path=relative_output_path,
                    context_files=["src/productions/design/design-systems/claude/DESIGN.md"],
                    constraints=["single self-contained HTML"],
                )
            generated_content = output_path.read_text(encoding="utf-8").strip()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output_path"], relative_output_path)
        self.assertEqual(result["error_type"], "")
        self.assertFalse(result["retryable"])
        self.assertEqual(generated_content, "<!doctype html><html><body>Dashboard</body></html>")
        self.assertIn("single self-contained HTML", captured_request["text"])
        self.assertIn("design-systems/claude/DESIGN.md", captured_request["text"])
        self.assertIn("operations dashboard", captured_request["text"])

    async def test_code_generation_tool_prefers_structured_save_tool_result(self) -> None:
        captured: dict[str, object] = {}

        class _ToolCallingLlmAgent:
            def __init__(self, **kwargs) -> None:
                self.before_model_callback = kwargs["before_model_callback"]
                self.tools = kwargs["tools"]

            async def run_async(self, ctx):
                llm_request = SimpleNamespace(contents=[])
                self.before_model_callback(SimpleNamespace(state={}), llm_request)
                tool_context = SimpleNamespace(
                    state={},
                    actions=SimpleNamespace(skip_summarization=False),
                )
                captured["tool_result"] = await self.tools[0](
                    content=(
                        "I will save the HTML now.\n"
                        "<!DOCTYPE html><html><body>Tool Saved</body></html>\n"
                        "Done."
                    ),
                    tool_context=tool_context,
                )
                captured["skip_summarization"] = tool_context.actions.skip_summarization
                yield _FakeEvent("This assistant prose must not be written.")

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmp_dir:
            output_path = Path(tmp_dir) / "tool-save.html"
            relative_output_path = workspace_relative_path(output_path)

            with (
                patch("src.runtime.code_artifacts.LlmAgent", _ToolCallingLlmAgent),
                patch("src.runtime.code_artifacts.build_llm", return_value="fake-model"),
                patch(
                    "src.runtime.code_artifacts.resolve_llm_model_name",
                    return_value="fake-model",
                ),
            ):
                result = await code_generation_tool(
                    _build_ctx({"turn_index": 0, "step": 0}),
                    prompt="Create an operations dashboard.",
                    language="html",
                    output_path=relative_output_path,
                )
            generated_content = output_path.read_text(encoding="utf-8").strip()

        self.assertEqual(result["status"], "success")
        self.assertTrue(captured["skip_summarization"])
        self.assertEqual(captured["tool_result"]["status"], "success")
        self.assertEqual(generated_content, "<!DOCTYPE html><html><body>Tool Saved</body></html>")
        self.assertNotIn("assistant prose", generated_content)
        self.assertNotIn("I will save", generated_content)

    async def test_code_generation_tool_filters_thought_and_extracts_html_fallback(self) -> None:
        class _ThoughtAndProseLlmAgent:
            def __init__(self, **_kwargs) -> None:
                pass

            async def run_async(self, ctx):
                yield _FakePartsEvent(
                    [
                        Part(text="The user wants me to generate a UI prototype.", thought=True),
                        Part(
                            text=(
                                "Here is the complete file:\n"
                                "<!DOCTYPE html><html><body>Clean HTML</body></html>\n"
                                "No extra notes."
                            )
                        ),
                    ]
                )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmp_dir:
            output_path = Path(tmp_dir) / "clean.html"
            relative_output_path = workspace_relative_path(output_path)

            with (
                patch("src.runtime.code_artifacts.LlmAgent", _ThoughtAndProseLlmAgent),
                patch("src.runtime.code_artifacts.build_llm", return_value="fake-model"),
                patch(
                    "src.runtime.code_artifacts.resolve_llm_model_name",
                    return_value="fake-model",
                ),
            ):
                result = await code_generation_tool(
                    _build_ctx({"turn_index": 0, "step": 0}),
                    prompt="Create an operations dashboard.",
                    language="html",
                    output_path=relative_output_path,
                )
            generated_content = output_path.read_text(encoding="utf-8").strip()

        self.assertEqual(result["status"], "success")
        self.assertEqual(generated_content, "<!DOCTYPE html><html><body>Clean HTML</body></html>")
        self.assertNotIn("The user wants", generated_content)
        self.assertNotIn("Here is the complete file", generated_content)
        self.assertTrue(any("Dropped non-HTML text before" in warning for warning in result["warnings"]))

    async def test_code_generation_tool_rejects_invalid_html_fallback(self) -> None:
        class _InvalidHtmlLlmAgent:
            def __init__(self, **_kwargs) -> None:
                pass

            async def run_async(self, ctx):
                yield _FakeEvent("Here is a fragment: <div>Missing document shell</div>")

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmp_dir:
            output_path = Path(tmp_dir) / "invalid.html"
            relative_output_path = workspace_relative_path(output_path)

            with (
                patch("src.runtime.code_artifacts.LlmAgent", _InvalidHtmlLlmAgent),
                patch("src.runtime.code_artifacts.build_llm", return_value="fake-model"),
                patch(
                    "src.runtime.code_artifacts.resolve_llm_model_name",
                    return_value="fake-model",
                ),
            ):
                result = await code_generation_tool(
                    _build_ctx({"turn_index": 0, "step": 0}),
                    prompt="Create an operations dashboard.",
                    language="html",
                    output_path=relative_output_path,
                )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "invalid_generated_content")
        self.assertTrue(result["retryable"])
        self.assertFalse(output_path.exists())

    async def test_code_generation_tool_reports_empty_model_response(self) -> None:
        class _EmptyLlmAgent:
            def __init__(self, **_kwargs) -> None:
                pass

            async def run_async(self, ctx):
                yield _FakeEvent("")

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmp_dir:
            output_path = Path(tmp_dir) / "empty.html"
            relative_output_path = workspace_relative_path(output_path)

            with (
                patch("src.runtime.code_artifacts.LlmAgent", _EmptyLlmAgent),
                patch("src.runtime.code_artifacts.build_llm", return_value="fake-model"),
                patch(
                    "src.runtime.code_artifacts.resolve_llm_model_name",
                    return_value="fake-model",
                ),
            ):
                result = await code_generation_tool(
                    _build_ctx({"turn_index": 0, "step": 0}),
                    prompt="Create an operations dashboard.",
                    language="html",
                    output_path=relative_output_path,
                )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "empty_result")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["raw_error_summary"], "empty model response")
        self.assertFalse(output_path.exists())

    async def test_code_generation_tool_writes_non_final_text_fallback(self) -> None:
        class _NonFinalLlmAgent:
            def __init__(self, **_kwargs) -> None:
                pass

            async def run_async(self, ctx):
                yield _FakeEvent(
                    "```html\n<!doctype html><html><body>Fallback</body></html>\n```",
                    final=False,
                )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmp_dir:
            output_path = Path(tmp_dir) / "fallback.html"
            relative_output_path = workspace_relative_path(output_path)

            with (
                patch("src.runtime.code_artifacts.LlmAgent", _NonFinalLlmAgent),
                patch("src.runtime.code_artifacts.build_llm", return_value="fake-model"),
                patch(
                    "src.runtime.code_artifacts.resolve_llm_model_name",
                    return_value="fake-model",
                ),
            ):
                result = await code_generation_tool(
                    _build_ctx({"turn_index": 0, "step": 0}),
                    prompt="Create an operations dashboard.",
                    language="html",
                    output_path=relative_output_path,
                )

            generated_content = output_path.read_text(encoding="utf-8").strip()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output_path"], relative_output_path)
        self.assertEqual(generated_content, "<!doctype html><html><body>Fallback</body></html>")

    async def test_code_generation_tool_reports_retryable_network_errors(self) -> None:
        class _FailingLlmAgent:
            def __init__(self, **_kwargs) -> None:
                pass

            async def run_async(self, ctx):
                raise TimeoutError("network timeout")
                yield

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmp_dir:
            output_path = Path(tmp_dir) / "network.html"
            relative_output_path = workspace_relative_path(output_path)

            with (
                patch("src.runtime.code_artifacts.LlmAgent", _FailingLlmAgent),
                patch("src.runtime.code_artifacts.build_llm", return_value="fake-model"),
                patch(
                    "src.runtime.code_artifacts.resolve_llm_model_name",
                    return_value="fake-model",
                ),
            ):
                result = await code_generation_tool(
                    _build_ctx({"turn_index": 0, "step": 0}),
                    prompt="Create an operations dashboard.",
                    language="html",
                    output_path=relative_output_path,
                )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "network_error")
        self.assertTrue(result["retryable"])
        self.assertIn("TimeoutError", result["raw_error_summary"])
        self.assertFalse(output_path.exists())

    async def test_code_generation_tool_reports_generation_timeout(self) -> None:
        class _HangingLlmAgent:
            def __init__(self, **_kwargs) -> None:
                pass

            async def run_async(self, ctx):
                await asyncio.sleep(1)
                yield _FakeEvent("<!doctype html><html><body>Late</body></html>")

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmp_dir:
            output_path = Path(tmp_dir) / "timeout.html"
            relative_output_path = workspace_relative_path(output_path)

            with (
                patch("src.runtime.code_artifacts.LlmAgent", _HangingLlmAgent),
                patch("src.runtime.code_artifacts.build_llm", return_value="fake-model"),
                patch(
                    "src.runtime.code_artifacts.resolve_llm_model_name",
                    return_value="fake-model",
                ),
                patch("src.runtime.code_artifacts._CODE_ARTIFACT_TIMEOUT_SECONDS", 0.01),
            ):
                result = await code_generation_tool(
                    _build_ctx({"turn_index": 0, "step": 0}),
                    prompt="Create an operations dashboard.",
                    language="html",
                    output_path=relative_output_path,
                )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "network_error")
        self.assertTrue(result["retryable"])
        self.assertIn("timed out after 0.01 seconds", result["raw_error_summary"])
        self.assertFalse(output_path.exists())

    async def test_code_generation_expert_requires_prompt(self) -> None:
        agent = CodeGenerationExpert(name="CodeGenerationExpert")
        ctx = _build_ctx({"current_parameters": {"language": "html"}})

        events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("must include: prompt", current_output["message"])
        self.assertEqual(current_output["error_type"], "invalid_parameters")
        self.assertFalse(current_output["retryable"])

    async def test_code_generation_expert_emits_output_files(self) -> None:
        agent = CodeGenerationExpert(name="CodeGenerationExpert")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "prompt": "Build a dashboard",
                    "language": "html",
                    "output_path": "generated/design/dashboard.html",
                    "context_files": "generated/context.md",
                    "constraints": ["single file"],
                }
            }
        )

        with patch(
            "src.agents.experts.code_generation.code_generation_expert.code_generation_tool",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "message": "Generated html code at generated/design/dashboard.html.",
                    "output_path": "generated/design/dashboard.html",
                    "output_files": [
                        {
                            "path": "generated/design/dashboard.html",
                            "description": "Generated dashboard.",
                            "source": "expert",
                        }
                    ],
                    "language": "html",
                    "provider": "google_adk",
                    "model_name": "fake-model",
                    "error_type": "",
                    "retryable": False,
                    "raw_error_summary": "",
                    "warnings": [],
                }
            ),
        ) as mocked_generation:
            events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "success")
        self.assertEqual(current_output["output_files"][0]["path"], "generated/design/dashboard.html")
        self.assertEqual(current_output["error_type"], "")
        self.assertFalse(current_output["retryable"])
        self.assertEqual(events[0].actions.state_delta["code_generation_results"]["language"], "html")
        self.assertEqual(mocked_generation.await_args.kwargs["context_files"], ["generated/context.md"])
        self.assertEqual(mocked_generation.await_args.kwargs["constraints"], ["single file"])


if __name__ == "__main__":
    unittest.main()
