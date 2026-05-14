import unittest

from google.genai import types

from conf.openai_codex import (
    _CodexAccumulator,
    _build_responses_body,
    strip_openai_codex_model_prefix,
)


class OpenAICodexLlmTests(unittest.TestCase):
    def test_strip_openai_codex_model_prefix_accepts_hyphen_and_underscore(self) -> None:
        self.assertEqual(strip_openai_codex_model_prefix("openai-codex/gpt-5.5"), "gpt-5.5")
        self.assertEqual(strip_openai_codex_model_prefix("openai_codex/gpt-5.5"), "gpt-5.5")
        self.assertEqual(strip_openai_codex_model_prefix("gpt-5.5"), "gpt-5.5")

    def test_build_responses_body_converts_messages_tools_and_schema(self) -> None:
        body = _build_responses_body(
            messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "hello"},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "description": "Lookup a value.",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                    },
                }
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "result",
                    "strict": True,
                    "schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
                },
            },
            generation_params={"max_completion_tokens": 123},
            model="openai_codex/gpt-5.5",
        )

        self.assertEqual(body["model"], "gpt-5.5")
        self.assertEqual(body["instructions"], "system prompt")
        self.assertEqual(body["input"][0]["content"][0]["text"], "hello")
        self.assertEqual(body["tools"][0]["name"], "lookup")
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["max_output_tokens"], 123)
        self.assertEqual(body["text"]["format"]["name"], "result")

    def test_codex_accumulator_builds_partial_and_final_adk_responses(self) -> None:
        accumulator = _CodexAccumulator(model_version="openai_codex/gpt-5.5")

        partial = accumulator.process_event(
            {"type": "response.output_text.delta", "delta": "Hello"},
            emit_partial=True,
        )
        accumulator.process_event(
            {
                "type": "response.output_item.added",
                "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "lookup"},
            },
            emit_partial=True,
        )
        accumulator.process_event(
            {"type": "response.function_call_arguments.done", "call_id": "call_1", "arguments": '{"query":"x"}'},
            emit_partial=True,
        )
        accumulator.process_event(
            {
                "type": "response.output_item.done",
                "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "lookup"},
            },
            emit_partial=True,
        )
        accumulator.process_event(
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
                },
            },
            emit_partial=True,
        )

        self.assertTrue(partial.partial)
        self.assertEqual(partial.content.parts[0].text, "Hello")

        final = accumulator.final_response()
        self.assertFalse(final.partial)
        self.assertEqual(final.finish_reason, types.FinishReason.STOP)
        self.assertEqual(final.content.parts[0].text, "Hello")
        self.assertEqual(final.content.parts[1].function_call.name, "lookup")
        self.assertEqual(final.content.parts[1].function_call.args["query"], "x")
        self.assertEqual(final.usage_metadata.total_token_count, 7)


if __name__ == "__main__":
    unittest.main()
