import unittest
from types import SimpleNamespace

from google.adk.agents import LlmAgent
from pptx import Presentation

from src.productions.ppt.ppt_product_manager import PptProductManager
from src.productions.ppt.schemas import DeckContentPlan, DeckPagePlan
from src.runtime.workspace import (
    build_workspace_file_record,
    resolve_workspace_path,
    workspace_relative_path,
    workspace_root,
)


def _write_markdown_source(name: str, text: str) -> str:
    source_dir = workspace_root() / "inbox" / "ppt_product_manager_tests"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / name
    source_path.write_text(text, encoding="utf-8")
    return workspace_relative_path(source_path)


def _page(slide_number: int, page_type: str) -> DeckPagePlan:
    return DeckPagePlan(
        slide_number=slide_number,
        page_type=page_type,
        title=f"Slide {slide_number}",
        purpose="Explain the planned message.",
        key_takeaway="Audience remembers the core point.",
        asset_intent="Use a simple supporting visual.",
    )


async def _fake_source_converter(source_input, parameters: dict) -> dict:
    output_path = str(parameters["output_path"])
    output_file = resolve_workspace_path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    asset_dir = output_file.parent / "figures"
    asset_dir.mkdir(parents=True, exist_ok=True)
    chart_path = asset_dir / "activation.png"
    chart_path.write_bytes(b"fake-png")
    markdown = "# Growth Launch\n\n![Activation chart](figures/activation.png)\n"
    output_file.write_text(markdown, encoding="utf-8")
    return {
        "status": "success",
        "message": "converted",
        "output_text": markdown,
        "results": {
            "method": "test:markdown",
            "output_path": output_path,
        },
        "output_files": [
            build_workspace_file_record(
                output_file,
                description="Converted Markdown source.",
                source="expert",
                name=output_file.name,
            )
        ],
    }


