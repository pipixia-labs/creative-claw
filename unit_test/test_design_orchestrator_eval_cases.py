import json
import unittest
from pathlib import Path

from src.agents.design_product_manager import DesignProductManager


class DesignOrchestratorEvalCaseTests(unittest.TestCase):
    def _eval_path(self) -> Path:
        return (
            Path(__file__).resolve().parents[1]
            / "evals"
            / "creative_claw_orchestrator"
            / "design_product_cases.json"
        )

    def test_design_eval_cases_have_expected_tool_contract(self) -> None:
        payload = json.loads(self._eval_path().read_text(encoding="utf-8"))

        self.assertEqual(payload["version"], "creative-claw-orchestrator-design-eval-v1")
        self.assertEqual(payload["agent"], "Orchestrator")
        self.assertGreaterEqual(len(payload["cases"]), 4)
        for case in payload["cases"]:
            self.assertEqual(case["expected_tool"], "run_design_product", case["id"])
            self.assertIn("user_message", case)
            self.assertIn("scenario", case["expected_arguments"])
            self.assertIn("allow_assumptions", case["expected_arguments"])
            self.assertIn("brief_schema_id", case["expected_result"])
            self.assertTrue(case["assertions"], case["id"])

    def test_design_eval_cases_match_design_product_manager_selection(self) -> None:
        payload = json.loads(self._eval_path().read_text(encoding="utf-8"))
        manager = DesignProductManager()

        for case in payload["cases"]:
            brief = manager.prepare_brief(
                prompt=case["user_message"],
                scenario=case["expected_arguments"]["scenario"],
                allow_assumptions=case["expected_arguments"]["allow_assumptions"],
            )

            self.assertEqual(
                brief.selection.brief_schema_id,
                case["expected_result"]["brief_schema_id"],
                case["id"],
            )
            self.assertEqual(
                brief.selection.task_skill,
                case["expected_result"]["task_skill"],
                case["id"],
            )


if __name__ == "__main__":
    unittest.main()
