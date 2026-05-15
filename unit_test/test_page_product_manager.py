import asyncio
import json
import os
import unittest
from pathlib import Path
from typing import Any
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.adk.agents import LlmAgent
from pydantic import PrivateAttr

from src.agents.experts.base import CreativeExpert
from src.productions.page.page_product_manager import (
    PAGE_PRODUCT_DRAFT_STATE_KEY,
    PAGE_PRODUCT_EXPERT_ALLOWLIST,
    PAGE_PRODUCT_FINAL_DRAFT_STATE_KEY,
    PAGE_PRODUCT_HTML_GENERATION_STATE_KEY,
    PAGE_PRODUCT_MATERIALS_STATE_KEY,
    PAGE_PRODUCT_RESULT_SCHEMA_VERSION,
    PAGE_PRODUCT_TEMPLATE_SELECTION_STATE_KEY,
    PAGE_TEMPLATES,
    TEMPLATES_HTML_DIR,
    PageCodeGenerationAgent,
    PageProductManager,
    ProductPageSkillRegistry,
    build_page_code_generation_constraints,
    build_page_code_generation_prompt,
    list_page_templates,
    load_page_templates_from_directory,
    select_page_template_match,
)
from src.productions.page.page_product_manager import page_artifact_visual_validation
from src.runtime.workspace import build_generated_output_path, resolve_workspace_path, workspace_relative_path
from src.skills.registry import SkillRegistry


class _FakeImageGenerationAgent(CreativeExpert):
    """Test double that records image-generation parameters and returns one image file."""

    _calls: list[dict[str, Any]] = PrivateAttr(default_factory=list)

    def __init__(self) -> None:
        """Initialize the fake with the real runtime agent name."""
        super().__init__(name="ImageGenerationAgent")

    @property
    def calls(self) -> list[dict[str, Any]]:
        """Return recorded image-generation calls."""
        return self._calls

    async def _run_async_impl(self, ctx):
        """Emit a successful fake image-generation event."""
        parameters = dict(ctx.session.state.get("current_parameters") or {})
        self._calls.append(parameters)
        image_path = workspace_relative_path(
            build_generated_output_path(
                session_id=ctx.session.id,
                turn_index=int(ctx.session.state.get("turn_index", 0) or 0),
                step=int(ctx.session.state.get("step", 0) or 0),
                output_type="generation",
                index=0,
                extension=".png",
            )
        )
        current_output = {
            "status": "success",
            "message": "Generated fake page image.",
            "output_files": [
                {
                    "path": image_path,
                    "description": "Fake generated page hero image.",
                    "source": "image_generation",
                }
            ],
        }
        yield self.format_event("Generated fake page image.", {"current_output": current_output})


