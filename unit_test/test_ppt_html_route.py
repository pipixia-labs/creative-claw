import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from src.productions.ppt.ppt_product_manager import PptProductManager
from src.productions.ppt.schemas import DeckPageAsset
from src.productions.ppt.routes.html import (
    HTML_DELIVERY_STAGE,
    HTML_PAGE_GENERATION_CONTENT_PLAN_KEY,
    HTML_PAGE_GENERATION_PAGES_KEY,
    HTML_ROUTE_STAGE_SEQUENCE,
    build_html_page_generation_agent,
    build_html_route,
    deliver_html_route_quality,
    export_html_pptx,
    generate_html_pages,
    prepare_html_route_paths,
    prepare_html_template,
    save_html_route_pages,
)
from src.productions.ppt.templates.html_registry import list_html_templates, load_html_template_package
from src.runtime.workspace import workspace_root


def _write_route_test_image(tmpdir: Path) -> str:
    image_path = tmpdir / "slide_asset.png"
    Image.new("RGB", (640, 360), "#2457D6").save(image_path)
    return str(image_path)


class PptHtmlRouteTests(unittest.TestCase):
    def test_html_template_registry_loads_default_template(self) -> None:
        templates = list_html_templates()
        template = load_html_template_package("clean_business", aspect_ratio="16:9")

        self.assertGreaterEqual(len(templates), 1)
        self.assertEqual(template.template_id, "clean_business")
        self.assertEqual(template.viewport_width, 1280)
        self.assertIn("cover", template.page_types)
        self.assertEqual(template.pptx_strategy, "native_editable")
        self.assertIn("editable", template.editability_notes)

    def test_html_template_preparation_uses_free_design_without_template(self) -> None:
        template_stage = prepare_html_template(template_id="", aspect_ratio="16:9")

        self.assertEqual(template_stage.template.template_id, "free_design")
        self.assertEqual(template_stage.template.label, "Free Design")
        self.assertIn("No system HTML template", template_stage.template.editability_notes)

    def test_html_page_generation_agent_uses_free_html_contract(self) -> None:
        agent = build_html_page_generation_agent()

        self.assertEqual(agent.name, "HtmlPageGenerationAgent")
        self.assertEqual(agent.output_key, "ppt_html_page_generation_agent_message")
        self.assertIn("Do not use a fixed template", agent.instruction)
        self.assertIn("one HTML fragment per slide", agent.instruction)
        self.assertEqual({tool.__name__ for tool in agent.tools}, {"save_html_route_pages"})

    def test_save_html_route_pages_accepts_per_slide_html_fragments(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="给我做一个ppt，用来给幼儿园小朋友讲英语单词。图文并茂。小于10页。",
            inputs=[],
            output={"format": "pptx"},
        )
        content_plan = manager.build_initial_deck_content_plan(requirement)
        tool_context = SimpleNamespace(
            state={
                HTML_PAGE_GENERATION_CONTENT_PLAN_KEY: content_plan.model_dump(mode="json"),
            }
        )
        pages = [
            {
                "slide_number": page.slide_number,
                "html": (
                    f"<section><div style='font-size:72px'>{page.title}</div>"
                    f"<p>{page.key_takeaway}</p></section>"
                ),
            }
            for page in content_plan.pages
        ]

        result = save_html_route_pages(pages, tool_context)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["page_count"], len(content_plan.pages))
        saved_pages = tool_context.state[HTML_PAGE_GENERATION_PAGES_KEY]
        self.assertEqual(len(saved_pages), len(content_plan.pages))
        self.assertIn('class="slide generated-slide', saved_pages[0]["html"])
        self.assertIn('data-slide-number="01"', saved_pages[0]["html"])

    def test_html_route_builds_html_previews_and_pptx(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="做一个 5 页 PPTX，用于产品发布。",
            inputs=[],
            output={"format": "pptx"},
        )
        content_plan = manager.build_initial_deck_content_plan(requirement)

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            route_build = build_html_route(
                content_plan=content_plan,
                output_dir=Path(tmpdir),
                aspect_ratio="16:9",
            )

            pptx_path = workspace_root() / route_build.pptx_path
            html_path = workspace_root() / route_build.html_deck_path
            quality_path = workspace_root() / route_build.quality_report_path
            build_log_path = workspace_root() / route_build.build_log_path

            self.assertTrue(pptx_path.exists())
            self.assertTrue(html_path.exists())
            self.assertTrue(quality_path.exists())
            self.assertTrue(build_log_path.exists())
            self.assertEqual(len(route_build.preview_paths), len(content_plan.pages))
            self.assertEqual(route_build.warnings, [])

            prs = Presentation(str(pptx_path))
            pptx_text = "\n".join(
                shape.text
                for slide in prs.slides
                for shape in slide.shapes
                if getattr(shape, "has_text_frame", False)
            )
            picture_count = sum(
                1
                for slide in prs.slides
                for shape in slide.shapes
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE
            )
            quality_report = json.loads(quality_path.read_text(encoding="utf-8"))
            build_log = json.loads(build_log_path.read_text(encoding="utf-8"))

            self.assertEqual(len(prs.slides), len(content_plan.pages))
            self.assertIn(content_plan.pages[0].title, pptx_text)
            self.assertEqual(picture_count, 0)
            self.assertTrue(quality_report["checks"]["pptx_contains_editable_text"])
            self.assertTrue(quality_report["checks"]["pptx_titles_present"])
            self.assertEqual(quality_report["route_stages"], list(HTML_ROUTE_STAGE_SEQUENCE))
            self.assertEqual(quality_report["delivery_stage"], HTML_DELIVERY_STAGE)
            self.assertEqual(build_log["workflow_name"], "HtmlRouteSequentialAgent")
            self.assertEqual(build_log["route_stages"], list(HTML_ROUTE_STAGE_SEQUENCE))
            self.assertEqual(build_log["delivery_stage"], HTML_DELIVERY_STAGE)
            self.assertEqual(build_log["template_id"], "free_design")
            self.assertEqual(build_log["template_source"], "none")
            self.assertEqual(build_log["page_generation_mode"], "free_design")
            self.assertIn('data-deck-template="free_design"', html_path.read_text(encoding="utf-8"))

    def test_html_route_renders_ready_assets_into_html_previews_and_pptx(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="做一个 5 页 PPTX，用于产品发布。",
            inputs=[],
            output={"format": "pptx"},
        )
        content_plan = manager.build_initial_deck_content_plan(requirement)

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            asset_path = _write_route_test_image(Path(tmpdir))
            content_plan.pages[0].assets = [
                DeckPageAsset(
                    asset_id="cover_asset",
                    source_kind="user_upload",
                    status="ready",
                    path=asset_path,
                    alt="Cover visual",
                )
            ]
            route_build = build_html_route(
                content_plan=content_plan,
                output_dir=Path(tmpdir) / "route_output",
                aspect_ratio="16:9",
            )

            pptx_path = workspace_root() / route_build.pptx_path
            html_path = workspace_root() / route_build.html_deck_path
            quality_path = workspace_root() / route_build.quality_report_path
            prs = Presentation(str(pptx_path))
            picture_count = sum(
                1
                for slide in prs.slides
                for shape in slide.shapes
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE
            )
            quality_report = json.loads(quality_path.read_text(encoding="utf-8"))

            self.assertIn("<img", html_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(picture_count, 1)
            self.assertEqual(quality_report["ready_asset_count"], 1)
            self.assertTrue(quality_report["checks"]["pptx_ready_assets_rendered"])

    def test_html_route_stage_functions_run_in_order(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="做一个 5 页 PPTX，用于产品发布。",
            inputs=[],
            output={"format": "pptx"},
        )
        content_plan = manager.build_initial_deck_content_plan(requirement)

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            paths = prepare_html_route_paths(Path(tmpdir))
            template_stage = prepare_html_template(template_id="clean_business", aspect_ratio="16:9")
            page_stage = generate_html_pages(
                content_plan=content_plan,
                template=template_stage.template,
                paths=paths,
            )
            pptx_stage = export_html_pptx(
                content_plan=content_plan,
                template=template_stage.template,
                page_generation=page_stage,
                paths=paths,
            )
            quality_stage = deliver_html_route_quality(
                content_plan=content_plan,
                template=template_stage.template,
                page_generation=page_stage,
                pptx_output=pptx_stage,
                paths=paths,
            )

            self.assertEqual(
                [
                    template_stage.stage,
                    page_stage.stage,
                    pptx_stage.stage,
                ],
                list(HTML_ROUTE_STAGE_SEQUENCE),
            )
            self.assertEqual(quality_stage.stage, HTML_DELIVERY_STAGE)
            self.assertTrue(page_stage.html_path.exists())
            self.assertTrue(pptx_stage.pptx_path.exists())
            self.assertEqual(quality_stage.quality_report["status"], "pass")


if __name__ == "__main__":
    unittest.main()
