import unittest
from types import SimpleNamespace

from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService

from src.agents.orchestrator.orchestrator_agent import Orchestrator


class PptProductToolTests(unittest.IsolatedAsyncioTestCase):
    def test_orchestrator_exposes_ppt_product_tool(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )

        tool_names = {getattr(tool, "__name__", "") for tool in orchestrator.agent.tools}
        instruction = orchestrator._build_instruction()

        self.assertIn("run_ppt_product", tool_names)
        self.assertIn("continue_ppt_product", tool_names)
        self.assertIn("run_ppt_product", instruction)
        self.assertIn("continue_ppt_product", instruction)
        self.assertIn("PPT workflow routing hints", instruction)
        self.assertIn("Do not route PPTX delivery through DesignProductManager", instruction)
        self.assertIn("HTML route first", instruction)
        self.assertIn("skills/product-ppt-skills", instruction)

    async def test_run_ppt_product_returns_structured_status(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={"sid": "ppt-test", "turn_index": 1, "step": 0, "expert_step": 0})

        result = await orchestrator.run_ppt_product(
            task="做一个 5 页 PPTX，用于季度汇报。",
            inputs=[{"name": "brief.md", "path": "inbox/demo/brief.md"}],
            output={"format": "pptx"},
            tool_context=tool_context,
        )

        self.assertEqual(result["status"], "awaiting_requirement_confirmation")
        self.assertEqual(result["result_schema_version"], "ppt-product-result-v1")
        self.assertEqual(result["selected_route"], "html")
        self.assertEqual(result["confirmed_requirement"]["slide_count_policy"]["target"], 5)
        self.assertEqual(tool_context.state["ppt_product_result"]["status"], "awaiting_requirement_confirmation")
        self.assertEqual(tool_context.state["ppt_workflow_state"]["stage"], "awaiting_requirement_confirmation")
        self.assertIn("summary_markdown", result["confirmation_request"])
        self.assertNotIn("final_file_paths", tool_context.state)
        self.assertEqual(tool_context.state["orchestration_events"][0]["title"], "Run PPT Product")
        self.assertEqual(tool_context.state["orchestration_events"][0]["stage"], "ppt_product_planning")


if __name__ == "__main__":
    unittest.main()
