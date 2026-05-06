import unittest
from types import SimpleNamespace
from unittest.mock import patch

from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService

from src.agents.orchestrator.orchestrator_agent import Orchestrator
from src.runtime.workspace import build_workspace_file_record, workspace_root


class DesignProductToolTests(unittest.IsolatedAsyncioTestCase):
    def test_orchestrator_exposes_design_product_tool(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )

        tool_names = {getattr(tool, "__name__", "") for tool in orchestrator.agent.tools}

        self.assertIn("run_design_product", tool_names)
        self.assertIn("run_design_product", orchestrator._build_instruction())

    async def test_run_design_product_returns_questions_without_generation(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={"sid": "design-test", "turn_index": 1, "step": 0, "expert_step": 0})

        with patch("src.productions.design.design_product_manager.design_product_manager.generate_code_artifact") as generate:
            result = await orchestrator.run_design_product(
                task="做一个后台看板。",
                tool_context=tool_context,
            )

        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["result_schema_version"], "design-product-result-v1")
        self.assertTrue(result["questions"])
        self.assertEqual(result["resource_selection"]["surface"], "dashboard")
        self.assertEqual(result["next_action"], "ask_user")
        self.assertEqual(result["design_issues"][0]["source"], "brief")
        generate.assert_not_called()
        self.assertIn("design_product_brief", tool_context.state)
        self.assertEqual(tool_context.state["design_product_result"]["status"], "needs_clarification")

    async def test_run_design_product_unknown_ui_returns_questions_without_generation(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={"sid": "design-test", "turn_index": 1, "step": 0, "expert_step": 0})

        with patch("src.productions.design.design_product_manager.design_product_manager.generate_code_artifact") as generate:
            result = await orchestrator.run_design_product(
                task="设计个嫦娥奔月主题餐厅的UI",
                tool_context=tool_context,
            )

        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["resource_selection"]["surface"], "unknown")
        self.assertEqual(result["resource_selection"]["brief_schema_id"], "brief_elements.unknown")
        self.assertEqual(result["resource_selection"]["task_skill"], "")
        questions_text = "\n".join(question["question"] for question in result["questions"])
        self.assertIn("官网/品牌页", questions_text)
        self.assertIn("点餐屏", questions_text)
        self.assertIn("顾客", questions_text)
        self.assertIn("店员", questions_text)
        self.assertIn("菜单浏览", questions_text)
        self.assertIn("订座", questions_text)
        self.assertIn("会员", questions_text)
        generate.assert_not_called()

    async def test_run_design_product_detailed_unknown_ui_calls_generation(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={"sid": "design-test", "turn_index": 1, "step": 0, "expert_step": 0})
        captured_request: dict[str, object] = {}

        async def _fake_generate(_runtime_context, **kwargs):
            captured_request.update(kwargs)
            output_file = workspace_root() / "generated/design-test/turn_1/turn1_step1_design_output0.html"
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(
                """<!doctype html>
<html lang="zh-CN">
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    main { display: grid; gap: 16px; }
    @media (max-width: 720px) { main { display: flex; flex-direction: column; } }
  </style>
</head>
<body>
  <main>
    <section>
      <h1>月宫点餐屏</h1>
      <p>面向到店顾客的嫦娥奔月主题餐厅界面，集中展示招牌菜单、双人套餐价格、预约订座入口、会员活动和月宫空间介绍。</p>
      <button>预约月宫雅座</button>
    </section>
  </main>
</body>
</html>
""",
                encoding="utf-8",
            )
            record = build_workspace_file_record(
                output_file,
                description="Design artifact generated by DesignProductManager.",
                source="design_product_manager",
                turn=1,
                step=1,
                expert_step=0,
            )
            return {
                "status": "success",
                "message": "Generated html code.",
                "output_text": "",
                "output_files": [record],
                "error_type": "",
                "retryable": False,
                "raw_error_summary": "",
                "language": "html",
                "output_path": record["path"],
                "warnings": [],
            }

        with patch(
            "src.productions.design.design_product_manager.design_product_manager.generate_code_artifact",
            side_effect=_fake_generate,
        ) as generate:
            result = await orchestrator.run_design_product(
                task="设计一个嫦娥奔月主题餐厅点餐屏，面向顾客，包含菜单浏览、套餐价格、预约订座和会员活动。",
                tool_context=tool_context,
            )

        generate.assert_called_once()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["resource_selection"]["surface"], "unknown")
        self.assertEqual(result["resource_selection"]["brief_schema_id"], "brief_elements.unknown")
        self.assertIn("Do not turn this into an operations dashboard", captured_request["prompt"])
        self.assertIn("点餐屏", captured_request["prompt"])
        self.assertEqual(result["next_action"], "deliver_artifact")

    async def test_run_design_product_calls_code_generation_and_records_output(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={"sid": "design-test", "turn_index": 1, "step": 0, "expert_step": 0})
        captured_request: dict[str, object] = {}

        async def _fake_generate(_runtime_context, **kwargs):
            captured_request.update(kwargs)
            output_file = workspace_root() / "generated/design-test/turn_1/turn1_step1_design_output0.html"
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(
                """<!doctype html>
<html lang="en">
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    main { display: grid; gap: 16px; }
    @media (max-width: 720px) { main { display: flex; flex-direction: column; } }
  </style>
</head>
<body>
  <main>
    <section><h1>Operations Dashboard</h1><p>DAU, conversion, retention, and channel ROI for operators.</p></section>
  </main>
</body>
</html>
""",
                encoding="utf-8",
            )
            record = build_workspace_file_record(
                output_file,
                description="Design artifact generated by DesignProductManager.",
                source="design_product_manager",
                turn=1,
                step=1,
                expert_step=0,
            )
            return {
                "status": "success",
                "message": "Generated html code.",
                "output_text": "",
                "output_files": [record],
                "error_type": "",
                "retryable": False,
                "raw_error_summary": "",
                "language": "html",
                "output_path": record["path"],
                "warnings": [],
            }

        with patch(
            "src.productions.design.design_product_manager.design_product_manager.generate_code_artifact",
            side_effect=_fake_generate,
        ) as generate:
            result = await orchestrator.run_design_product(
                task="设计一个运营数据 UI，展示 DAU、转化率、留存和渠道 ROI。",
                tool_context=tool_context,
            )

        generate.assert_called_once()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result_schema_version"], "design-product-result-v1")
        self.assertEqual(result["brief"]["selection"]["surface"], "dashboard")
        self.assertEqual(result["brief"]["selection"]["brief_schema_id"], "brief_elements.operation_data_ui")
        self.assertEqual(result["brief"]["selection"]["task_skill"], "dashboard")
        self.assertEqual(result["brief"]["selection"]["design_system"], "linear-app")
        self.assertEqual(result["brief"]["design_brief"]["schema_version"], "design-brief-v1")
        self.assertEqual(result["resource_selection"]["surface"], "dashboard")
        self.assertEqual(result["next_action"], "deliver_artifact")
        self.assertEqual(result["design_issues"], [])
        self.assertEqual(len(result["output_files"]), 1)
        self.assertEqual(captured_request["output_type"], "design")
        self.assertEqual(captured_request["output_source"], "design_product_manager")
        self.assertIn("operation_data_ui", captured_request["prompt"])
        self.assertIn("design_product_result", tool_context.state)
        self.assertEqual(tool_context.state["new_files"][0]["path"], result["output_files"][0]["path"])
        self.assertEqual(result["design_validation"][0]["status"], "pass")
        self.assertTrue(result["design_validation"][0]["checks"]["parseable_html"])

    async def test_run_design_product_reports_generation_failure_through_manager(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={"sid": "design-test", "turn_index": 1, "step": 0, "expert_step": 0})

        async def _fake_generate(_runtime_context, **_kwargs):
            return {
                "status": "error",
                "message": "Code artifact generation failed: API overloaded.",
                "output_text": "",
                "output_files": [],
                "error_type": "api_overloaded",
                "retryable": True,
                "raw_error_summary": "ServiceUnavailable: overloaded",
                "language": "html",
                "output_path": "",
                "warnings": [],
            }

        with patch(
            "src.productions.design.design_product_manager.design_product_manager.generate_code_artifact",
            side_effect=_fake_generate,
        ):
            result = await orchestrator.run_design_product(
                task="设计一个运营数据 dashboard，展示 DAU。",
                tool_context=tool_context,
            )

        self.assertEqual(result["status"], "generation_failed")
        self.assertEqual(result["next_action"], "user_can_retry_generation")
        self.assertEqual(result["design_issues"][0]["source"], "code_generation")
        self.assertEqual(result["design_issues"][0]["error_type"], "api_overloaded")
        self.assertEqual(result["design_validation"], [])
        self.assertEqual(tool_context.state["design_product_result"]["status"], "generation_failed")


if __name__ == "__main__":
    unittest.main()
