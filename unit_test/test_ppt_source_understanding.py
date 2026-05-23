import unittest
from pathlib import Path
from types import SimpleNamespace

from src.productions.ppt.ppt_product_manager import PptProductManager
from src.productions.ppt.planning import PptContentPlanner
from src.productions.ppt.schemas import ConfirmedRequirement, SlideCountPolicy, SourceInput, SourceUnderstanding
from src.runtime.workspace import build_workspace_file_record, resolve_workspace_path, workspace_relative_path, workspace_root


def _write_markdown_source(name: str, text: str) -> str:
    source_dir = workspace_root() / "inbox" / "ppt_source_material_tests"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / name
    source_path.write_text(text, encoding="utf-8")
    return workspace_relative_path(source_path)


def _write_markdown_source_with_asset(name: str) -> str:
    source_dir = workspace_root() / "inbox" / "ppt_source_material_tests" / Path(name).stem
    asset_dir = source_dir / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "chart.png").write_bytes(b"fake-png")
    source_path = source_dir / name
    source_path.write_text("# Brief\n\n![Growth chart](assets/chart.png)\n", encoding="utf-8")
    return workspace_relative_path(source_path)


async def _fake_source_converter(source_input: SourceInput, parameters: dict) -> dict:
    output_path = str(parameters["output_path"])
    output_file = resolve_workspace_path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    asset_dir = output_file.parent / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    figure_path = asset_dir / "chart.png"
    figure_path.write_bytes(b"fake-png")
    markdown = "# Source Material\n\n![Growth chart](assets/chart.png)\n"
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


class PptSourceMaterialTests(unittest.IsolatedAsyncioTestCase):
    def test_ppt_product_manager_does_not_import_expert_tool(self) -> None:
        manager_file = Path("src/productions/ppt/ppt_product_manager/ppt_product_manager.py")

        source_text = manager_file.read_text(encoding="utf-8")

        self.assertNotIn("anything_to_md.tool", source_text)
        self.assertNotIn("convert_anything_to_markdown", source_text)

    async def test_prepare_source_materials_records_markdown_and_figures(self) -> None:
        source_path = _write_markdown_source("brief.md", "# Brief\n")
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-source-test", "turn_index": 1, "step": 2})

        result = await manager._prepare_source_materials(
            [SourceInput(name="brief.md", path=source_path)],
            fallback_document_type="markdown",
            tool_context=tool_context,
            source_converter=_fake_source_converter,
        )

        self.assertEqual(result.document_type, "markdown")
        self.assertEqual(result.markdown_sources[0]["name"], "brief.md")
        self.assertEqual(result.markdown_sources[0]["method"], "test:markdown")
        self.assertTrue(result.markdown_sources[0]["output_path"].endswith(".md"))
        self.assertEqual(result.figures[0]["alt"], "Growth chart")
        self.assertTrue(result.figures[0]["path"].endswith("assets/chart.png"))
        self.assertTrue(resolve_workspace_path(result.markdown_sources[0]["output_path"]).exists())
        self.assertTrue(resolve_workspace_path(result.figures[0]["path"]).exists())
        markdown_text = resolve_workspace_path(result.markdown_sources[0]["output_path"]).read_text(encoding="utf-8")
        self.assertIn(result.figures[0]["path"], markdown_text)
        self.assertNotIn("](assets/chart.png)", markdown_text)
        self.assertTrue(result.output_files)

    async def test_read_ppt_markdown_sources_exposes_workspace_relative_image_paths(self) -> None:
        markdown_path = _write_markdown_source_with_asset("relative_images.md")
        source_materials = SourceUnderstanding(
            document_type="markdown",
            markdown_sources=[
                {
                    "name": "relative_images.md",
                    "source_path": markdown_path,
                    "method": "test:markdown_passthrough",
                    "output_path": markdown_path,
                }
            ],
        )
        requirement = ConfirmedRequirement(
            route="html",
            topic="AI sales assistant launch deck",
            source_understanding=source_materials,
        )
        tool_context = SimpleNamespace(state={"ppt_confirmed_requirement": requirement.model_dump(mode="json")})

        result = PptContentPlanner().read_ppt_markdown_sources(tool_context)

        source_text = result["source_texts"][0]["text"]
        self.assertIn("inbox/ppt_source_material_tests/relative_images/assets/chart.png", source_text)
        self.assertNotIn("](assets/chart.png)", source_text)

    async def test_content_planner_uses_source_material_references_only(self) -> None:
        source_path = _write_markdown_source("planner_brief.md", "# AI Sales Assistant\n")
        source_materials = SourceUnderstanding(
            document_type="markdown",
            markdown_sources=[
                {
                    "name": "planner_brief.md",
                    "source_path": source_path,
                    "method": "test:markdown",
                    "output_path": "generated/planner_brief.md",
                }
            ],
        )
        requirement = ConfirmedRequirement(
            route="html",
            topic="AI sales assistant launch deck",
            slide_count_policy=SlideCountPolicy(minimum=6, maximum=6, target=6, source="user"),
            source_inputs=[SourceInput(name="planner_brief.md", path=source_path)],
            source_understanding=source_materials,
        )

        plan = PptContentPlanner().build_plan(requirement)

        plan_text = str(plan.model_dump(mode="json"))
        self.assertEqual(len(plan.pages), 6)
        self.assertIn("Planner Brief", [chapter.title for chapter in plan.chapters])
        self.assertIn("prepared Markdown sources", plan_text)
        self.assertNotIn("cover", {page.page_type for page in plan.pages})
        self.assertNotIn("toc", {page.page_type for page in plan.pages})
        self.assertNotIn("chapter_start", {page.page_type for page in plan.pages})
        self.assertNotIn("chapter_content", {page.page_type for page in plan.pages})


if __name__ == "__main__":
    unittest.main()
