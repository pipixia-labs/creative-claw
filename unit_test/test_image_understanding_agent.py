import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.agents.experts.image_understanding.image_understanding_agent import (
    ImageUnderstandingAgent,
    ImageUnderstandingOutput,
    ImageUnderstandingParameters,
    ImageUnderstandingResultItem,
)


def _build_ctx(state: dict) -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(
            state=state,
            app_name="test_app",
            user_id="user_1",
            id="session_1",
        ),
    )


class ImageUnderstandingAgentValidationTests(unittest.IsolatedAsyncioTestCase):
    def test_image_understanding_parameters_schema_normalizes_public_contract(self) -> None:
        parameters = ImageUnderstandingParameters.model_validate(
            {
                "input_path": " inbox/session/a.png ",
                "mode": " PROMPT ",
            }
        )

        self.assertEqual(parameters.input_paths, ["inbox/session/a.png"])
        self.assertEqual(parameters.mode, ["prompt"])
        self.assertEqual(parameters.modes_for_inputs(), ["prompt"])

    def test_image_understanding_parameters_repeats_single_mode_for_multiple_inputs(self) -> None:
        parameters = ImageUnderstandingParameters.model_validate(
            {
                "input_paths": ["inbox/session/a.png", "inbox/session/b.png"],
                "mode": [" OCR "],
            }
        )

        self.assertEqual(parameters.modes_for_inputs(), ["ocr", "ocr"])

    def test_image_understanding_output_schema_preserves_error_contract(self) -> None:
        item = ImageUnderstandingResultItem(
            input_path=" inbox/session/a.png ",
            mode=" PROMPT ",
            status=" SUCCESS ",
            message=" ok ",
        ).to_result_dict()
        current_output = ImageUnderstandingOutput(
            status="error",
            message=" failed ",
            results=[item],
        ).to_current_output()

        self.assertEqual(item["input_path"], "inbox/session/a.png")
        self.assertEqual(item["mode"], "PROMPT")
        self.assertEqual(item["status"], "success")
        self.assertEqual(current_output["status"], "error")
        self.assertEqual(current_output["message"], "failed")
        self.assertEqual(current_output["results"][0]["message"], "ok")

    async def test_agent_accepts_prompt_mode(self) -> None:
        agent = ImageUnderstandingAgent(name="ImageUnderstandingAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_path": "inbox/session/a.png",
                    "mode": "prompt",
                }
            }
        )

        with patch(
            "src.agents.experts.image_understanding.image_understanding_agent.image_to_text_tool",
            new=AsyncMock(return_value={"status": "success", "message": "ok"}),
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(events[0].actions.state_delta["current_output"]["status"], "success")

    async def test_agent_rejects_missing_image_inputs(self) -> None:
        agent = ImageUnderstandingAgent(name="ImageUnderstandingAgent")
        ctx = _build_ctx({"current_parameters": {"mode": "description"}})

        events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("must include: input_path or input_paths, mode", current_output["message"])

    async def test_agent_rejects_invalid_mode_values(self) -> None:
        agent = ImageUnderstandingAgent(name="ImageUnderstandingAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_path": "inbox/session/a.png",
                    "mode": "nonsense",
                }
            }
        )

        events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("Supported modes are", current_output["message"])


if __name__ == "__main__":
    unittest.main()
