import importlib.util
import json
import unittest
from pathlib import Path


class DesignLiveEvalAssetTests(unittest.TestCase):
    def _eval_root(self) -> Path:
        return Path(__file__).resolve().parents[1] / "evals" / "creative_claw_orchestrator"

    def test_live_evalset_targets_run_design_product(self) -> None:
        evalset = json.loads((self._eval_root() / "design_product_live_evalset.json").read_text(encoding="utf-8"))

        self.assertEqual(evalset["eval_set_id"], "creative_claw_design_product_live_eval")
        self.assertGreaterEqual(len(evalset["eval_cases"]), 2)
        for case in evalset["eval_cases"]:
            with self.subTest(case=case["eval_id"]):
                invocation = case["conversation"][0]
                tool_uses = invocation["intermediate_data"]["tool_uses"]
                self.assertEqual(tool_uses[0]["name"], "run_design_product")
                self.assertIn("task", tool_uses[0]["args"])
                self.assertEqual(tool_uses[0]["args"]["output"]["format"], "html")

    def test_live_eval_config_uses_tool_trajectory(self) -> None:
        config = json.loads((self._eval_root() / "adk_eval_config.json").read_text(encoding="utf-8"))

        trajectory = config["criteria"]["tool_trajectory_avg_score"]
        self.assertEqual(trajectory["threshold"], 1.0)
        self.assertEqual(trajectory["match_type"], "IN_ORDER")

    def test_live_eval_app_exports_root_agent_without_live_call(self) -> None:
        agent_path = self._eval_root() / "adk_app" / "agent.py"
        spec = importlib.util.spec_from_file_location("creative_claw_design_eval_agent", agent_path)

        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.root_agent.name, "CreativeClawOrchestrator")

    def test_live_eval_runner_scripts_exist(self) -> None:
        project_root = Path(__file__).resolve().parents[1]

        self.assertTrue((project_root / "scripts" / "run_design_orchestrator_live_eval.py").exists())
        self.assertTrue((project_root / "scripts" / "run_design_orchestrator_live_route_check.py").exists())


if __name__ == "__main__":
    unittest.main()
