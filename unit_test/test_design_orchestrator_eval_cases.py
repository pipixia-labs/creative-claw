import json
import unittest
from pathlib import Path


class DesignOrchestratorEvalCaseTests(unittest.TestCase):
    def _eval_path(self) -> Path:
        return (
            Path(__file__).resolve().parents[1]
            / "evals"
            / "creative_claw_orchestrator"
            / "design_product_cases.json"
        )

    def test_design_eval_cases_still_route_to_design_product_tool(self) -> None:
        payload = json.loads(self._eval_path().read_text(encoding="utf-8"))

        self.assertEqual(payload["version"], "creative-claw-orchestrator-design-eval-v1")
        self.assertEqual(payload["agent"], "Orchestrator")
        self.assertGreaterEqual(len(payload["cases"]), 4)
        for case in payload["cases"]:
            self.assertEqual(case["expected_tool"], "run_design_product", case["id"])
            self.assertIn("user_message", case)
            self.assertIn("task", case["expected_arguments"])


if __name__ == "__main__":
    unittest.main()
