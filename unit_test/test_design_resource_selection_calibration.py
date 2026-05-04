import json
import unittest
from pathlib import Path

from src.productions.design.design_product_manager import DesignProductManager


class DesignResourceSelectionCalibrationTests(unittest.TestCase):
    def _calibration_path(self) -> Path:
        return (
            Path(__file__).resolve().parents[1]
            / "evals"
            / "creative_claw_orchestrator"
            / "design_resource_selection_calibration.json"
        )

    def test_calibration_samples_match_resource_selection(self) -> None:
        payload = json.loads(self._calibration_path().read_text(encoding="utf-8"))
        manager = DesignProductManager()

        self.assertEqual(payload["version"], "creative-claw-design-resource-selection-calibration-v1")
        self.assertGreaterEqual(len(payload["cases"]), 10)
        for case in payload["cases"]:
            with self.subTest(case=case["id"]):
                brief = manager.prepare_brief(
                    prompt=case["prompt"],
                    scenario=case.get("scenario", ""),
                    allow_assumptions=True,
                )
                expected = case["expected"]
                self.assertEqual(brief.selection.surface, expected["surface"])
                self.assertEqual(brief.selection.brief_schema_id, expected["brief_schema_id"])
                self.assertEqual(brief.selection.task_skill, expected["task_skill"])
                self.assertEqual(brief.selection.design_system, expected["design_system"])
                self.assertEqual(brief.selection.device_frame, expected["device_frame"])


if __name__ == "__main__":
    unittest.main()