class PptProductManagerTests(unittest.IsolatedAsyncioTestCase):
    def test_instruction_prioritizes_pptx_and_adk_workflow(self) -> None:
        manager = PptProductManager()

        instruction = manager.build_instruction()

        self.assertIsInstance(manager, LlmAgent)
        self.assertIs(manager.build_agent(), manager)
        self.assertEqual([tool.__name__ for tool in manager.tools], ["dispatch_ppt_route"])
        self.assertIn("PPT and PowerPoint production", instruction)
        self.assertIn("ADK workflow", instruction)
        self.assertIn("HTML route first", instruction)
        self.assertIn("Do not claim PPTX generation succeeded", instruction)

    def test_route_registry_registers_all_routes(self) -> None:
        manager = PptProductManager()

        routes = manager.list_registered_routes()

        self.assertEqual(set(routes), {"html", "svg", "xml"})
        self.assertTrue(routes["html"]["implemented"])
        self.assertFalse(routes["svg"]["implemented"])
        self.assertFalse(routes["xml"]["implemented"])

    async def test_dispatch_ppt_route_tool_uses_state_registry(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="做一个 5 页 PPTX 产品介绍。",
            inputs=[],
            output={"format": "pptx"},
        )
        content_plan = manager.build_initial_deck_content_plan(requirement)
        tool_context = SimpleNamespace(
            state={
                "sid": "ppt-dispatch-tool-test",
                "turn_index": 1,
                "step": 1,
                "ppt_confirmed_requirement": requirement.model_dump(mode="json"),
                "ppt_deck_content_plan": content_plan.model_dump(mode="json"),
            }
        )

        result = await manager.dispatch_ppt_route(route="html", tool_context=tool_context)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["selected_route"], "html")
        self.assertEqual(tool_context.state["ppt_route_build"]["template"]["template_id"], "clean_business")
        self.assertTrue(result["output_files"])

    def test_prepare_confirmed_requirement_defaults_to_html_mvp_for_pptx(self) -> None:
        manager = PptProductManager()

        requirement = manager.prepare_confirmed_requirement(
            task="做一个 6 页 PPTX，用于产品发布会。",
            inputs=[{"name": "brief.md", "path": "inbox/demo/brief.md"}],
            output={"format": "pptx"},
        )

        self.assertEqual(requirement.route, "html")
        self.assertEqual(requirement.output_format, "pptx")
        self.assertEqual(requirement.slide_count_policy.target, 6)
        self.assertEqual(requirement.slide_count_policy.source, "user")
        self.assertEqual(requirement.language, "zh-CN")
        self.assertEqual(requirement.source_understanding.document_type, "markdown")
        self.assertEqual(requirement.template_requirement.template_source, "system")
        self.assertEqual(requirement.editability_requirement.level, "high")
        self.assertFalse(requirement.confirmed_by_user)

    def test_prepare_confirmed_requirement_honors_explicit_route(self) -> None:
        manager = PptProductManager()

        requirement = manager.prepare_confirmed_requirement(
            task="套用用户上传 PPTX 模板生成汇报。",
            inputs=[{"name": "template.pptx", "path": "inbox/demo/template.pptx"}],
            output={"route": "xml"},
        )

        self.assertEqual(requirement.route, "xml")
        self.assertTrue(requirement.confirmed_by_user)
        self.assertTrue(requirement.template_requirement.use_template)
        self.assertEqual(requirement.template_requirement.template_source, "user")
        self.assertEqual(requirement.editability_requirement.level, "native")

    async def test_run_generates_html_route_outputs_and_writes_state(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-manager-test", "turn_index": 1, "step": 1})

        result = await manager.run_product_request(
            task="生成一个 PPTX 产品介绍。",
            inputs=[],
            output={"format": "pptx"},
            tool_context=tool_context,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result_schema_version"], "ppt-product-result-v1")
        self.assertEqual(result["product_line"], "ppt")
        self.assertEqual(result["selected_route"], "html")
        self.assertIn("ppt_confirmed_requirement", tool_context.state)
        self.assertIn("ppt_deck_content_plan", tool_context.state)
        self.assertIn("ppt_route_build", tool_context.state)
        self.assertEqual(tool_context.state["product_line"], "ppt")
        self.assertEqual(tool_context.state["ppt_product_result"]["status"], "success")
        self.assertEqual(len(result["output_files"]), len(result["delivery_manifest"]["output_files"]))
        self.assertTrue(result["delivery_manifest"]["final_pptx"].endswith(".pptx"))
        self.assertEqual(tool_context.state["final_file_paths"], [result["delivery_manifest"]["final_pptx"]])

        pptx_path = resolve_workspace_path(result["delivery_manifest"]["final_pptx"])
        html_path = resolve_workspace_path(result["delivery_manifest"]["intermediate_artifacts"][0])
        self.assertTrue(pptx_path.exists())
        self.assertTrue(html_path.exists())
        self.assertGreater(len(result["delivery_manifest"]["previews"]), 0)
        self.assertEqual(len(Presentation(str(pptx_path)).slides), len(result["deck_content_plan"]["pages"]))

    async def test_run_returns_deferred_status_for_unimplemented_xml_route(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-manager-test", "turn_index": 1, "step": 1})

        result = await manager.run_product_request(
            task="套用用户上传 PPTX 模板生成汇报。",
            inputs=[{"name": "template.pptx", "path": "inbox/demo/template.pptx"}],
            output={"route": "xml"},
            tool_context=tool_context,
        )

        self.assertEqual(result["status"], "route_not_implemented")
        self.assertEqual(result["selected_route"], "xml")
        self.assertEqual(result["output_files"], [])
        self.assertNotIn("final_file_paths", tool_context.state)

    async def test_run_records_source_materials_and_resets_current_output(self) -> None:
        source_path = _write_markdown_source(
            "launch_brief.md",
            """# Growth Launch

## Customer Proof
- Activation rose after guided onboarding.
- Enterprise pipeline needs proof-led messaging.
""",
        )
        manager = PptProductManager()
        tool_context = SimpleNamespace(
            state={
                "sid": "ppt-manager-source-test",
                "turn_index": 2,
                "step": 1,
                "current_output": {"status": "success", "message": "stale expert output"},
            }
        )

        result = await manager.run_product_request(
            task="基于材料生成 6 页 PPTX，用于增长发布会。",
            inputs=[{"name": "launch_brief.md", "path": source_path}],
            output={"format": "pptx"},
            tool_context=tool_context,
            source_converter=_fake_source_converter,
        )

        self.assertEqual(result["status"], "success")
        source_materials = result["confirmed_requirement"]["source_understanding"]
        self.assertEqual(source_materials["markdown_sources"][0]["name"], "launch_brief.md")
        self.assertEqual(source_materials["figures"][0]["alt"], "Activation chart")
        plan_text = str(result["deck_content_plan"])
        self.assertIn("converted Markdown source", plan_text)
        self.assertIn("ppt_source_markdown_sources", tool_context.state)
        self.assertIn("ppt_source_figures", tool_context.state)
        self.assertTrue(tool_context.state["ppt_source_output_files"])
        self.assertEqual(tool_context.state["current_output"]["product_line"], "ppt")
        self.assertEqual(tool_context.state["current_output"]["status"], "success")

        html_path = resolve_workspace_path(result["delivery_manifest"]["intermediate_artifacts"][0])
        html_text = html_path.read_text(encoding="utf-8")
        self.assertIn("converted Markdown source", html_text)

        pptx_path = resolve_workspace_path(result["delivery_manifest"]["final_pptx"])
        pptx_text = "\n".join(
            shape.text
            for slide in Presentation(str(pptx_path)).slides
            for shape in slide.shapes
            if getattr(shape, "has_text_frame", False)
        )
        self.assertIn("converted Markdown source", pptx_text)

    async def test_run_returns_needs_clarification_for_too_thin_request(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-manager-test", "turn_index": 1, "step": 1})

        result = await manager.run_product_request(
            task="做个 PPT",
            inputs=[],
            output={"format": "pptx"},
            tool_context=tool_context,
        )

        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["selected_route"], "html")
        self.assertIn("补充 PPT 的主题", result["next_actions"][0])
        self.assertNotIn("final_file_paths", tool_context.state)

    def test_deck_content_plan_requires_standard_page_types(self) -> None:
        plan = DeckContentPlan(
            title="Demo deck",
            core_narrative="A complete five-part narrative.",
            pages=[
                _page(1, "cover"),
                _page(2, "toc"),
                _page(3, "chapter_start"),
                _page(4, "chapter_content"),
                _page(5, "ending"),
            ],
        )

        self.assertEqual(len(plan.pages), 5)

        with self.assertRaisesRegex(ValueError, "missing required page types"):
            DeckContentPlan(
                title="Broken deck",
                core_narrative="Missing ending.",
                pages=[
                    _page(1, "cover"),
                    _page(2, "toc"),
                    _page(3, "chapter_start"),
                    _page(4, "chapter_content"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
