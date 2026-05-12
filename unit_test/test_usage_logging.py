import asyncio
import unittest
from types import SimpleNamespace

from src.runtime.usage_logging import (
    CreativeClawUsageLoggingPlugin,
    LLM_USAGE_HISTORY_STATE_KEY,
    LLM_USAGE_TOTALS_STATE_KEY,
    extract_usage_metadata,
)


class _UsageMetadata:
    prompt_token_count = 10
    candidates_token_count = 4
    total_token_count = 14
    cached_content_token_count = 2
    thoughts_token_count = None
    tool_use_prompt_token_count = 1


class UsageLoggingTests(unittest.TestCase):
    def test_extract_usage_metadata_keeps_numeric_fields(self) -> None:
        usage = extract_usage_metadata(SimpleNamespace(usage_metadata=_UsageMetadata()))

        self.assertEqual(usage["prompt_token_count"], 10)
        self.assertEqual(usage["candidates_token_count"], 4)
        self.assertEqual(usage["total_token_count"], 14)
        self.assertEqual(usage["cached_content_token_count"], 2)
        self.assertEqual(usage["tool_use_prompt_token_count"], 1)
        self.assertNotIn("thoughts_token_count", usage)

    def test_plugin_logs_usage_to_state(self) -> None:
        plugin = CreativeClawUsageLoggingPlugin()
        state = {}
        callback_context = SimpleNamespace(agent_name="TestAgent", state=state)
        response = SimpleNamespace(
            model_version="test-model",
            usage_metadata={
                "prompt_token_count": 8,
                "candidates_token_count": 3,
                "total_token_count": 11,
            },
        )

        asyncio.run(
            plugin.after_model_callback(
                callback_context=callback_context,
                llm_response=response,
            )
        )

        self.assertEqual(state[LLM_USAGE_TOTALS_STATE_KEY]["total_token_count"], 11)
        self.assertEqual(state[LLM_USAGE_TOTALS_STATE_KEY]["prompt_token_count"], 8)
        self.assertEqual(len(state[LLM_USAGE_HISTORY_STATE_KEY]), 1)
        self.assertEqual(state[LLM_USAGE_HISTORY_STATE_KEY][0]["agent_name"], "TestAgent")


if __name__ == "__main__":
    unittest.main()
