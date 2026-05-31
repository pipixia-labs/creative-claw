from types import SimpleNamespace
import unittest

from src.runtime.adk_compat import (
    get_invocation_context,
    has_invocation_context,
    invocation_app_name,
)


class AdkCompatTests(unittest.TestCase):
    def test_invocation_context_helpers_unwrap_tool_context(self) -> None:
        invocation_context = SimpleNamespace(app_name="creative_claw_test")
        tool_context = SimpleNamespace(_invocation_context=invocation_context)

        self.assertTrue(has_invocation_context(tool_context))
        self.assertIs(get_invocation_context(tool_context), invocation_context)
        self.assertEqual(invocation_app_name(tool_context), "creative_claw_test")

    def test_invocation_context_helpers_accept_plain_context(self) -> None:
        plain_context = SimpleNamespace(app_name="plain_app")

        self.assertFalse(has_invocation_context(plain_context))
        self.assertIs(get_invocation_context(plain_context), plain_context)
        self.assertEqual(invocation_app_name(plain_context), "plain_app")
        self.assertEqual(invocation_app_name(SimpleNamespace(), default="fallback_app"), "fallback_app")


if __name__ == "__main__":
    unittest.main()
