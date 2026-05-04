import json
import tempfile
import unittest
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from src.productions.ppt.ppt_product_manager import PptProductManager
from src.productions.ppt.routes.html import build_html_route
from src.productions.ppt.templates.html_registry import list_html_templates, load_html_template_package
from src.runtime.workspace import workspace_root


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

            self.assertTrue(pptx_path.exists())
            self.assertTrue(html_path.exists())
            self.assertTrue(quality_path.exists())
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

            self.assertEqual(len(prs.slides), len(content_plan.pages))
            self.assertIn(content_plan.pages[0].title, pptx_text)
            self.assertEqual(picture_count, 0)
            self.assertTrue(quality_report["checks"]["pptx_contains_editable_text"])
            self.assertTrue(quality_report["checks"]["pptx_titles_present"])


if __name__ == "__main__":
    unittest.main()
