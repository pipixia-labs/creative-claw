import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService

from src.agents.orchestrator.orchestrator_agent import Orchestrator


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
        self.assertIn("private product-design skills", orchestrator._build_instruction())

    async def test_run_design_product_delegates_to_design_product_manager_agent(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={"sid": "design-test", "turn_index": 1})
        expected_result = {
            "result_schema_version": "design-product-result-v2",
            "status": "success",
            "product_line": "design",
            "message": "done",
            "final_file_paths": [],
        }
        mocked_run = AsyncMock(return_value=expected_result)
        with patch.object(type(orchestrator.design_product_manager), "run_product_request", new=mocked_run):
            result = await orchestrator.run_design_product(
                task="设计一个 landing page。",
                inputs=[{"path": "input/example.md"}],
                output={"format": "html"},
                tool_context=tool_context,
            )

        self.assertEqual(result, expected_result)
        mocked_run.assert_awaited_once_with(
            task="设计一个 landing page。",
            inputs=[{"path": "input/example.md"}],
            output={"format": "html"},
            tool_context=tool_context,
            expert_agents=orchestrator.expert_agents,
            app_name=orchestrator.app_name,
            artifact_service=orchestrator.artifact_service,
        )


if __name__ == "__main__":
    unittest.main()
