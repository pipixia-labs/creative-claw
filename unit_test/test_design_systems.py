import unittest

from src.productions.design.design_systems import (
    list_design_systems,
    read_design_system,
    resolve_design_system_preview,
)


class DesignSystemRegistryTests(unittest.TestCase):
    def test_lists_design_system_summaries_with_preview_urls(self) -> None:
        systems = list_design_systems()
        by_id = {system.id: system for system in systems}

        self.assertIn("claude", by_id)
        self.assertEqual(by_id["claude"].title, "Claude")
        self.assertTrue(by_id["claude"].summary)
        self.assertTrue(by_id["claude"].swatches)
        self.assertEqual(by_id["claude"].preview_url, "/api/design-systems/claude/preview")
        self.assertEqual(by_id["claude"].showcase_url, "/api/design-systems/claude/showcase")

    def test_reads_design_system_and_resolves_preview_safely(self) -> None:
        body = read_design_system("claude")
        preview = resolve_design_system_preview("claude")
        rejected = resolve_design_system_preview("../claude")

        self.assertIsNotNone(body)
        self.assertIn("name: Claude", body or "")
        self.assertIsNotNone(preview)
        self.assertTrue(preview.name.endswith(".html"))
        self.assertIsNone(rejected)


if __name__ == "__main__":
    unittest.main()
