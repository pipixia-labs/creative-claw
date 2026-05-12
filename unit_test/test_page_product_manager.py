import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.adk.agents import LlmAgent

from src.productions.page.page_product_manager import (
    PAGE_PRODUCT_EXPERT_ALLOWLIST,
    PAGE_PRODUCT_RESULT_SCHEMA_VERSION,
    PageCodeGenerationAgent,
    PageProductManager,
    ProductPageSkillRegistry,
    build_page_code_generation_constraints,
    build_page_code_generation_prompt,
)
from src.runtime.workspace import resolve_workspace_path
from src.skills.registry import SkillRegistry


class PageProductManagerTests(unittest.TestCase):
    def test_page_product_manager_is_llm_agent_with_private_tools(self) -> None:
        manager = PageProductManager()

        self.assertIsInstance(manager, LlmAgent)
        self.assertEqual(manager.name, "PageProductManager")
        self.assertEqual(
            {tool.__name__ for tool in manager.tools},
            {
                "list_product_page_skills",
                "read_product_page_skill",
                "list_page_experts",
                "invoke_page_expert",
                "invoke_page_code_generation",
                "emit_page_progress",
                "save_page_artifact",
                "validate_page_artifact",
                "register_page_delivery",
            },
        )
        self.assertIn("content-first HTML page tasks", manager.instruction)
        self.assertIn("skills/product-page-skills", manager.instruction)
        self.assertIn("poster-page-designer", manager.instruction)
        self.assertIn("invoke_page_code_generation", manager.instruction)
        self.assertIn('provider="nano_banana"', manager.instruction)
        self.assertNotIn("DesignCanvas", manager.instruction)

    def test_page_code_generation_prompt_is_content_first_not_design_canvas(self) -> None:
        prompt = build_page_code_generation_prompt("做一篇小红书长图，主题是 AI 写作。")

        self.assertIn("Creative Claw Page Artifact Mode", prompt)
        self.assertIn("content-first HTML page", prompt)
        self.assertIn("公众号", prompt)
        self.assertIn("long-image style work", prompt)
        self.assertIn("finished publishable poster", prompt)
        self.assertIn("multi-option review board", prompt)
        self.assertIn("target artboard", prompt)
        self.assertIn("type hierarchy", prompt)
        self.assertIn("做一篇小红书长图", prompt)
        self.assertNotIn("DesignCanvas", prompt)

    def test_page_code_generation_constraints_are_simple_page_constraints(self) -> None:
        constraints = build_page_code_generation_constraints(["fit 4:5 poster preview"])

        self.assertIn("Generate exactly one standalone HTML file.", constraints)
        self.assertIn("Optimize for content-first communication: copy, narrative sequence, images, and CTA.", constraints)
        self.assertIn("Use workspace-relative asset paths exactly as provided.", constraints)
        self.assertIn(
            "For poster requests, generate one finished publishable artboard by default, not a multi-option review board.",
            constraints,
        )
        self.assertIn(
            "For poster artboards, use clear safe margins, type hierarchy, one hero subject, and an explicit CTA or memory point.",
            constraints,
        )
        self.assertIn("fit 4:5 poster preview", constraints)
        self.assertNotIn("Embed a DesignCanvas/DCViewport-style scaffold for the main design surface.", constraints)

    def test_private_product_page_skill_registry_lists_poster_page_designer(self) -> None:
        registry = ProductPageSkillRegistry()

        skills = registry.list_skills()
        skill_names = {skill.name for skill in skills}
        content = registry.read_skill("poster-page-designer")

        self.assertIn("poster-page-designer", skill_names)
        self.assertIn("先做草稿", content)
        self.assertIn("图像期望", content)
        self.assertIn("对 poster 来说，排版就是产品", content)
        self.assertIn("成品交付模式", content)
        self.assertIn("多方案评审画布", content)
        self.assertIn("Value Proposition", content)
        self.assertIn("版式和精致度要求", content)
        self.assertIn("负面清单", content)
        self.assertIn("对小红书、公众号文章、长图和视觉文章来说，内容结构就是产品", content)
        self.assertIn("Poster 草稿示例", content)
        self.assertIn("文章 / 长图草稿示例", content)
        self.assertIn("SearchAgent", content)
        self.assertIn("ImageGenerationAgent", content)
        self.assertIn('provider="nano_banana"', content)
        self.assertIn("ImageUnderstandingAgent", content)
        self.assertIn("AnythingToMD", content)
        self.assertIn("invoke_page_code_generation", content)
        self.assertIn("不要使用本机绝对路径", content)
        self.assertNotIn("list_product_page_skills", content)
        self.assertNotIn("read_product_page_skill", content)

    def test_global_skill_registry_does_not_expose_private_product_page_skills(self) -> None:
        global_registry = SkillRegistry()

        skill_names = {skill.name for skill in global_registry.list_skills()}

        self.assertNotIn("poster-page-designer", skill_names)
        self.assertNotIn("product-page-skills", skill_names)

    def test_private_page_skill_tools_list_and_read_skills(self) -> None:
        manager = PageProductManager()
        tool_context = SimpleNamespace(state={})

        listed = manager.list_product_page_skills(tool_context)
        read = manager.read_product_page_skill("poster-page-designer", tool_context)

        self.assertEqual(listed["status"], "success")
        self.assertGreaterEqual(listed["count"], 1)
        self.assertEqual(read["status"], "success")
        self.assertEqual(read["name"], "poster-page-designer")
        self.assertIn("Poster/Page Designer", read["content"])
        self.assertEqual(tool_context.state["active_product_page_skill"]["name"], "poster-page-designer")

    def test_private_page_expert_tools_list_allowlist_and_reject_other_experts(self) -> None:
        manager = PageProductManager()
        tool_context = SimpleNamespace(state={})

        listed = manager.list_page_experts(tool_context)
        rejected = asyncio.run(
            manager.invoke_page_expert(
                agent_name="VideoGenerationAgent",
                prompt="{}",
                tool_context=tool_context,
            )
        )

        self.assertEqual(listed["status"], "success")
        self.assertEqual(
            [expert["name"] for expert in listed["experts"]],
            list(PAGE_PRODUCT_EXPERT_ALLOWLIST),
        )
        self.assertEqual(rejected["status"], "error")
        self.assertIn("Allowed experts", rejected["message"])

    def test_invoke_page_expert_uses_shared_dispatcher(self) -> None:
        manager = PageProductManager()
        manager._expert_agents = {"CodeGenerationExpert": object()}
        tool_context = SimpleNamespace(state={})
        expected_tool_result = {
            "agent_name": "CodeGenerationExpert",
            "status": "success",
            "message": "generated",
            "output_files": [{"path": "generated/page.html"}],
        }
        mocked_dispatch = AsyncMock(
            return_value=SimpleNamespace(tool_result=expected_tool_result)
        )

        with patch(
            "src.productions.page.page_product_manager.page_product_manager.dispatch_expert_call",
            new=mocked_dispatch,
        ):
            result = asyncio.run(
                manager.invoke_page_expert(
                    agent_name="CodeGenerationExpert",
                    prompt='{"prompt":"Build a long image page","language":"html"}',
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result, expected_tool_result)
        mocked_dispatch.assert_awaited_once()
        self.assertEqual(
            tool_context.state["page_product_last_expert_result"],
            expected_tool_result,
        )
        self.assertEqual(tool_context.state["page_product_generation"], expected_tool_result)

    def test_invoke_page_code_generation_uses_private_agent(self) -> None:
        manager = PageProductManager()
        tool_context = SimpleNamespace(
            state={
                "sid": "page-private-codegen-test",
                "turn_index": 1,
                "step": 2,
                "expert_step": 0,
            }
        )
        expected_output = {
            "status": "success",
            "message": "Generated html code at generated/page.html.",
            "output_path": "generated/page.html",
            "output_files": [
                {
                    "path": "generated/page.html",
                    "description": "Page artifact generated by PageCodeGenerationAgent.",
                    "source": "page_code_generation_agent",
                }
            ],
            "language": "html",
            "error_type": "",
            "retryable": False,
            "raw_error_summary": "",
            "warnings": [],
        }
        mocked_generation = AsyncMock(return_value=expected_output)

        with patch.object(PageCodeGenerationAgent, "run_generation", new=mocked_generation):
            result = asyncio.run(
                manager.invoke_page_code_generation(
                    prompt="Build a content-first HTML poster.",
                    output_path="generated/page.html",
                    context_files=["skills/product-page-skills/poster-page-designer/SKILL.md"],
                    constraints=["single long image style page"],
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result["status"], "success")
        mocked_generation.assert_awaited_once()
        self.assertEqual(
            tool_context.state["page_product_last_code_generation_result"]["status"],
            "success",
        )
        self.assertEqual(tool_context.state["page_product_generation"]["language"], "html")
        self.assertEqual(tool_context.state["new_files"][0]["source"], "page_code_generation_agent")

    def test_progress_save_validate_and_register_delivery_tools(self) -> None:
        manager = PageProductManager()
        tool_context = SimpleNamespace(
            state={
                "sid": "page-product-manager-test",
                "turn_index": 1,
                "step": 0,
                "expert_step": 0,
            }
        )

        progress = manager.emit_page_progress(
            stage="content_draft",
            status="started",
            message="Writing page content draft.",
            tool_context=tool_context,
        )
        draft = manager.save_page_artifact(
            file_name="draft.md",
            content="# Page Draft\n\n## Copy Draft\nHello",
            description="Test page draft.",
            tool_context=tool_context,
        )
        saved = manager.save_page_artifact(
            file_name="page.html",
            content="""<!doctype html>
<html lang="en">
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>main { max-width: 720px; margin: auto; }</style>
</head>
<body>
  <main>
    <h1>Content First Page</h1>
    <p>This generated page has enough visible text for validation.</p>
  </main>
</body>
</html>""",
            description="Test page artifact.",
            tool_context=tool_context,
        )
        output_path = saved["output_path"]
        validation = manager.validate_page_artifact(
            paths=[output_path],
            tool_context=tool_context,
        )
        delivery = manager.register_page_delivery(
            status="success",
            reply_text="页面产物已完成。",
            final_file_paths=[output_path],
            supporting_file_paths=[draft["output_path"]],
            tool_context=tool_context,
        )

        self.assertEqual(progress["status"], "success")
        self.assertEqual(saved["status"], "success")
        self.assertTrue(resolve_workspace_path(output_path).exists())
        self.assertEqual(validation["status"], "success")
        self.assertEqual(delivery["result_schema_version"], PAGE_PRODUCT_RESULT_SCHEMA_VERSION)
        self.assertEqual(delivery["status"], "success")
        self.assertEqual(delivery["product_line"], "page")
        self.assertEqual(delivery["final_file_paths"], [output_path])
        self.assertEqual(delivery["supporting_file_paths"], [draft["output_path"]])
        self.assertEqual(tool_context.state["final_file_paths"], [output_path])
        self.assertEqual(tool_context.state["page_product_result"]["message"], "页面产物已完成。")

    def test_validate_page_artifact_rejects_non_html_final_file(self) -> None:
        manager = PageProductManager()
        tool_context = SimpleNamespace(
            state={
                "sid": "page-validation-test",
                "turn_index": 1,
                "step": 0,
                "expert_step": 0,
            }
        )
        saved = manager.save_page_artifact(
            file_name="draft.md",
            content="# Draft",
            description="Draft file.",
            tool_context=tool_context,
        )

        validation = manager.validate_page_artifact(paths=[saved["output_path"]], tool_context=tool_context)

        self.assertEqual(validation["status"], "error")
        self.assertIn("must be an HTML file", validation["validations"][0]["errors"][0])

    def test_run_product_request_requires_adk_invocation_context(self) -> None:
        manager = PageProductManager()
        tool_context = SimpleNamespace(state={})

        result = asyncio.run(
            manager.run_product_request(
                task="做一个小红书长图。",
                tool_context=tool_context,
            )
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("ADK invocation context", result["message"])


if __name__ == "__main__":
    unittest.main()
