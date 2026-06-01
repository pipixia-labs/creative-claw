import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.productions.ppt.ppt_product_manager import PptProductManager
from src.productions.ppt.planning import PptContentPlanner
from src.productions.ppt.schemas import (
    ConfirmedRequirement,
    PptSourcePreparationResult,
    SlideCountPolicy,
    SourceInput,
    SourceUnderstanding,
)
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


class _FakeRemoteResponse:
    def __init__(self, data: bytes, headers: dict[str, str]):
        self._data = data
        self._offset = 0
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._data):
            return b""
        if size is None or size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


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

    async def test_prepare_source_materials_phase_persists_typed_result(self) -> None:
        source_path = _write_markdown_source("phase_brief.md", "# Phase Brief\n")
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-source-phase-test", "turn_index": 3, "step": 2})

        result = await manager._prepare_source_materials_phase(
            raw_inputs=[{"name": "phase_brief.md", "path": source_path}],
            tool_context=tool_context,
            expert_agents={},
            app_name="creative_claw",
            artifact_service=None,
            source_converter=_fake_source_converter,
        )

        self.assertIsInstance(result, PptSourcePreparationResult)
        self.assertEqual(result.source_inputs[0].path, source_path)
        self.assertEqual(result.source_materials.markdown_sources[0]["name"], "phase_brief.md")
        self.assertEqual(
            tool_context.state["ppt_source_materials"],
            result.source_materials.model_dump(mode="json"),
        )
        self.assertTrue(result.input_signature)
        self.assertFalse(result.reused_existing_preparation)
        self.assertEqual(
            tool_context.state["ppt_source_markdown_sources"],
            result.source_materials.markdown_sources,
        )
        self.assertEqual(tool_context.state["ppt_source_figures"], result.source_materials.figures)
        self.assertTrue(tool_context.state["ppt_source_output_files"])
        self.assertEqual(
            tool_context.state["ppt_source_preparation_result"]["input_signature"],
            result.input_signature,
        )

    async def test_prepare_source_materials_phase_persists_empty_source_state(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-empty-source-phase-test", "turn_index": 1, "step": 1})

        result = await manager._prepare_source_materials_phase(
            raw_inputs=[],
            tool_context=tool_context,
            expert_agents={},
            app_name="creative_claw",
            artifact_service=None,
            source_converter=_fake_source_converter,
        )

        self.assertEqual(result.source_inputs, [])
        self.assertEqual(result.source_materials.document_type, "brief")
        self.assertEqual(tool_context.state["ppt_source_markdown_sources"], [])
        self.assertEqual(tool_context.state["ppt_source_figures"], [])
        self.assertEqual(tool_context.state["ppt_source_output_files"], [])

    async def test_prepare_source_materials_phase_reuses_same_remote_source(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(
            state={"sid": "ppt-remote-source-reuse-test", "channel": "web", "turn_index": 1, "step": 1}
        )
        source_url = "https://example.com/source.pdf"
        urlopen_calls = 0
        converter_calls = 0

        def _fake_urlopen(*_args, **_kwargs):
            nonlocal urlopen_calls
            urlopen_calls += 1
            return _FakeRemoteResponse(
                b"%PDF-1.4\nremote pdf fixture\n",
                {"content-type": "application/pdf", "content-length": "28"},
            )

        async def _counting_source_converter(source_input: SourceInput, parameters: dict) -> dict:
            nonlocal converter_calls
            converter_calls += 1
            return await _fake_source_converter(source_input, parameters)

        with patch(
            "src.productions.ppt.ppt_product_manager.ppt_product_manager.urlopen",
            side_effect=_fake_urlopen,
        ):
            first_result = await manager._prepare_source_materials_phase(
                raw_inputs=[{"name": "source.pdf", "url": source_url}],
                tool_context=tool_context,
                expert_agents={},
                app_name="creative_claw",
                artifact_service=None,
                source_converter=_counting_source_converter,
            )
            second_result = await manager._prepare_source_materials_phase(
                raw_inputs=[{"name": "source.pdf", "url": source_url}],
                tool_context=tool_context,
                expert_agents={},
                app_name="creative_claw",
                artifact_service=None,
                source_converter=_counting_source_converter,
            )

        self.assertFalse(first_result.reused_existing_preparation)
        self.assertTrue(second_result.reused_existing_preparation)
        self.assertEqual(urlopen_calls, 1)
        self.assertEqual(converter_calls, 1)
        self.assertEqual(first_result.input_signature, second_result.input_signature)
        self.assertEqual(first_result.source_inputs[0].path, second_result.source_inputs[0].path)
        self.assertEqual(len(tool_context.state["ppt_remote_source_downloads"]), 1)
        self.assertTrue(resolve_workspace_path(second_result.source_inputs[0].path).exists())
        self.assertTrue(tool_context.state["ppt_source_preparation_result"]["reused_existing_preparation"])

    async def test_prepare_source_materials_phase_does_not_reuse_failed_remote_source(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(
            state={"sid": "ppt-remote-source-retry-test", "channel": "web", "turn_index": 1, "step": 1}
        )
        source_url = "https://example.com/retry.pdf"
        urlopen_calls = 0

        def _fake_urlopen(*_args, **_kwargs):
            nonlocal urlopen_calls
            urlopen_calls += 1
            if urlopen_calls == 1:
                raise RuntimeError("temporary remote failure")
            return _FakeRemoteResponse(
                b"%PDF-1.4\nremote pdf fixture\n",
                {"content-type": "application/pdf", "content-length": "28"},
            )

        with patch(
            "src.productions.ppt.ppt_product_manager.ppt_product_manager.urlopen",
            side_effect=_fake_urlopen,
        ):
            first_result = await manager._prepare_source_materials_phase(
                raw_inputs=[{"name": "retry.pdf", "url": source_url}],
                tool_context=tool_context,
                expert_agents={},
                app_name="creative_claw",
                artifact_service=None,
                source_converter=_fake_source_converter,
            )
            second_result = await manager._prepare_source_materials_phase(
                raw_inputs=[{"name": "retry.pdf", "url": source_url}],
                tool_context=tool_context,
                expert_agents={},
                app_name="creative_claw",
                artifact_service=None,
                source_converter=_fake_source_converter,
            )

        self.assertFalse(first_result.reused_existing_preparation)
        self.assertFalse(second_result.reused_existing_preparation)
        self.assertEqual(urlopen_calls, 2)
        self.assertTrue(first_result.source_inputs[0].path.startswith("https://"))
        self.assertFalse(second_result.source_inputs[0].path.startswith("https://"))
        self.assertTrue(resolve_workspace_path(second_result.source_inputs[0].path).exists())

    async def test_prepare_source_materials_phase_reruns_when_local_source_changes(self) -> None:
        source_path = _write_markdown_source("changing_source.md", "# First Version\n")
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-source-change-test", "turn_index": 1, "step": 1})

        first_result = await manager._prepare_source_materials_phase(
            raw_inputs=[{"name": "changing_source.md", "path": source_path}],
            tool_context=tool_context,
            expert_agents={},
            app_name="creative_claw",
            artifact_service=None,
            source_converter=None,
        )
        resolve_workspace_path(source_path).write_text("# Second Version\n", encoding="utf-8")
        second_result = await manager._prepare_source_materials_phase(
            raw_inputs=[{"name": "changing_source.md", "path": source_path}],
            tool_context=tool_context,
            expert_agents={},
            app_name="creative_claw",
            artifact_service=None,
            source_converter=None,
        )

        self.assertFalse(first_result.reused_existing_preparation)
        self.assertFalse(second_result.reused_existing_preparation)
        self.assertNotEqual(first_result.input_signature, second_result.input_signature)

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
