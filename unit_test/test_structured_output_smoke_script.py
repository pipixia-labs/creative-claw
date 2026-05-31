import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts import run_orchestrator_structured_output_smoke as smoke


class StructuredOutputSmokeScriptTests(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_is_unsupported_without_force_native(self) -> None:
        args = SimpleNamespace(
            models=["deepseek/deepseek-v4-pro"],
            prompt="smoke",
            max_llm_calls=1,
            verbose=False,
            force_native=False,
        )

        with patch.object(smoke, "run_smoke_case", new=AsyncMock()) as mocked_run:
            results = await smoke.run_all(args)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["model"], "deepseek/deepseek-v4-pro")
        self.assertEqual(results[0]["status"], "unsupported")
        self.assertIn("prompt_json", results[0]["reason"])
        mocked_run.assert_not_awaited()

    async def test_force_native_runs_unsupported_provider(self) -> None:
        args = SimpleNamespace(
            models=["deepseek/deepseek-v4-pro"],
            prompt="smoke",
            max_llm_calls=1,
            verbose=False,
            force_native=True,
        )
        expected = {
            "model": "deepseek/deepseek-v4-pro",
            "status": "success",
            "function_calls": ["get_structured_output_smoke_status"],
        }

        with patch.object(smoke, "run_smoke_case", new=AsyncMock(return_value=expected)) as mocked_run:
            results = await smoke.run_all(args)

        self.assertEqual(results, [expected])
        mocked_run.assert_awaited_once_with(
            model_reference="deepseek/deepseek-v4-pro",
            prompt="smoke",
            max_llm_calls=1,
        )
