import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.genai.types import Content, Part

from src.agents.experts.code_generation.code_generation_expert import CodeGenerationExpert
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
    def __init__(self, text: str) -> None:
        self.content = Content(role="model", parts=[Part(text=text)])

    def is_final_response(self) -> bool:
        return True


class CodeGenerationExpertTests(unittest.IsolatedAsyncioTestCase):
    def test_strip_code_fence_removes_surrounding_fence(self) -> None:
        self.assertEqual(strip_code_fence("```html\n<div>ok</div>\n```"), "<div>ok</div>")

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
                    context_files=["skills/design-knowledge-and-skills/brief-elements/dashboard.json"],
                    constraints=["single self-contained HTML"],
                )
            generated_content = output_path.read_text(encoding="utf-8").strip()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output_path"], relative_output_path)
        self.assertEqual(result["error_type"], "")
        self.assertFalse(result["retryable"])
        self.assertEqual(generated_content, "<!doctype html><html><body>Dashboard</body></html>")
        self.assertIn("single self-contained HTML", captured_request["text"])
        self.assertIn("brief-elements/dashboard.json", captured_request["text"])
        self.assertIn("operations dashboard", captured_request["text"])

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
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "success")
        self.assertEqual(current_output["output_files"][0]["path"], "generated/design/dashboard.html")
        self.assertEqual(current_output["error_type"], "")
        self.assertFalse(current_output["retryable"])
        self.assertEqual(events[0].actions.state_delta["code_generation_results"]["language"], "html")


if __name__ == "__main__":
    unittest.main()