class PageProductManagerTests(unittest.TestCase):
    def test_page_product_manager_is_llm_agent_with_private_tools(self) -> None:
        manager = PageProductManager()

        self.assertIsInstance(manager, LlmAgent)
        self.assertEqual(manager.name, "PageProductManager")
        self.assertEqual(manager.include_contents, "none")
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
        self.assertIn("built-in tagged templates", manager.instruction)
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
        self.assertIn("must start with `<!DOCTYPE html>` and end with `</html>`", prompt)
        self.assertIn("Do not assume external network access", prompt)
        self.assertIn("If the brief lists a non-empty `html_relative_src`", prompt)
        self.assertIn("`file://` images are blocked", prompt)
        self.assertIn("structured data such as CSV, JSON, or tables", prompt)
        self.assertIn("Visual Quality Defaults", prompt)
        self.assertIn("Template-specific or skill-specific style rules override these defaults.", prompt)
        self.assertIn("Selected Built-In Page Template", prompt)
        self.assertIn("Template ID: xhs_knowledge_long_image", prompt)
        self.assertIn("Use a 1080 px wide vertical artboard", prompt)
        self.assertIn("做一篇小红书长图", prompt)
        self.assertNotIn("DesignCanvas", prompt)

    def test_page_code_generation_constraints_are_simple_page_constraints(self) -> None:
        constraints = build_page_code_generation_constraints(["fit 4:5 poster preview"])

        self.assertIn("Generate exactly one standalone HTML file.", constraints)
        self.assertIn("The HTML document must start with <!DOCTYPE html> and end with </html>.", constraints)
        self.assertIn("Optimize for content-first communication: copy, narrative sequence, images, and CTA.", constraints)
        self.assertIn(
            "Do not invent fake data, testimonials, legal claims, prices, metrics, or unsupported performance numbers.",
            constraints,
        )
        self.assertIn("Use non-empty html_relative_src material paths exactly when provided.", constraints)
        self.assertIn("Do not use file:// URLs when html_relative_src is available.", constraints)
        self.assertIn("Do not invent local absolute paths or file:// URLs.", constraints)
        self.assertIn(
            "For poster requests, generate one finished publishable artboard by default, not a multi-option review board.",
            constraints,
        )
        self.assertIn(
            "For poster artboards, use clear safe margins, type hierarchy, one hero subject, and an explicit CTA or memory point.",
            constraints,
        )
        self.assertIn("Do not rely on external network assets or CDN resources unless explicitly approved.", constraints)
        self.assertIn(
            "Use the selected built-in Page template as structural and visual guidance unless explicit user requirements override it.",
            constraints,
        )
        self.assertIn(
            "Treat visual quality rules as defaults that can be overridden by a selected skill or explicit user style brief.",
            constraints,
        )
        self.assertIn("fit 4:5 poster preview", constraints)
        self.assertNotIn("Embed a DesignCanvas/DCViewport-style scaffold for the main design surface.", constraints)

    def test_page_templates_are_tagged_and_typical(self) -> None:
        template_ids = {template.id for template in PAGE_TEMPLATES}
        summaries = list_page_templates()

        self.assertEqual(len(PAGE_TEMPLATES), 8)
        self.assertEqual(len(summaries), 8)
        self.assertTrue(TEMPLATES_HTML_DIR.exists())
        self.assertEqual(
            template_ids,
            {
                "xhs_knowledge_long_image",
                "wechat_editorial_article",
                "poster_product_launch",
                "saas_landing_onepager",
                "visual_report_brief",
                "quote_social_card",
                "event_agenda_page",
                "product_detail_story",
            },
        )
        for template in PAGE_TEMPLATES:
            self.assertIn("templates-html", template.source_dir)
            self.assertIn("<!DOCTYPE html>", template.template_html)
            self.assertGreaterEqual(len(template.tags), 4)
            self.assertGreaterEqual(len(template.layout_rules), 3)
            self.assertGreaterEqual(len(template.style_rules), 3)
            self.assertGreaterEqual(len(template.content_rules), 3)
            self.assertGreaterEqual(len(template.quality_checks), 3)

    def test_page_templates_load_from_templates_html_directory(self) -> None:
        templates = load_page_templates_from_directory(TEMPLATES_HTML_DIR)

        self.assertEqual(len(templates), 8)
        for template in templates:
            template_dir = TEMPLATES_HTML_DIR / template.id

            self.assertTrue((template_dir / "metadata.json").exists())
            self.assertTrue((template_dir / "template.html").exists())
            self.assertEqual(template.source_dir, str(template_dir))

    def test_page_template_selector_matches_typical_briefs(self) -> None:
        cases = {
            "做一张产品发布海报，强调新品首发和预约 CTA。": "poster_product_launch",
            "根据这份 CSV 数据生成一页视觉报告，突出关键洞察。": "visual_report_brief",
            "做一个 SaaS 官网 landing page，包含 value proposition 和 waitlist。": "saas_landing_onepager",
            "做一张朋友圈金句社交卡，文案是保持长期主义。": "quote_social_card",
            "生成活动议程页，包含大会日程、嘉宾和报名信息。": "event_agenda_page",
            "做一个商品详情页，突出材质、规格和使用场景。": "product_detail_story",
            "写一篇微信公众号视觉文章，主题是 AI 写作方法。": "wechat_editorial_article",
            "做一篇小红书知识长图，主题是 AI 写作。": "xhs_knowledge_long_image",
        }

        for brief, expected_template_id in cases.items():
            with self.subTest(brief=brief):
                match = select_page_template_match(brief)

                self.assertEqual(match.template.id, expected_template_id)
                self.assertGreater(match.score, 0)

    def test_page_code_generation_prompt_can_use_explicit_template_id(self) -> None:
        prompt = build_page_code_generation_prompt(
            "做一个新品营销页面。",
            template_id="quote_social_card",
        )

        self.assertIn("Template ID: quote_social_card", prompt)
        self.assertIn("Make the quote the main composition", prompt)

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
                    template_id="poster_product_launch",
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result["status"], "success")
        mocked_generation.assert_awaited_once()
        self.assertEqual(mocked_generation.await_args.kwargs["template_id"], "poster_product_launch")
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
            browser_preview=False,
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
        self.assertEqual(validation["validations"][0]["checks"]["browser_preview_checked"], False)
        self.assertEqual(validation["validations"][0]["visual_validation"]["status"], "success")
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

        validation = manager.validate_page_artifact(
            paths=[saved["output_path"]],
            tool_context=tool_context,
            browser_preview=False,
        )

        self.assertEqual(validation["status"], "error")
        self.assertIn("must be an HTML file", validation["validations"][0]["errors"][0])

    def test_validate_page_artifact_warns_on_review_board_signals(self) -> None:
        manager = PageProductManager()
        tool_context = SimpleNamespace(
            state={
                "sid": "page-review-board-validation-test",
                "turn_index": 1,
                "step": 0,
                "expert_step": 0,
            }
        )
        saved = manager.save_page_artifact(
            file_name="review-board.html",
            content="""<!doctype html>
<html lang="zh-CN">
<body>
  <main>
    <h1>母亲节花店海报</h1>
    <section>方案一：粉色花束方向</section>
    <section>方案二：高级品牌方向</section>
  </main>
</body>
</html>""",
            description="Review-board style page.",
            tool_context=tool_context,
        )

        validation = manager.validate_page_artifact(
            paths=[saved["output_path"]],
            tool_context=tool_context,
            browser_preview=False,
        )

        self.assertEqual(validation["status"], "success")
        self.assertEqual(validation["validations"][0]["status"], "warning")
        self.assertIn("review-board", validation["validations"][0]["warnings"][0])
        self.assertFalse(validation["validations"][0]["checks"]["visual_no_review_board_signals"])

    def test_validate_page_artifact_includes_browser_visual_warnings(self) -> None:
        manager = PageProductManager()
        tool_context = SimpleNamespace(
            state={
                "sid": "page-browser-validation-test",
                "turn_index": 1,
                "step": 0,
                "expert_step": 0,
            }
        )
        saved = manager.save_page_artifact(
            file_name="page.html",
            content="""<!doctype html>
<html lang="en">
<body>
  <main>
    <h1>Launch Poster</h1>
    <p>Clear CTA and supporting copy.</p>
  </main>
</body>
</html>""",
            description="Page artifact.",
            tool_context=tool_context,
        )
        fake_browser_result = {
            "checks": {
                "browser_preview_available": True,
                "browser_preview_checked": True,
                "browser_poster_no_text_overflow": False,
            },
            "errors": [],
            "warnings": ["visual validation: poster viewport has text overflow"],
        }

        with patch(
            "src.productions.page.page_product_manager.page_artifact_visual_validation._run_node_playwright_page_preview",
            return_value=fake_browser_result,
        ):
            validation = manager.validate_page_artifact(
                paths=[saved["output_path"]],
                tool_context=tool_context,
                browser_preview=True,
            )

        self.assertEqual(validation["status"], "success")
        self.assertEqual(validation["validations"][0]["status"], "warning")
        self.assertFalse(validation["validations"][0]["checks"]["browser_poster_no_text_overflow"])
        self.assertIn("text overflow", validation["validations"][0]["warnings"][0])

    def test_page_browser_preview_serializes_slotted_viewports(self) -> None:
        captured_args: list[str] = []

        def _fake_subprocess_run(args, **_kwargs):
            captured_args.extend(args)
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ok": True, "results": []}),
                stderr="",
            )

        with (
            patch.object(page_artifact_visual_validation.shutil, "which", return_value="/usr/local/bin/node"),
            patch.object(page_artifact_visual_validation.Path, "exists", return_value=True),
            patch.object(
                page_artifact_visual_validation.subprocess,
                "run",
                side_effect=_fake_subprocess_run,
            ),
        ):
            result = page_artifact_visual_validation._run_node_playwright_page_preview(
                Path("/tmp/page.html"),
                (
                    page_artifact_visual_validation.PageVisualViewport(
                        name="tablet",
                        width=768,
                        height=1024,
                    ),
                ),
            )

        self.assertTrue(result["checks"]["browser_preview_available"])
        self.assertEqual(json.loads(captured_args[4]), [{"name": "tablet", "width": 768, "height": 1024}])

    def test_page_browser_preview_keeps_node_diagnostics_outside_page_context(self) -> None:
        script = page_artifact_visual_validation._node_playwright_script()

        self.assertIn("metrics.consoleErrors = unique(consoleErrors).slice(0, 3);", script)
        self.assertIn("metrics.pageErrors = unique(pageErrors).slice(0, 3);", script)
        self.assertNotIn("consoleErrors: unique(consoleErrors)", script)
        self.assertNotIn("pageErrors: unique(pageErrors)", script)

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

    def test_run_product_request_uses_sequential_pipeline_and_registers_delivery(self) -> None:
        manager = PageProductManager()
        tool_context = SimpleNamespace(
            state={
                "sid": "page-sequential-pipeline-test",
                "turn_index": 1,
                "step": 0,
                "expert_step": 0,
            },
            _invocation_context=SimpleNamespace(user_id="user-page-pipeline"),
        )
        generation_output = {
            "status": "success",
            "message": "Generated html code at generated/page-pipeline.html.",
            "output_path": "generated/page-pipeline.html",
            "output_files": [
                {
                    "path": "generated/page-pipeline.html",
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
        mocked_generation = AsyncMock(return_value=generation_output)

        with (
            patch.object(PageCodeGenerationAgent, "run_generation", new=mocked_generation),
            patch(
                "src.productions.page.page_product_manager.page_product_manager._validate_one_page_artifact",
                side_effect=AssertionError("Page pipeline delivery should skip visual validation."),
            ) as mocked_validation,
        ):
            result = asyncio.run(
                manager.run_product_request(
                    task="根据 CSV 数据做一个数据周报网页。",
                    output={"template_id": "visual_report_brief"},
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["product_line"], "page")
        self.assertEqual(result["final_file_paths"], ["generated/page-pipeline.html"])
        self.assertEqual(tool_context.state["final_file_paths"], ["generated/page-pipeline.html"])
        self.assertEqual(
            tool_context.state[PAGE_PRODUCT_TEMPLATE_SELECTION_STATE_KEY]["template_id"],
            "visual_report_brief",
        )
        self.assertEqual(tool_context.state[PAGE_PRODUCT_DRAFT_STATE_KEY]["status"], "success")
        self.assertEqual(tool_context.state[PAGE_PRODUCT_MATERIALS_STATE_KEY]["status"], "success")
        self.assertEqual(tool_context.state[PAGE_PRODUCT_FINAL_DRAFT_STATE_KEY]["status"], "success")
        self.assertEqual(tool_context.state[PAGE_PRODUCT_HTML_GENERATION_STATE_KEY]["status"], "success")
        self.assertEqual(tool_context.state["page_product_result"], result)
        progress_stages = [
            item["stage"]
            for item in tool_context.state["page_product_progress"]
        ]
        self.assertEqual(
            progress_stages,
            [
                "template_selection",
                "content_draft",
                "material_preparation",
                "final_draft",
                "html_generation",
                "finalizing",
            ],
        )
        mocked_generation.assert_awaited_once()
        mocked_validation.assert_not_called()
        self.assertIn("Final Page HTML Generation Brief", mocked_generation.await_args.kwargs["prompt"])
        self.assertEqual(mocked_generation.await_args.kwargs["template_id"], "visual_report_brief")

    def test_run_product_request_generates_visual_materials_before_html(self) -> None:
        manager = PageProductManager()
        image_agent = _FakeImageGenerationAgent()
        tool_context = SimpleNamespace(
            state={
                "sid": "page-image-material-test",
                "turn_index": 1,
                "step": 0,
                "expert_step": 0,
            },
            _invocation_context=SimpleNamespace(user_id="user-page-image-material"),
        )
        generation_output = {
            "status": "success",
            "message": "Generated html code at generated/page-with-image.html.",
            "output_path": "generated/page-with-image.html",
            "output_files": [
                {
                    "path": "generated/page-with-image.html",
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
        mocked_generation = AsyncMock(return_value=generation_output)

        with (
            patch.object(PageCodeGenerationAgent, "run_generation", new=mocked_generation),
            patch(
                "src.productions.page.page_product_manager.page_product_manager._validate_one_page_artifact",
                side_effect=AssertionError("Page pipeline delivery should skip visual validation."),
            ) as mocked_validation,
        ):
            result = asyncio.run(
                manager.run_product_request(
                    task="做一张产品发布海报，需要主视觉插图。",
                    output={
                        "template_id": "poster_product_launch",
                        "image_assets": [
                            {
                                "asset_id": "launch_hero",
                                "prompt": "A premium abstract product launch hero image without text.",
                                "aspect_ratio": "4:5",
                            }
                        ],
                    },
                    tool_context=tool_context,
                    expert_agents={"ImageGenerationAgent": image_agent},
                )
            )

        self.assertEqual(result["status"], "success")
        mocked_validation.assert_not_called()
        self.assertEqual(image_agent.calls[0]["prompt"], "A premium abstract product launch hero image without text.")
        self.assertEqual(image_agent.calls[0]["aspect_ratio"], "4:5")
        materials = tool_context.state[PAGE_PRODUCT_MATERIALS_STATE_KEY]
        self.assertEqual(materials["generated_assets"][0]["asset_id"], "launch_hero")
        generated_image_path = materials["generated_assets"][0]["path"]
        self.assertIn("turn1_step0_generation_output0.png", generated_image_path)
        self.assertEqual(materials["generated_assets"][0]["status"], "ready")
        self.assertIn(generated_image_path, result["supporting_file_paths"])
        final_prompt = mocked_generation.await_args.kwargs["prompt"]
        html_output_path = mocked_generation.await_args.kwargs["output_path"]
        expected_relative_src = Path(
            os.path.relpath(
                resolve_workspace_path(generated_image_path),
                start=resolve_workspace_path(html_output_path).parent,
            )
        ).as_posix()
        expected_file_src = resolve_workspace_path(generated_image_path).as_uri()
        self.assertIn("Generated image `launch_hero`", final_prompt)
        self.assertIn(f"workspace_path={generated_image_path}", final_prompt)
        self.assertIn(f"html_relative_src={expected_relative_src}", final_prompt)
        self.assertIn(f"absolute_file_src={expected_file_src}", final_prompt)
        self.assertIn(f"Final HTML output path: {html_output_path}", final_prompt)
        self.assertIn("Use non-empty `html_relative_src` values exactly", final_prompt)
        self.assertIn("do not use them when `html_relative_src` is present", final_prompt)
        self.assertIn("do not use them as browser image sources", final_prompt)
        self.assertIn("Do not invent image paths", final_prompt)


if __name__ == "__main__":
    unittest.main()
