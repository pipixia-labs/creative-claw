import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from pptx import Presentation

from src.productions.ppt.ppt_product_manager import PptProductManager
from src.productions.ppt.routes.svg import (
    PPT_SVG_ROUTE_GENERATED_PAGES_KEY,
    SVG_DELIVERY_STAGE,
    SVG_ROUTE_STAGE_SEQUENCE,
    build_ppt_design_strategy_expert,
    build_ppt_svg_deck_executor_expert,
    build_svg_route,
    check_svg_pages_quality,
    export_svg_pages_to_pptx,
    prepare_svg_route_paths,
)
from src.productions.ppt.schemas import PptSvgExecutionPlan
from src.runtime.workspace import resolve_workspace_path, workspace_root


class PptSvgRouteTests(unittest.TestCase):
    def test_svg_route_builds_svg_pages_quality_report_and_editable_pptx(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="做一个 3 页 PPTX 产品介绍，使用 SVG route。",
            inputs=[],
            output={"format": "pptx", "route": "svg", "slide_count": 3},
        )
        content_plan = manager.build_initial_deck_content_plan(requirement)

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            route_build = build_svg_route(
                content_plan=content_plan,
                output_dir=Path(tmpdir),
                aspect_ratio="16:9",
            )

            pptx_path = resolve_workspace_path(route_build.pptx_path)
            quality_path = resolve_workspace_path(route_build.quality_report_path)
            build_log_path = resolve_workspace_path(route_build.build_log_path)
            svg_paths = [resolve_workspace_path(path) for path in route_build.svg_page_paths]
            prs = Presentation(str(pptx_path))
            pptx_text = "\n".join(
                shape.text
                for slide in prs.slides
                for shape in slide.shapes
                if getattr(shape, "has_text_frame", False)
            )
            quality_report = json.loads(quality_path.read_text(encoding="utf-8"))
            build_log = json.loads(build_log_path.read_text(encoding="utf-8"))

            self.assertTrue(pptx_path.exists())
            self.assertTrue(all(path.exists() for path in svg_paths))
            self.assertEqual(len(route_build.svg_page_paths), len(content_plan.pages))
            self.assertEqual(len(prs.slides), len(content_plan.pages))
            self.assertIn(content_plan.pages[0].title, pptx_text)
            self.assertEqual(quality_report["status"], "pass")
            self.assertEqual(quality_report["route_stages"], list(SVG_ROUTE_STAGE_SEQUENCE))
            self.assertEqual(quality_report["delivery_stage"], SVG_DELIVERY_STAGE)
            self.assertEqual(build_log["workflow_name"], "SvgRouteSequentialAgent")
            self.assertEqual(build_log["route_stages"], list(SVG_ROUTE_STAGE_SEQUENCE))
            self.assertEqual(build_log["pptx_conversion"]["engine"], "native_drawingml_svg_converter")
            self.assertEqual(build_log["pptx_conversion"]["editable_level"], "high")

    def test_svg_quality_checker_reports_invalid_svg(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            svg_path = Path(tmpdir) / "bad.svg"
            svg_path.write_text("<svg viewBox='0 0 10 10'><script /></svg>", encoding="utf-8")

            report = check_svg_pages_quality(
                svg_page_paths=[str(svg_path)],
                expected_page_count=1,
                execution_plan=PptSvgExecutionPlan(),
            )

            self.assertEqual(report.status, "failed")
            self.assertFalse(report.checks["all_viewboxes_match_canvas"])
            self.assertFalse(report.checks["no_unsupported_tags"])

    def test_svg_quality_checker_rejects_malformed_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            svg_path = Path(tmpdir) / "bad_path.svg"
            svg_path.write_text(
                "<svg xmlns='http://www.w3.org/2000/svg' width='1280' height='720' viewBox='0 0 1280 720'><path d='M 10'/></svg>",
                encoding="utf-8",
            )

            report = check_svg_pages_quality(
                svg_page_paths=[str(svg_path)],
                expected_page_count=1,
                execution_plan=PptSvgExecutionPlan(),
            )

            self.assertEqual(report.status, "failed")
            self.assertFalse(report.checks["all_visual_elements_convertible"])
            self.assertIn("malformed_svg_path", {issue["code"] for issue in report.issues})

    def test_svg_expert_builders_use_ppt_svg_tools(self) -> None:
        manager = PptProductManager()
        design_agent = build_ppt_design_strategy_expert(
            save_design_strategy_tool=manager.save_ppt_design_strategy,
            save_svg_execution_plan_tool=manager.save_ppt_svg_execution_plan,
        )
        executor_agent = build_ppt_svg_deck_executor_expert(
            read_svg_execution_plan_tool=manager.read_ppt_svg_execution_plan,
            save_svg_page_tool=manager.save_ppt_svg_page,
        )

        self.assertEqual(design_agent.name, "PptDesignStrategyExpert")
        self.assertEqual(executor_agent.name, "PptSvgDeckExecutorExpert")
        self.assertEqual(
            {tool.__name__ for tool in design_agent.tools},
            {"save_ppt_design_strategy", "save_ppt_svg_execution_plan"},
        )
        self.assertEqual(
            {tool.__name__ for tool in executor_agent.tools},
            {"read_ppt_svg_execution_plan", "save_ppt_svg_page"},
        )

    def test_save_ppt_svg_page_tool_writes_route_page(self) -> None:
        manager = PptProductManager()
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            paths = prepare_svg_route_paths(Path(tmpdir))
            tool_context = SimpleNamespace(
                state={
                    "sid": "svg-page-tool-test",
                    "turn_index": 1,
                    "step": 1,
                    "ppt_svg_route_output_dir": str(paths.output_dir),
                }
            )
            result = manager.save_ppt_svg_execution_plan(
                {"aspect_ratio": "16:9", "canvas_width": 1280, "canvas_height": 720},
                tool_context,
            )
            self.assertEqual(result["status"], "success")

            save_result = manager.save_ppt_svg_page(
                slide_number=1,
                file_name="slide_001.svg",
                title="Cover",
                page_type="cover",
                page_rhythm="anchor",
                svg_content="<svg xmlns='http://www.w3.org/2000/svg' width='1280' height='720' viewBox='0 0 1280 720'><text x='72' y='120'>Cover</text></svg>",
                tool_context=tool_context,
            )

            self.assertEqual(save_result["status"], "success")
            saved_page = tool_context.state[PPT_SVG_ROUTE_GENERATED_PAGES_KEY][0]
            self.assertEqual(saved_page["slide_number"], 1)
            self.assertTrue(resolve_workspace_path(saved_page["svg_path"]).exists())

    def test_native_svg_export_supports_paths_and_images(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            output_dir = Path(tmpdir)
            svg_path = output_dir / "native.svg"
            pptx_path = output_dir / "native.pptx"
            svg_path.write_text(
                """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
  <path d="M 80 120 L 260 120 L 220 220 Z" fill="#2457D6" stroke="#172033" stroke-width="2"/>
  <polyline points="320,120 390,170 460,120" fill="none" stroke="#43A6FF" stroke-width="4"/>
  <image x="80" y="280" width="80" height="80" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="/>
  <text x="200" y="330" data-width="600" font-family="Aptos" font-size="32" fill="#172033"><tspan x="200" dy="0">Native SVG Text</tspan><tspan x="200" dy="42">Second line</tspan></text>
</svg>""",
                encoding="utf-8",
            )

            report = check_svg_pages_quality(
                svg_page_paths=[str(svg_path)],
                expected_page_count=1,
                execution_plan=PptSvgExecutionPlan(),
            )
            export = export_svg_pages_to_pptx(
                svg_page_paths=[str(svg_path)],
                pptx_path=pptx_path,
                execution_plan=PptSvgExecutionPlan(),
            )
            prs = Presentation(str(pptx_path))
            pptx_text = "\n".join(
                shape.text
                for slide in prs.slides
                for shape in slide.shapes
                if getattr(shape, "has_text_frame", False)
            )

            self.assertEqual(report.status, "pass")
            self.assertTrue(pptx_path.exists())
            self.assertTrue(export.conversion_report["ok"])
            self.assertEqual(export.conversion_report["engine"], "native_drawingml_svg_converter")
            self.assertEqual(export.conversion_report["pages"][0]["image_count"], 1)
            self.assertIn("Native SVG Text", pptx_text)

    def test_native_svg_export_supports_ppt_master_baseline_features(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            output_dir = Path(tmpdir)
            image_path = output_dir / "wide.png"
            svg_path = output_dir / "baseline.svg"
            pptx_path = output_dir / "baseline.pptx"
            Image.new("RGB", (4, 2), "#2457D6").save(image_path)
            svg_path.write_text(
                f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <defs>
    <linearGradient id="accentGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#2457D6"/>
      <stop offset="100%" stop-color="#43A6FF"/>
    </linearGradient>
    <marker id="arrow" markerWidth="8" markerHeight="8"><path d="M 0 0 L 8 4 L 0 8 Z"/></marker>
    <clipPath id="imageClip"><circle cx="980" cy="260" r="70"/></clipPath>
    <filter id="softShadow"><feGaussianBlur stdDeviation="4"/><feOffset dx="3" dy="4"/><feFlood flood-color="#000000" flood-opacity="0.25"/></filter>
  </defs>
  <g id="editableGroup" filter="url(#softShadow)">
    <rect x="64" y="64" width="500" height="150" rx="16" fill="url(#accentGrad)" stroke="#172033" stroke-width="2"/>
    <path d="M 96 360 C 160 300 220 420 280 360 S 400 300 460 360 Q 520 430 580 360 T 700 360 A 42 42 0 0 1 784 360" fill="none" stroke="#172033" stroke-width="4" marker-end="url(#arrow)"/>
    <image x="900" y="190" width="160" height="140" href="{image_path.name}" preserveAspectRatio="xMidYMid slice" clip-path="url(#imageClip)"/>
    <text x="96" y="150" data-width="420" font-family="Aptos" font-size="34" fill="#FFFFFF"><tspan font-weight="bold">Rich</tspan><tspan fill="#F4C542" font-style="italic"> Text</tspan></text>
  </g>
</svg>""",
                encoding="utf-8",
            )

            report = check_svg_pages_quality(
                svg_page_paths=[str(svg_path)],
                expected_page_count=1,
                execution_plan=PptSvgExecutionPlan(),
            )
            export = export_svg_pages_to_pptx(
                svg_page_paths=[str(svg_path)],
                pptx_path=pptx_path,
                execution_plan=PptSvgExecutionPlan(),
            )
            prs = Presentation(str(pptx_path))
            with zipfile.ZipFile(pptx_path) as pptx_zip:
                slide_xml = pptx_zip.read("ppt/slides/slide1.xml").decode("utf-8")

            self.assertEqual(report.status, "pass")
            self.assertTrue(export.conversion_report["ok"])
            self.assertEqual(len(prs.slides), 1)
            self.assertIn("<a:gradFill", slide_xml)
            self.assertIn("<a:tailEnd", slide_xml)
            self.assertIn("<a:srcRect", slide_xml)
            self.assertIn("<p:grpSp>", slide_xml)
            self.assertIn("<a:outerShdw", slide_xml)
            self.assertIn("<a:cubicBezTo>", slide_xml)

    def test_native_svg_export_failure_does_not_replace_existing_pptx(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            output_dir = Path(tmpdir)
            svg_path = output_dir / "bad.svg"
            pptx_path = output_dir / "deck.pptx"
            original_bytes = b"existing-pptx-placeholder"
            pptx_path.write_bytes(original_bytes)
            svg_path.write_text(
                "<svg xmlns='http://www.w3.org/2000/svg' width='1280' height='720' viewBox='0 0 1280 720'><path d='M 0 0 R 20 20 40 40'/></svg>",
                encoding="utf-8",
            )

            export = export_svg_pages_to_pptx(
                svg_page_paths=[str(svg_path)],
                pptx_path=pptx_path,
                execution_plan=PptSvgExecutionPlan(),
            )

            self.assertFalse(export.conversion_report["ok"])
            self.assertEqual(export.conversion_report["final_strategy"], "failed")
            self.assertEqual(pptx_path.read_bytes(), original_bytes)

    def test_save_ppt_svg_page_rejects_unsupported_native_subset(self) -> None:
        manager = PptProductManager()
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            paths = prepare_svg_route_paths(Path(tmpdir))
            tool_context = SimpleNamespace(
                state={
                    "sid": "svg-page-tool-test",
                    "turn_index": 1,
                    "step": 1,
                    "ppt_svg_route_output_dir": str(paths.output_dir),
                }
            )
            manager.save_ppt_svg_execution_plan(
                {"aspect_ratio": "16:9", "canvas_width": 1280, "canvas_height": 720},
                tool_context,
            )

            with self.assertRaisesRegex(ValueError, "native PPTX converter subset"):
                manager.save_ppt_svg_page(
                    slide_number=1,
                    file_name="bad.svg",
                    title="Bad",
                    page_type="content",
                    page_rhythm="body",
                    svg_content="<svg xmlns='http://www.w3.org/2000/svg' width='1280' height='720' viewBox='0 0 1280 720'><foreignObject /></svg>",
                    tool_context=tool_context,
                )


if __name__ == "__main__":
    unittest.main()
