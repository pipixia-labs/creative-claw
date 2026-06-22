import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService

from src.agents.orchestrator.orchestrator_agent import Orchestrator


class PageProductToolTests(unittest.IsolatedAsyncioTestCase):
    def test_orchestrator_exposes_page_product_tool(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )

        tool_names = {getattr(tool, "__name__", "") for tool in orchestrator.agent.tools}
        instruction = orchestrator._build_instruction()

        self.assertIn("run_page_product", tool_names)
        self.assertIn("run_page_product", instruction)
        self.assertIn("Page workflow routing hints", instruction)
        self.assertIn("content-first HTML posters", instruction)
        self.assertIn("skills/product-page-skills", instruction)

    async def test_run_page_product_delegates_to_page_product_manager_agent(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={"sid": "page-test", "turn_index": 1})
        expected_result = {
            "result_schema_version": "page-product-result-v1",
            "status": "success",
            "product_line": "page",
            "message": "done",
            "final_file_paths": [],
        }
        mocked_run = AsyncMock(return_value=expected_result)
        with patch.object(type(orchestrator.page_product_manager), "run_product_request", new=mocked_run):
            result = await orchestrator.run_page_product(
                task="做一篇小红书长图。",
                inputs=[{"path": "input/example.md"}],
                output={"format": "html"},
                tool_context=tool_context,
            )

        self.assertEqual(result, expected_result)
        mocked_run.assert_awaited_once_with(
            task="做一篇小红书长图。",
            inputs=[{"path": "input/example.md"}],
            output={"format": "html"},
            interaction_language="zh",
            tool_context=tool_context,
            expert_agents=orchestrator.expert_agents,
            app_name=orchestrator.app_name,
            artifact_service=orchestrator.artifact_service,
        )


if __name__ == "__main__":
    unittest.main()
