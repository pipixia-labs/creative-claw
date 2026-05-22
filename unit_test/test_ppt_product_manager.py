import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from google.adk.agents import LlmAgent
from google.genai.types import Content, Part
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from src.productions.ppt.planning.content_planner import (
    _build_content_planning_user_message,
    _select_content_page_type,
)
from src.productions.ppt.ppt_product_manager import (
    PptProductManager,
    ProductPptSkillRegistry,
)
from src.productions.ppt.ppt_product_manager.ppt_product_manager import (
    _build_product_manager_skill_run_user_message,
    _snapshot_workspace_pptx_files,
)
from src.productions.ppt.schemas import DeckContentPlan, DeckPageAsset, DeckPagePlan, SourceUnderstanding
from src.productions.ppt.routes.html import PPT_HTML_PAGE_GENERATION_EXPERT_NAME
from src.productions.ppt.routes.svg import (
    PPT_DESIGN_STRATEGY_EXPERT_NAME,
    PPT_SVG_DECK_EXECUTOR_EXPERT_NAME,
    PPT_SVG_EXECUTION_PLAN_STATE_KEY,
)
from src.runtime.workspace import (
    build_workspace_file_record,
    resolve_workspace_path,
    workspace_relative_path,
    workspace_root,
)
from src.skills.registry import SkillRegistry


def _write_markdown_source(name: str, text: str) -> str:
    source_dir = workspace_root() / "inbox" / "ppt_product_manager_tests"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / name
    source_path.write_text(text, encoding="utf-8")
    return workspace_relative_path(source_path)


def _write_test_image(name: str) -> str:
    image_dir = workspace_root() / "inbox" / "ppt_product_manager_tests"
    image_dir.mkdir(parents=True, exist_ok=True)
    image_path = image_dir / name
    Image.new("RGB", (640, 360), "#2457D6").save(image_path)
    return workspace_relative_path(image_path)


def _page(slide_number: int, page_type: str) -> DeckPagePlan:
    return DeckPagePlan(
        slide_number=slide_number,
        page_type=page_type,
        title=f"Slide {slide_number}",
        purpose="Explain the planned message.",
        key_takeaway="Audience remembers the core point.",
        asset_intent="Use a simple supporting visual.",
    )


class _DictState(dict):
    def to_dict(self) -> dict:
        return dict(self)


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


async def _fake_source_converter(source_input, parameters: dict) -> dict:
    output_path = str(parameters["output_path"])
    output_file = resolve_workspace_path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    asset_dir = output_file.parent / "figures"
    asset_dir.mkdir(parents=True, exist_ok=True)
    chart_path = asset_dir / "activation.png"
    Image.new("RGB", (640, 360), "#43A6FF").save(chart_path)
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
        self.assertEqual(manager.include_contents, "none")
        self.assertEqual(
            {tool.__name__ for tool in manager.tools},
            {
                "list_product_ppt_skills",
                "read_product_ppt_skill",
                "read_product_ppt_skill_file",
                "list_session_files",
                "list_dir",
                "glob",
                "grep",
                "read_file",
                "write_file",
                "edit_file",
                "exec_command",
                "process_session",
                "list_ppt_experts",
                "invoke_ppt_expert",
                "save_ppt_system_selection",
                "save_ppt_private_skill_html",
                "save_ppt_private_skill_pptx",
                "save_ppt_design_strategy",
                "save_ppt_svg_execution_plan",
                "read_ppt_svg_execution_plan",
                "save_ppt_svg_page",
                "check_ppt_svg_quality",
                "export_ppt_svg_to_pptx",
                "dispatch_ppt_route",
            },
        )
        self.assertIn("PPT and PowerPoint production", instruction)
        self.assertIn("ADK workflow", instruction)
        self.assertIn("currently implemented built-in route", instruction)
        self.assertIn("PPT system selection", instruction)
        self.assertIn("skills/product-ppt-skills", instruction)
        self.assertIn("built-in HTML route", instruction)
        self.assertIn("uploaded input includes PPTX/PPTM/POTX/POTM", instruction)
        self.assertIn("choose between the built-in HTML route and the built-in SVG route", instruction)
        self.assertIn("hard-coded keyword-to-skill rules", instruction)
        self.assertIn("you run that skill workflow directly as PptProductManager", instruction)
        self.assertIn("PptHtmlPageGenerationExpert", instruction)
        self.assertIn("PptDesignStrategyExpert", instruction)
        self.assertIn("PptSvgDeckExecutorExpert", instruction)
        self.assertIn("invoke_ppt_expert", instruction)
        self.assertIn("export_ppt_svg_to_pptx", instruction)
        self.assertIn("template-based PPTX workflow", instruction)
        self.assertIn("immediately after the file is generated and verified", instruction)
        self.assertIn("must not block delivery", instruction)
        self.assertIn("Template analysis artifacts", instruction)
        self.assertIn("Do not stop after `thumbnail.py`", instruction)
        self.assertIn("return a concrete blocker", instruction)
        self.assertIn("Do not claim PPTX generation succeeded", instruction)

    def test_private_pptx_template_skill_prompt_enforces_delivery_checklist(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="用这个模板做一个毕业答辩 PPT。",
            inputs=[{"name": "template.pptx", "path": "inbox/demo/template.pptx"}],
            output={"format": "pptx", "route": "xml"},
        )
        content_plan = manager.build_initial_deck_content_plan(requirement)

        user_message = _build_product_manager_skill_run_user_message(
            requirement=requirement,
            content_plan=content_plan,
            system_selection={
                "system_type": "private_skill",
                "route": "xml",
                "skill_name": "pptx",
                "output_format": "pptx",
                "reason": "Use pptx skill for uploaded template.",
            },
            skill_content="# PPTX Skill\nTemplate-Based Workflow",
            available_experts=[],
        )

        self.assertIn("user_template_pptx_workflow_checklist", user_message)
        self.assertIn("confirmed_requirement_json.source_inputs", user_message)
        self.assertIn("template_requirement.template_path", user_message)
        self.assertIn("list_session_files(section='uploaded')", user_message)
        self.assertIn("uploaded_history", user_message)
        self.assertIn("ppt_private_skill_output_dir", user_message)
        self.assertIn("Template thumbnails", user_message)
        self.assertIn("not final deliverables", user_message)
        self.assertIn("Do not stop after thumbnail.py", user_message)
        self.assertIn("generate a real .pptx", user_message)
        self.assertIn("immediately register it with save_ppt_private_skill_pptx", user_message)
        self.assertIn("optional QA or expert failures", user_message)
        self.assertIn("save_ppt_private_skill_pptx", user_message)
        self.assertIn("concrete blocker", user_message)

    def test_list_session_files_uploaded_falls_back_to_latest_history(self) -> None:
        manager = PptProductManager()
        source_dir = workspace_root() / "inbox" / "ppt_product_manager_tests"
        source_dir.mkdir(parents=True, exist_ok=True)
        template_path = source_dir / "history-template.pptx"
        template_path.write_bytes(b"pptx")
        file_record = build_workspace_file_record(
            template_path,
            description="Uploaded template.",
            source="channel",
            name="history-template.pptx",
            turn=1,
        )
        tool_context = SimpleNamespace(
            state={
                "uploaded": [],
                "input_files": [],
                "uploaded_history": [
                    {"turn": 0, "files": []},
                    {"turn": 1, "files": [file_record]},
                ],
            }
        )

        uploaded_result = manager.list_session_files(section="uploaded", tool_context=tool_context)
        input_result = manager.list_session_files(section="input", tool_context=tool_context)

        self.assertEqual(uploaded_result["uploaded"], [file_record])
        self.assertEqual(input_result["input_files"], [file_record])

    def test_private_skill_source_inputs_are_visible_as_uploaded_files(self) -> None:
        manager = PptProductManager()
        source_dir = workspace_root() / "inbox" / "ppt_product_manager_tests"
        source_dir.mkdir(parents=True, exist_ok=True)
        template_path = source_dir / "confirmed-template.pptx"
        template_path.write_bytes(b"pptx")
        relative_template_path = workspace_relative_path(template_path)
        requirement = manager.prepare_confirmed_requirement(
            task="用这个模板做一个 PPT。",
            inputs=[{"name": "confirmed-template.pptx", "path": relative_template_path}],
            output={"format": "pptx", "route": "xml"},
        )
        state = {
            "uploaded": [],
            "input_files": [],
            "uploaded_history": [],
            "turn_index": 2,
            "step": 3,
        }

        appended = manager._ensure_private_skill_source_files_visible(state, requirement)

        self.assertEqual(len(appended), 1)
        self.assertEqual(appended[0]["path"], relative_template_path)
        self.assertEqual(state["uploaded"][0]["path"], relative_template_path)
        self.assertEqual(state["input_files"][0]["path"], relative_template_path)
        self.assertEqual(state["uploaded"][0]["source"], "confirmed_requirement")

    def test_product_manager_registers_html_page_generation_expert(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={})

        experts = manager.product_expert_agents
        listed = manager.list_ppt_experts(tool_context)

        self.assertIn(PPT_HTML_PAGE_GENERATION_EXPERT_NAME, experts)
        self.assertIn(PPT_DESIGN_STRATEGY_EXPERT_NAME, experts)
        self.assertIn(PPT_SVG_DECK_EXECUTOR_EXPERT_NAME, experts)
        self.assertEqual(experts[PPT_HTML_PAGE_GENERATION_EXPERT_NAME].name, PPT_HTML_PAGE_GENERATION_EXPERT_NAME)
        self.assertEqual(
            {tool.__name__ for tool in experts[PPT_HTML_PAGE_GENERATION_EXPERT_NAME].tools},
            {"save_html_route_pages"},
        )
        self.assertEqual(
            {tool.__name__ for tool in experts[PPT_DESIGN_STRATEGY_EXPERT_NAME].tools},
            {"save_ppt_design_strategy", "save_ppt_svg_execution_plan"},
        )
        self.assertEqual(
            {tool.__name__ for tool in experts[PPT_SVG_DECK_EXECUTOR_EXPERT_NAME].tools},
            {"read_ppt_svg_execution_plan", "save_ppt_svg_page"},
        )
        self.assertIn(PPT_HTML_PAGE_GENERATION_EXPERT_NAME, listed["experts"])
        self.assertIn(PPT_DESIGN_STRATEGY_EXPERT_NAME, listed["experts"])
        self.assertIn(PPT_SVG_DECK_EXECUTOR_EXPERT_NAME, listed["experts"])
        self.assertEqual(tool_context.state["ppt_skill_available_experts"], listed["experts"])

    async def test_invoke_ppt_html_page_generation_expert_uses_ppt_state(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="做一个 3 页 PPTX 产品介绍。",
            inputs=[],
            output={"format": "pptx", "slide_count": 3},
        )
        content_plan = manager.build_initial_deck_content_plan(requirement)
        captured: dict[str, object] = {}

        async def _fake_run_html_page_generation_expert(**kwargs):
            captured.update(kwargs)
            return [{"slide_number": 1, "html": "<section><h1>Slide</h1></section>"}]

        tool_context = SimpleNamespace(
            state={
                "ppt_confirmed_requirement": requirement.model_dump(mode="json"),
                "ppt_deck_content_plan": content_plan.model_dump(mode="json"),
            },
            _invocation_context=SimpleNamespace(app_name="creative_claw", user_id="test-user"),
        )

        with patch(
            "src.productions.ppt.ppt_product_manager.ppt_product_manager.run_html_page_generation_expert",
            _fake_run_html_page_generation_expert,
        ):
            result = await manager.invoke_ppt_expert(
                PPT_HTML_PAGE_GENERATION_EXPERT_NAME,
                "Generate editable HTML slide fragments.",
                tool_context,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["agent_name"], PPT_HTML_PAGE_GENERATION_EXPERT_NAME)
        self.assertEqual(result["current_output"]["html_pages"][0]["slide_number"], 1)
        self.assertIs(
            captured["page_generation_agent"],
            manager.product_expert_agents[PPT_HTML_PAGE_GENERATION_EXPERT_NAME],
        )
        self.assertEqual(captured["template"].template_id, "free_design")
        self.assertEqual(tool_context.state["current_output"]["status"], "success")
        self.assertEqual(
            tool_context.state["ppt_skill_last_expert_result"]["agent_name"],
            PPT_HTML_PAGE_GENERATION_EXPERT_NAME,
        )

    async def test_invoke_ppt_expert_returns_error_payload_for_bad_parameters(self) -> None:
        manager = PptProductManager()
        manager._skill_runtime_expert_agents = {
            "ImageUnderstandingAgent": SimpleNamespace(name="ImageUnderstandingAgent")
        }
        tool_context = SimpleNamespace(
            state=_DictState({"sid": "ppt-expert-error-test", "turn_index": 1, "step": 1}),
            _invocation_context=SimpleNamespace(app_name="creative_claw", user_id="test-user"),
        )

        result = await manager.invoke_ppt_expert(
            "ImageUnderstandingAgent",
            "Visually inspect these rendered PPT slides.",
            tool_context,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["agent_name"], "ImageUnderstandingAgent")
        self.assertIn("requires structured invoke_agent parameters", result["message"])
        self.assertEqual(tool_context.state["ppt_skill_last_expert_result"], result)

    def test_private_product_ppt_skill_registry_lists_complete_workflow(self) -> None:
        registry = ProductPptSkillRegistry()

        skills = registry.list_skills()
        skill_names = {skill.name for skill in skills}
        content = registry.read_skill("ppt-complete-workflow")
        skill_file_content = registry.read_skill_file("ppt-complete-workflow", "SKILL.md")

        self.assertIn("ppt-complete-workflow", skill_names)
        self.assertIn("PPT Complete Workflow", content)
        self.assertEqual(content, skill_file_content)
        self.assertIn("Built-in HTML route", content)
        self.assertIn("If the user explicitly specifies", content)
        self.assertIn("Do not use local absolute paths", content)

    def test_private_product_ppt_skill_registry_lists_pptx_template_skill(self) -> None:
        registry = ProductPptSkillRegistry()

        skills = registry.list_skills()
        skill_names = {skill.name for skill in skills}
        content = registry.read_skill("pptx")
        editing = registry.read_skill_file("pptx", "editing.md")

        self.assertIn("pptx", skill_names)
        self.assertIn("PPTX Skill", content)
        self.assertIn("uploaded PPTX/POTX template workflows", content)
        self.assertIn("Edit or create from template", content)
        self.assertIn("Template-Based Workflow", editing)

    def test_svg_route_accepts_explicit_system_layout_template_requirement(self) -> None:
        manager = PptProductManager()

        requirement = manager.prepare_confirmed_requirement(
            task="做一个战略咨询汇报 PPT，走 svg route，用 mckinsey 模板。",
            inputs=[],
            output={"format": "pptx", "route": "svg", "template_id": "mckinsey"},
        )

        self.assertEqual(requirement.route, "svg")
        self.assertTrue(requirement.template_requirement.use_template)
        self.assertEqual(requirement.template_requirement.template_source, "system")
        self.assertEqual(requirement.template_requirement.template_id, "mckinsey")

    def test_global_skill_registry_does_not_expose_private_product_ppt_skills(self) -> None:
        global_registry = SkillRegistry()

        skill_names = {skill.name for skill in global_registry.list_skills()}

        self.assertNotIn("ppt-complete-workflow", skill_names)
        self.assertNotIn("product-ppt-skills", skill_names)

    def test_private_ppt_skill_tools_list_and_read_skills(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={})

        listed = manager.list_product_ppt_skills(tool_context)
        read = manager.read_product_ppt_skill("ppt-complete-workflow", tool_context)

        self.assertEqual(listed["status"], "success")
        self.assertGreaterEqual(listed["count"], 1)
        self.assertEqual(read["status"], "success")
        self.assertEqual(read["name"], "ppt-complete-workflow")
        self.assertIn("PPT Complete Workflow", read["content"])
        self.assertEqual(tool_context.state["active_product_ppt_skill"]["name"], "ppt-complete-workflow")

    def test_private_ppt_skill_tool_reads_skill_relative_files(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={})

        result = manager.read_product_ppt_skill_file(
            name="ppt-complete-workflow",
            relative_path="SKILL.md",
            tool_context=tool_context,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["name"], "ppt-complete-workflow")
        self.assertEqual(result["relative_path"], "SKILL.md")
        self.assertIn("PPT Complete Workflow", result["content"])

    def test_private_ppt_skill_workspace_tools_use_runtime_workspace(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-private-tools-test"})
        path = "generated/ppt-private-tools-test/notes.txt"

        write_result = manager.write_file(path, "alpha\nbeta\n", tool_context)
        read_result = manager.read_file(path, tool_context)
        grep_result = manager.grep(
            "beta",
            path="generated/ppt-private-tools-test",
            output_mode="content",
            tool_context=tool_context,
        )
        glob_result = manager.glob(
            "*.txt",
            path="generated/ppt-private-tools-test",
            tool_context=tool_context,
        )
        exec_result = manager.exec_command("printf ppt-tool-ok", timeout=10, tool_context=tool_context)

        self.assertIn("Successfully wrote", write_result)
        self.assertEqual(read_result, "alpha\nbeta\n")
        self.assertIn("beta", grep_result)
        self.assertIn("notes.txt", glob_result)
        self.assertEqual(exec_result, "ppt-tool-ok")

    def test_save_ppt_private_skill_pptx_registers_final_artifact(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-private-pptx-test", "turn_index": 1, "step": 2})
        pptx_path = resolve_workspace_path("generated/ppt-private-pptx-test/final.pptx")
        pptx_path.parent.mkdir(parents=True, exist_ok=True)
        deck = Presentation()
        deck.slides.add_slide(deck.slide_layouts[0])
        deck.save(pptx_path)

        result = manager.save_ppt_private_skill_pptx(
            pptx_path=workspace_relative_path(pptx_path),
            description="Private PPTX generated by test.",
            tool_context=tool_context,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["artifact_type"], "pptx")
        self.assertEqual(result["source"], "save_ppt_private_skill_pptx")
        self.assertEqual(result["pptx_path"], workspace_relative_path(pptx_path))
        self.assertEqual(tool_context.state["final_file_paths"], [workspace_relative_path(pptx_path)])
        self.assertEqual(tool_context.state["ppt_private_skill_build"]["pptx_path"], workspace_relative_path(pptx_path))

    def test_recover_unregistered_private_skill_pptx_registers_final_artifact(self) -> None:
        manager = PptProductManager()
        state = {"sid": "ppt-private-recovery-test", "turn_index": 1, "step": 3}
        before_snapshot = _snapshot_workspace_pptx_files()
        source_dir = Path(tempfile.mkdtemp(prefix="work_ai_pptx_recovery_", dir=workspace_root()))
        source_path = source_dir / "recovered_deck.pptx"
        deck = Presentation()
        deck.slides.add_slide(deck.slide_layouts[0])
        deck.save(source_path)

        result = manager._recover_unregistered_private_skill_pptx(
            state,
            skill_name="pptx",
            before_snapshot=before_snapshot,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["artifact_type"], "pptx")
        self.assertEqual(result["source"], "private_skill_pptx_recovery")
        self.assertEqual(result["recovered_from_path"], workspace_relative_path(source_path))
        self.assertNotEqual(result["pptx_path"], workspace_relative_path(source_path))
        self.assertTrue(
            result["pptx_path"].startswith(
                "generated/ppt-private-recovery-test/turn_1/ppt_private_skill_step_3/"
            )
        )
        self.assertTrue(resolve_workspace_path(result["pptx_path"]).exists())
        self.assertEqual(state["final_file_paths"], [result["pptx_path"]])
        self.assertEqual(state["ppt_private_skill_build"]["pptx_path"], result["pptx_path"])

    def test_private_skill_delivery_accepts_svg_pptx_export(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="用 pptx skill 做 2 页 PPTX。",
            inputs=[],
            output={"format": "pptx", "route": "svg", "slide_count": 2},
        )
        content_plan = manager.build_initial_deck_content_plan(requirement)
        pptx_path = "generated/session/turn_1/ppt_svg_route/deck.pptx"
        file_record = build_workspace_file_record(
            pptx_path,
            description="PPT product SVG route PPTX artifact.",
            source="ppt_product_manager",
        )

        private_build = manager._resolve_private_skill_build_from_state(
            {
                "ppt_svg_pptx_export": {
                    "status": "success",
                    "message": "Exported PPT SVG pages.",
                    "pptx_path": pptx_path,
                    "conversion_report": {"ok": True},
                    "output_files": [file_record],
                }
            },
            skill_name="pptx",
        )
        result = manager._build_private_skill_delivery_result(
            requirement=requirement,
            content_plan=content_plan,
            system_selection={
                "system_type": "private_skill",
                "skill_name": "pptx",
                "output_format": "pptx",
            },
            private_build=private_build,
        )

        self.assertEqual(private_build["artifact_type"], "pptx")
        self.assertEqual(private_build["source"], "export_ppt_svg_to_pptx")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.delivery_manifest.final_pptx, pptx_path)
        self.assertEqual(result.delivery_manifest.intermediate_artifacts, [])
        self.assertIn("editable PPTX", " ".join(result.warnings))

    def test_system_selection_confirmation_uses_table_without_narrow_system_selection_column(self) -> None:
        summary = PptProductManager._format_system_selection_confirmation(
            {
                "system_type": "private_skill",
                "skill_name": "pptx",
                "output_format": "pptx",
                "reason": "使用 PPTX 模板 skill 生成可编辑 PPTX。",
            }
        )

        self.assertIn("### 系统选择", summary)
        self.assertIn("| 项目 | 当前值 |", summary)
        self.assertIn("| 制作系统 | 私有 PPT skill `pptx` |", summary)
        self.assertIn("| 输出方式 | pptx |", summary)
        self.assertIn("| 选择理由 | 使用 PPTX 模板 skill 生成可编辑 PPTX。 |", summary)
        self.assertNotIn("| 系统选择 |", summary)

    def test_route_registry_registers_all_routes(self) -> None:
        manager = PptProductManager()

        routes = manager.list_registered_routes()

        self.assertEqual(set(routes), {"html", "svg", "xml"})
        self.assertTrue(routes["html"]["implemented"])
        self.assertTrue(routes["svg"]["implemented"])
        self.assertFalse(routes["xml"]["implemented"])

    def test_content_planning_agent_exposes_material_tools(self) -> None:
        manager = PptProductManager()

        agent = manager.content_planner.build_agent()

        self.assertIsInstance(agent, LlmAgent)
        self.assertEqual(agent.name, "PptContentPlanningAgent")
        self.assertEqual(agent.output_key, "ppt_content_planning_agent_message")
        self.assertEqual(agent.include_contents, "none")
        self.assertEqual(
            {tool.__name__ for tool in agent.tools},
            {"read_ppt_markdown_sources", "save_ppt_deck_content_plan_markdown"},
        )
        self.assertIn("do not force cover, toc, chapter_start", agent.instruction)
        self.assertIn("template requirements only", agent.instruction)
        self.assertIn("Do not overuse `content`", agent.instruction)
        self.assertIn("`comparison` for tradeoffs", agent.instruction)

    def test_requirement_analysis_agent_saves_confirmed_requirement_json(self) -> None:
        manager = PptProductManager()

        agent = manager.build_requirement_analysis_agent()

        self.assertIsInstance(agent, LlmAgent)
        self.assertEqual(agent.name, "PptRequirementAnalysisAgent")
        self.assertEqual(agent.output_key, "ppt_requirement_analysis_agent_message")
        self.assertEqual(agent.include_contents, "none")
        self.assertEqual(
            {tool.__name__ for tool in agent.tools},
            {"save_ppt_confirmed_requirement_json"},
        )
        self.assertIn("ConfirmedRequirement JSON", agent.instruction)
        self.assertIn("multiple PPT systems", agent.instruction)
        self.assertIn("system-selection agent", agent.instruction)
        self.assertIn("source_inputs include PPTX/PPTM/POTX/POTM", agent.instruction)
        self.assertIn("Do not infer routes from keyword matching", agent.instruction)
        self.assertIn("受众为", agent.instruction)

    def test_system_selection_agent_chooses_without_keyword_rules(self) -> None:
        manager = PptProductManager()

        agent = manager.build_system_selection_agent()

        self.assertIsInstance(agent, LlmAgent)
        self.assertEqual(agent.name, "PptSystemSelectionAgent")
        self.assertEqual(agent.output_key, "ppt_system_selection_agent_message")
        self.assertEqual(
            {tool.__name__ for tool in agent.tools},
            {
                "list_product_ppt_skills",
                "read_product_ppt_skill",
                "save_ppt_system_selection",
            },
        )
        self.assertIn("Do not use hard-coded keyword rules", agent.instruction)
        self.assertIn("source_inputs include PPTX/PPTM/POTX/POTM", agent.instruction)
        self.assertIn("built-in `html` or `svg`", agent.instruction)

    def test_ppt_svg_strategy_tools_save_and_read_execution_plan(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={})

        strategy_result = manager.save_ppt_design_strategy(
            {
                "style_name": "clean_svg",
                "design_direction": "Use editable SVG primitives.",
                "palette": ["#FFFFFF", "#172033", "#2457D6"],
            },
            {
                "summary": "Use clean SVG route styling.",
                "decisions": ["Use 16:9"],
            },
            tool_context,
        )
        plan_result = manager.save_ppt_svg_execution_plan(
            {
                "aspect_ratio": "16:9",
                "canvas_width": 1280,
                "canvas_height": 720,
                "accent_color": "#2457D6",
            },
            tool_context,
        )
        read_result = manager.read_ppt_svg_execution_plan(tool_context)

        self.assertEqual(strategy_result["status"], "success")
        self.assertEqual(plan_result["status"], "success")
        self.assertEqual(read_result["status"], "success")
        self.assertEqual(tool_context.state["ppt_design_strategy"]["style_name"], "clean_svg")
        self.assertEqual(tool_context.state[PPT_SVG_EXECUTION_PLAN_STATE_KEY]["canvas_width"], 1280)

    def test_save_ppt_svg_execution_plan_preserves_route_guidance_fields(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(
            state={
                PPT_SVG_EXECUTION_PLAN_STATE_KEY: {
                    "aspect_ratio": "16:9",
                    "canvas_width": 1280,
                    "canvas_height": 720,
                    "page_rhythm_by_slide": {"P01": "anchor", "P02": "dense"},
                    "typography_ramp": {"page_title": 40, "body": 22},
                    "page_type_layout_guidance": {"comparison": "Use matched comparison lanes."},
                    "template_adherence_rules": {"content": "Preserve template header and footer."},
                    "quality_constraints": ["Respect page rhythm."],
                }
            }
        )

        result = manager.save_ppt_svg_execution_plan(
            {
                "aspect_ratio": "16:9",
                "canvas_width": 1280,
                "canvas_height": 720,
                "accent_color": "#FF0000",
                "page_rhythm_by_slide": {},
            },
            tool_context,
        )
        saved_plan = tool_context.state[PPT_SVG_EXECUTION_PLAN_STATE_KEY]

        self.assertEqual(result["status"], "success")
        self.assertEqual(saved_plan["accent_color"], "#FF0000")
        self.assertEqual(saved_plan["page_rhythm_by_slide"]["P02"], "dense")
        self.assertEqual(saved_plan["typography_ramp"]["body"], 22)
        self.assertIn("comparison", saved_plan["page_type_layout_guidance"])
        self.assertEqual(saved_plan["template_adherence_rules"]["content"], "Preserve template header and footer.")

    def test_content_planning_page_type_heuristics_use_richer_svg_types(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="做一个 SVG route PPT，对比两个方案并说明实施流程和关键指标。",
            inputs=[],
            output={"format": "pptx", "route": "svg"},
        )

        self.assertEqual(
            _select_content_page_type(
                requirement=requirement,
                point="方案 A 和方案 B 的成本收益对比",
                support="资源投入和风险差异明显",
                index=0,
                default="content",
            ),
            "comparison",
        )
        self.assertEqual(
            _select_content_page_type(
                requirement=requirement,
                point="实施流程分为需求确认、试点、推广三个步骤",
                support="每一步都有明确交付物",
                index=1,
                default="content",
            ),
            "process",
        )
        self.assertEqual(
            _select_content_page_type(
                requirement=requirement,
                point="核心指标提升 35%",
                support="效果已经超过目标",
                index=2,
                default="content",
            ),
            "stat",
        )

    def test_product_manager_skill_runner_masks_saved_long_html_arguments(self) -> None:
        manager = PptProductManager()
        long_html = "<!DOCTYPE html>" + ("x" * 9000)
        output_path = "generated/session/turn_3/ppt_private_skill_step_3/index.html"
        callback_context = SimpleNamespace(
            state={
                "ppt_private_skill_build": {
                    "output_path": output_path,
                }
            }
        )
        llm_request = SimpleNamespace(
            contents=[
                Content(
                    role="model",
                    parts=[
                        Part.from_function_call(
                            name="save_ppt_private_skill_html",
                            args={
                                "file_name": "index.html",
                                "html_content": long_html,
                                "description": "HTML deck",
                            },
                        )
                    ],
                )
            ]
        )

        self.assertEqual(manager.include_contents, "none")
        manager.before_model_callback(callback_context, llm_request)

        args = llm_request.contents[0].parts[0].function_call.args
        self.assertEqual(args["file_name"], "index.html")
        self.assertEqual(args["description"], "HTML deck")
        self.assertNotIn("<!DOCTYPE html>", args["html_content"])
        self.assertIn(output_path, args["html_content"])
        self.assertIn("<tool_output_masked>", args["html_content"])
        self.assertEqual(callback_context.state["ppt_private_skill_masked_html_content_count"], 1)

    def test_product_manager_skill_runner_keeps_short_html_arguments(self) -> None:
        manager = PptProductManager()
        short_html = "<!DOCTYPE html><html><body>Short</body></html>"
        callback_context = SimpleNamespace(state={})
        llm_request = SimpleNamespace(
            contents=[
                Content(
                    role="model",
                    parts=[
                        Part.from_function_call(
                            name="save_ppt_private_skill_html",
                            args={
                                "file_name": "index.html",
                                "html_content": short_html,
                            },
                        )
                    ],
                )
            ]
        )

        manager.before_model_callback(callback_context, llm_request)

        args = llm_request.contents[0].parts[0].function_call.args
        self.assertEqual(args["html_content"], short_html)
        self.assertNotIn("ppt_private_skill_masked_html_content_count", callback_context.state)

    def test_private_skill_execution_agent_is_not_exposed(self) -> None:
        manager = PptProductManager()

        self.assertFalse(hasattr(manager, "build_private_skill_execution_agent"))

    def test_requirement_analysis_save_tool_preserves_source_inputs(self) -> None:
        source_path = _write_markdown_source("requirement_source.pdf", "%PDF test fixture")
        manager = PptProductManager()
        fallback_requirement = manager.prepare_confirmed_requirement(
            task="针对这个素材，给我做一个ppt。",
            inputs={"files": [source_path]},
            output={"format": "pptx"},
        )
        tool_context = SimpleNamespace(
            state={
                "ppt_requirement_analysis_base": {
                    "fallback_requirement": fallback_requirement.model_dump(mode="json"),
                }
            }
        )

        result = manager.save_ppt_confirmed_requirement_json(
            {
                "route": "html",
                "topic": "视觉原语推理",
                "audience": "同组的同学",
                "scenario": "组会",
                "slide_count_policy": {
                    "minimum": 14,
                    "maximum": 16,
                    "target": 15,
                    "source": "user",
                },
                "source_inputs": [{"name": "invented.pdf", "path": "missing.pdf"}],
            },
            tool_context,
        )

        self.assertEqual(result["status"], "success")
        saved_requirement = tool_context.state["ppt_confirmed_requirement"]
        self.assertEqual(saved_requirement["topic"], "视觉原语推理")
        self.assertEqual(saved_requirement["audience"], "同组的同学")
        self.assertEqual(saved_requirement["scenario"], "组会")
        self.assertEqual(saved_requirement["slide_count_policy"]["target"], 15)
        self.assertEqual(saved_requirement["source_inputs"][0]["path"], source_path)

    def test_content_planning_user_message_includes_requirement_json(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="给我做一个ppt，用来给幼儿园小朋友讲英语单词。图文并茂。小于10页。",
            inputs=[],
            output={
                "format": "pptx",
                "language": "zh-CN",
                "slide_count": "小于10页",
                "style": "图文并茂、活泼可爱、适合儿童英语启蒙",
            },
        )

        user_message = _build_content_planning_user_message(requirement)

        self.assertIn("ConfirmedRequirement JSON", user_message)
        self.assertIn('"request_brief": "给我做一个ppt，用来给幼儿园小朋友讲英语单词。图文并茂。小于10页。"', user_message)
        self.assertIn('"topic": "英语单词"', user_message)
        self.assertIn('"audience": "幼儿园小朋友"', user_message)
        self.assertIn("Do not invent a generic business communication deck", user_message)

    def test_content_planning_tools_read_and_save_plan(self) -> None:
        source_path = _write_markdown_source(
            "planning_brief.md",
            "# Planning Brief\n\n- Activation rose after onboarding.\n",
        )
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="基于材料生成 5 页 PPTX。",
            inputs=[{"name": "planning_brief.md", "path": source_path}],
            output={"format": "pptx"},
            source_understanding=SourceUnderstanding(
                document_type="markdown",
                markdown_sources=[
                    {
                        "name": "planning_brief.md",
                        "source_path": source_path,
                        "method": "test",
                        "output_path": source_path,
                    }
                ],
            ),
        )
        tool_context = SimpleNamespace(
            state={
                "ppt_confirmed_requirement": requirement.model_dump(mode="json"),
            }
        )

        source_result = manager.content_planner.read_ppt_markdown_sources(tool_context)
        markdown_plan = """# Deck: Planning Brief
Audience: Internal team
Language: en
SlideCount: 5
Narrative: Explain activation changes.

## Slide 1 | cover | Planning Brief
Purpose: Introduce the planning brief.
Takeaway: Activation rose after onboarding.
Content:
- Audience: Growth team
Visual:
- placeholder | role=hero | description=clean title area

## Slide 2 | toc | Agenda
Purpose: Preview the deck.
Takeaway: The deck covers evidence and next steps.
Content:
- Activation
- Evidence
- Next steps
Visual:
- placeholder | role=list | description=agenda list

## Slide 3 | chapter_start | Activation
Purpose: Start the activation chapter.
Takeaway: Activation rose after onboarding.
Content:
- Activation rose after onboarding.
Visual:
- search | role=reference | query=activation onboarding chart | description=visual reference for activation onboarding

## Slide 4 | chapter_content | Evidence
Purpose: Explain the evidence.
Takeaway: Guided onboarding improved activation.
Content:
- Activation rose after onboarding.
- Enterprise teams need proof.
Visual:
- ai | role=supporting_visual | description=friendly product onboarding illustration

## Slide 5 | ending | Next Steps
Purpose: Close with next steps.
Takeaway: Use the activation proof in the story.
Content:
- Review the evidence
- Prepare the launch story
Visual:
- placeholder | role=summary | description=closing icon area
"""
        save_result = manager.content_planner.save_ppt_deck_content_plan_markdown(
            markdown_plan,
            tool_context,
        )

        self.assertEqual(source_result["status"], "success")
        self.assertIn("Activation rose", source_result["source_texts"][0]["text"])
        self.assertEqual(save_result["status"], "success")
        self.assertEqual(tool_context.state["ppt_deck_content_plan"]["title"], "Planning Brief")
        self.assertIn("ppt_deck_content_plan_markdown", tool_context.state)
        self.assertEqual(tool_context.state["ppt_deck_content_plan"]["pages"][2]["asset_source_preference"], "search")
        self.assertEqual(tool_context.state["ppt_deck_content_plan"]["pages"][3]["asset_source_preference"], "ai")

    def test_content_planning_rejects_off_task_kindergarten_business_plan(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="给我做一个ppt，用来给幼儿园小朋友讲英语单词。图文并茂。小于10页。",
            inputs=[],
            output={"format": "pptx"},
        )
        tool_context = SimpleNamespace(
            state={
                "ppt_confirmed_requirement": requirement.model_dump(mode="json"),
            }
        )
        bad_markdown_plan = """# Deck: 目标对齐沟通稿
Audience: 团队成员
Language: zh-CN
SlideCount: 6
Narrative: 通过清晰的背景、目标、关键信息和协作安排，帮助团队快速形成一致理解。

## Slide 1 | cover | 目标对齐沟通稿
Purpose: 建立主题氛围，说明本次沟通聚焦于统一理解与推进协作。
Takeaway: 团队需要先对目标与重点形成共同认知。
Content:
- 聚焦共同目标
- 明确核心信息
Visual:
- ai | role=hero | description=现代团队围绕简洁白板讨论目标，明亮办公空间，专业、清爽、无文字

## Slide 2 | toc | 内容一览
Purpose: 展示整体结构。
Takeaway: 本次内容将从背景、目标、重点和协作安排展开。
Content:
- 背景与目标
- 关键信息梳理
Visual:
- placeholder | role=grid | description=四段式目录布局

## Slide 3 | chapter_start | 第一部分：背景与目标
Purpose: 开启背景说明章节。
Takeaway: 明确背景是判断重点与行动方向的前提。
Content:
- 先看现状
- 再定方向
Visual:
- ai | role=hero | description=抽象路线图从起点延伸到目标旗帜，简洁商务插画风，无文字

## Slide 4 | chapter_content | 明确沟通对象
Purpose: 梳理受众关注点。
Takeaway: 面向不同对象时，信息重点与表达深度需要有所侧重。
Content:
- 识别主要听众与决策角色
- 提炼听众最关心的问题
Visual:
- placeholder | role=grid | description=人物角色卡片与关注点列表

## Slide 5 | ending | 后续协作
Purpose: 收束内容，推动会后形成明确协作节奏。
Takeaway: 共识需要转化为责任、时间和交付物。
Content:
- 确认负责人和参与方
- 明确近期交付物
Visual:
- ai | role=hero | description=团队成员把任务卡片贴到看板上，现代扁平插画，无文字
"""

        with self.assertRaisesRegex(ValueError, "kindergarten English-word task"):
            manager.content_planner.save_ppt_deck_content_plan_markdown(
                bad_markdown_plan,
                tool_context,
            )

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
        self.assertEqual(tool_context.state["ppt_route_build"]["template"]["template_id"], "free_design")
        self.assertTrue(result["output_files"])

    async def test_dispatch_ppt_route_injects_product_html_page_expert(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="做一个 5 页 PPTX 产品介绍。",
            inputs=[],
            output={"format": "pptx"},
        )
        content_plan = manager.build_initial_deck_content_plan(requirement)
        captured: dict[str, object] = {}

        async def _fake_build_html_route_with_agent(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(pptx_path="generated/test/deck.pptx")

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            with patch(
                "src.productions.ppt.ppt_product_manager.ppt_product_manager.build_html_route_with_agent",
                _fake_build_html_route_with_agent,
            ):
                result = await manager._dispatch_ppt_route(
                    requirement=requirement,
                    content_plan=content_plan,
                    output_dir=Path(tmpdir),
                    tool_context=SimpleNamespace(state={}),
                    expert_agents=manager.product_expert_agents,
                )

        self.assertEqual(result.pptx_path, "generated/test/deck.pptx")
        self.assertIs(
            captured["page_generation_agent"],
            manager.product_expert_agents[PPT_HTML_PAGE_GENERATION_EXPERT_NAME],
        )

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
        self.assertFalse(requirement.template_requirement.use_template)
        self.assertEqual(requirement.template_requirement.template_source, "none")
        self.assertEqual(requirement.editability_requirement.level, "high")
        self.assertFalse(requirement.confirmed_by_user)

    def test_prepare_confirmed_requirement_keeps_task_brief_without_documents(self) -> None:
        manager = PptProductManager()
        task = "给我做一个ppt，用来给幼儿园小朋友讲英语单词。图文并茂。小于10页。"

        requirement = manager.prepare_confirmed_requirement(
            task=task,
            inputs=[],
            output={"format": "pptx"},
        )

        self.assertEqual(requirement.request_brief, task)
        self.assertEqual(requirement.source_inputs, [])
        self.assertEqual(requirement.source_understanding.document_type, "brief")

    def test_prepare_confirmed_requirement_accepts_file_path_strings(self) -> None:
        source_path = _write_markdown_source("creative_agent_NeurIPS_2026_10_.pdf", "%PDF test fixture")
        manager = PptProductManager()

        requirement = manager.prepare_confirmed_requirement(
            task="针对这个素材，给我做一个ppt，用来组会上给团队的同学讲解。",
            inputs={"files": [source_path]},
            output={"format": "pptx", "language": "zh-CN", "purpose": "组会讲解"},
        )

        self.assertEqual(len(requirement.source_inputs), 1)
        self.assertEqual(requirement.source_inputs[0].name, "creative_agent_NeurIPS_2026_10_.pdf")
        self.assertEqual(requirement.source_inputs[0].path, source_path)
        self.assertEqual(requirement.source_understanding.document_type, "pdf")
        self.assertIn("| 输入材料 | 1 个 |", manager._format_requirement_confirmation(requirement))

    def test_prepare_confirmed_requirement_separates_task_documents_and_ignored_outline(self) -> None:
        source_path = _write_markdown_source("kid_words.md", "# Words\n\n- Apple\n")
        manager = PptProductManager()
        task = (
            "重新制作PPT：主题必须是“给幼儿园小朋友讲英语单词”，不是商务汇报。"
            "必须包含 Apple、Cat、Dog、Sun、Ball。"
        )

        requirement = manager.prepare_confirmed_requirement(
            task=task,
            inputs={
                "outline": [{"slide": 1, "title": "不要把这个当文档"}],
                "documents": [{"name": "kid_words.md", "path": source_path, "mime_type": "text/markdown"}],
            },
            output={
                "format": "pptx",
                "language": "zh-CN",
                "slide_count": 8,
                "style": "儿童友好、卡通、图文并茂、明亮柔和配色",
                "must_not_include": "商务、目标共识、推进路径、团队协作、行动计划",
            },
        )

        self.assertEqual(requirement.request_brief, task)
        self.assertEqual(requirement.topic, "英语单词")
        self.assertEqual(requirement.slide_count_policy.target, 8)
        self.assertEqual(requirement.language, "zh-CN")
        self.assertEqual(len(requirement.source_inputs), 1)
        self.assertEqual(requirement.source_inputs[0].name, "kid_words.md")
        self.assertEqual(requirement.source_understanding.document_type, "markdown")
        self.assertNotIn("business", requirement.style_requirement.style_keywords)
        self.assertIn("playful", requirement.style_requirement.style_keywords)
        self.assertIn("kid_friendly", requirement.style_requirement.style_keywords)
        self.assertIn("illustrated", requirement.style_requirement.style_keywords)

    def test_prepare_confirmed_requirement_extracts_public_topic_and_audience(self) -> None:
        manager = PptProductManager()

        requirement = manager.prepare_confirmed_requirement(
            task="给我做一个pptx，用于向大学文科学生科普ai",
            inputs=[],
            output={"format": "pptx"},
        )
        content_plan = manager.build_initial_deck_content_plan(requirement)

        self.assertEqual(requirement.topic, "AI科普")
        self.assertEqual(requirement.audience, "大学文科学生")
        self.assertNotIn("给我", requirement.topic)
        self.assertNotIn("pptx", requirement.topic.lower())
        self.assertNotIn("用于", content_plan.pages[0].title)
        self.assertNotIn("给我做", content_plan.pages[0].title)
        self.assertNotIn("pptx", content_plan.pages[0].title.lower())

    def test_prepare_confirmed_requirement_detects_illustrated_kid_word_deck(self) -> None:
        manager = PptProductManager()

        requirement = manager.prepare_confirmed_requirement(
            task="给我做一个ppt，用来给幼儿园小朋友讲英语单词。图文并茂。小于10页。",
            inputs=[],
            output={"format": "pptx"},
        )

        self.assertEqual(requirement.topic, "英语单词")
        self.assertEqual(requirement.audience, "幼儿园小朋友")
        self.assertEqual(requirement.slide_count_policy.maximum, 9)
        self.assertLessEqual(requirement.slide_count_policy.target, 9)
        self.assertIn("illustrated", requirement.style_requirement.style_keywords)
        self.assertIn("kid_friendly", requirement.style_requirement.style_keywords)
        self.assertIn("playful", requirement.style_requirement.style_keywords)

    def test_content_plan_honors_exact_kindergarten_word_pages(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="给我做一个ppt，用来给幼儿园小朋友讲英语单词。3页，分别讲 猫、狗、鸭子。",
            inputs=[],
            output={"format": "pptx"},
        )

        plan = manager.build_initial_deck_content_plan(requirement)

        self.assertEqual(requirement.slide_count_policy.target, 3)
        self.assertEqual(len(plan.pages), 3)
        self.assertEqual([page.page_type for page in plan.pages], ["content", "content", "content"])
        self.assertEqual([page.title for page in plan.pages], ["Cat 猫", "Dog 狗", "Duck 鸭子"])
        self.assertNotIn("cover", {page.page_type for page in plan.pages})
        self.assertNotIn("toc", {page.page_type for page in plan.pages})
        self.assertNotIn("chapter_start", {page.page_type for page in plan.pages})

    def test_prepare_confirmed_requirement_cleans_orchestrator_style_task(self) -> None:
        manager = PptProductManager()

        requirement = manager.prepare_confirmed_requirement(
            task=(
                "制作一个面向大学文科学生的AI科普PPTX，语言为中文，风格清晰现代、适合课堂/讲座使用。"
                "内容需帮助非理工背景学生理解AI：AI是什么、发展简史、核心概念。"
            ),
            inputs=[],
            output={"format": "pptx"},
        )

        self.assertEqual(requirement.topic, "AI科普")
        self.assertEqual(requirement.audience, "大学文科学生")
        self.assertEqual(requirement.scenario, "课堂/讲座")

    def test_prepare_confirmed_requirement_extracts_topic_from_given_audience_phrase(self) -> None:
        manager = PptProductManager()

        requirement = manager.prepare_confirmed_requirement(
            task="基于上传材料做一个给大学文科学生的AI科普PPTX",
            inputs={
                "outline": [{"slide": 1, "title": "not a document"}],
                "documents": [{"name": "brief.md", "path": "input/brief.md", "mime_type": "text/markdown"}],
            },
            output={"format": "pptx", "slide_count": 8},
        )

        self.assertEqual(requirement.topic, "AI科普")
        self.assertEqual(requirement.audience, "大学文科学生")
        self.assertEqual(len(requirement.source_inputs), 1)
        self.assertEqual(requirement.slide_count_policy.target, 8)

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
        self.assertEqual(requirement.template_requirement.template_path, "inbox/demo/template.pptx")
        self.assertEqual(requirement.editability_requirement.level, "native")

    def test_prepare_confirmed_requirement_defaults_xml_for_powerpoint_input(self) -> None:
        manager = PptProductManager()

        requirement = manager.prepare_confirmed_requirement(
            task="用这个模版给我做一个ppt，用于宣传我的花店。",
            inputs=[{"name": "flower-template.pptx", "path": "inbox/demo/flower-template.pptx"}],
            output={"format": "pptx"},
        )

        self.assertEqual(requirement.route, "xml")
        self.assertFalse(requirement.confirmed_by_user)
        self.assertTrue(requirement.template_requirement.use_template)
        self.assertEqual(requirement.template_requirement.template_source, "user")
        self.assertEqual(requirement.template_requirement.template_path, "inbox/demo/flower-template.pptx")
        self.assertEqual(requirement.editability_requirement.level, "native")

        selection = manager._build_default_system_selection(requirement)

        self.assertEqual(selection["system_type"], "private_skill")
        self.assertEqual(selection["route"], "xml")
        self.assertEqual(selection["skill_name"], "pptx")
        self.assertEqual(selection["output_format"], "pptx")

    def test_prepare_confirmed_requirement_explicit_route_overrides_powerpoint_input(self) -> None:
        manager = PptProductManager()

        requirement = manager.prepare_confirmed_requirement(
            task="用这个模版给我做一个ppt，用于宣传我的花店。",
            inputs=[{"name": "flower-template.pptx", "path": "inbox/demo/flower-template.pptx"}],
            output={"format": "pptx", "route": "html"},
        )

        self.assertEqual(requirement.route, "html")
        self.assertTrue(requirement.confirmed_by_user)
        self.assertFalse(requirement.template_requirement.use_template)

        selection = manager._build_default_system_selection(requirement)

        self.assertEqual(selection["system_type"], "built_in_route")
        self.assertEqual(selection["route"], "html")
        self.assertEqual(selection["skill_name"], "")

    def test_default_system_selection_uses_pptx_skill_for_user_templates(self) -> None:
        manager = PptProductManager()
        requirement = manager.prepare_confirmed_requirement(
            task="套用用户上传 PPTX 模板生成汇报。",
            inputs=[{"name": "template.pptx", "path": "inbox/demo/template.pptx"}],
            output={"route": "xml"},
        )

        selection = manager._build_default_system_selection(requirement)

        self.assertEqual(selection["system_type"], "private_skill")
        self.assertEqual(selection["skill_name"], "pptx")
        self.assertEqual(selection["output_format"], "pptx")

    async def test_run_generates_html_route_outputs_and_writes_state(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-manager-test", "turn_index": 1, "step": 1})

        result = await manager.run_product_request(
            task="生成一个 PPTX 产品介绍。",
            inputs=[],
            output={"format": "pptx", "auto_confirm": True},
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

    async def test_run_generates_svg_route_outputs_and_writes_state(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-manager-svg-test", "turn_index": 1, "step": 1})

        result = await manager.run_product_request(
            task="生成一个 3 页 PPTX 产品介绍，使用 SVG route。",
            inputs=[],
            output={"format": "pptx", "route": "svg", "slide_count": 3, "auto_confirm": True},
            tool_context=tool_context,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["selected_route"], "svg")
        self.assertEqual(result["phase"], "svg_route_delivery")
        self.assertEqual(len(result["route_build"]["svg_page_paths"]), len(result["deck_content_plan"]["pages"]))
        self.assertEqual(result["delivery_manifest"]["intermediate_artifacts"], result["route_build"]["svg_page_paths"])
        self.assertTrue(result["delivery_manifest"]["final_pptx"].endswith(".pptx"))
        self.assertEqual(tool_context.state["final_file_paths"], [result["delivery_manifest"]["final_pptx"]])
        self.assertIn("ppt_design_strategy", tool_context.state)
        self.assertIn("ppt_svg_execution_plan", tool_context.state)

        pptx_path = resolve_workspace_path(result["delivery_manifest"]["final_pptx"])
        svg_path = resolve_workspace_path(result["route_build"]["svg_page_paths"][0])
        quality_path = resolve_workspace_path(result["delivery_manifest"]["quality_report"])
        self.assertTrue(pptx_path.exists())
        self.assertTrue(svg_path.exists())
        self.assertTrue(quality_path.exists())
        self.assertEqual(len(Presentation(str(pptx_path)).slides), len(result["deck_content_plan"]["pages"]))

    async def test_run_can_deliver_private_skill_html_when_selector_chooses_skill(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-manager-private-skill-test", "turn_index": 1, "step": 1})

        def _content_plan_builder(_requirement):
            return DeckContentPlan(
                title="AI for Kids",
                core_narrative="Explain AI through familiar classroom examples.",
                pages=[
                    DeckPagePlan(
                        slide_number=1,
                        page_type="cover",
                        title="AI 是什么",
                        purpose="Introduce AI in simple language.",
                        key_takeaway="AI can help computers learn patterns.",
                        content_blocks=[{"items": ["AI 像一个会观察和练习的小助手。"]}],
                    ),
                    DeckPagePlan(
                        slide_number=2,
                        page_type="content",
                        title="AI 怎么学习",
                        purpose="Explain learning from examples.",
                        key_takeaway="Examples help AI get better.",
                        content_blocks=[{"items": ["看到很多图片后，AI 能学会分类。"]}],
                    ),
                ],
            )

        async def _selector(**_kwargs):
            return {
                "system_type": "private_skill",
                "route": "html",
                "skill_name": "ppt-complete-workflow",
                "output_format": "html",
                "reason": "Test selector chose the private PPT skill.",
            }

        result = await manager.run_product_request(
            task="给我做一个ppt，用来和小学生科普AI，不超过8页。用最合适的 skill 完成。",
            inputs=[],
            output={"format": "pptx", "auto_confirm": True},
            tool_context=tool_context,
            content_plan_builder=_content_plan_builder,
            system_selection_builder=_selector,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["phase"], "private_skill_delivery")
        self.assertEqual(result["selected_route"], "html")
        self.assertEqual(result["delivery_manifest"]["final_pptx"], "")
        self.assertEqual(tool_context.state["ppt_system_selection"]["system_type"], "private_skill")
        self.assertEqual(tool_context.state["ppt_system_selection"]["skill_name"], "ppt-complete-workflow")
        self.assertEqual(tool_context.state["active_product_ppt_skill"]["name"], "ppt-complete-workflow")
        self.assertIn("PptProductManager skill runner", tool_context.state["ppt_private_skill_execution_output"]["message"])
        self.assertIn("final_file_paths", tool_context.state)
        self.assertEqual(tool_context.state["final_file_paths"], result["delivery_manifest"]["intermediate_artifacts"])

        html_path = resolve_workspace_path(result["delivery_manifest"]["intermediate_artifacts"][0])
        self.assertTrue(html_path.exists())
        html_text = html_path.read_text(encoding="utf-8")
        self.assertIn("AI 是什么", html_text)
        self.assertIn("ppt-complete-workflow", html_text)

    async def test_pptx_private_skill_without_runner_does_not_fallback_to_html(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(
            state={"sid": "pptx-private-skill-failure-test", "turn_index": 1, "step": 1}
        )
        requirement = manager.prepare_confirmed_requirement(
            task="用这个模板做一个花店宣传 PPT。",
            inputs=[{"name": "template.pptx", "path": "inbox/demo/template.pptx"}],
            output={"format": "pptx", "route": "xml"},
        )
        content_plan = manager.build_initial_deck_content_plan(requirement)

        result = await manager.execute_private_ppt_skill(
            requirement=requirement,
            content_plan=content_plan,
            system_selection={
                "system_type": "private_skill",
                "route": "xml",
                "skill_name": "pptx",
                "output_format": "pptx",
                "reason": "Use pptx skill for uploaded template.",
            },
            tool_context=tool_context,
            expert_agents={},
            app_name="creative_claw",
            artifact_service=None,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["output_path"], "")
        self.assertEqual(result["output_format"], "pptx")
        self.assertNotIn("final_file_paths", tool_context.state)
        self.assertIn("ppt_private_skill_runtime_path", tool_context.state)
        self.assertTrue(resolve_workspace_path(tool_context.state["ppt_private_skill_runtime_path"]).exists())

    async def test_private_skill_recovers_generated_pptx_after_child_runner_error(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(
            state={
                "sid": "pptx-private-skill-child-error-test",
                "turn_index": 1,
                "step": 3,
            },
            _invocation_context=SimpleNamespace(
                app_name="creative_claw",
                user_id="test-user",
                plugin_manager=SimpleNamespace(plugins=[]),
                credential_service=None,
            ),
        )
        requirement = manager.prepare_confirmed_requirement(
            task="用这个模板做一个品牌故事 PPT。",
            inputs=[{"name": "template.pptx", "path": "inbox/demo/template.pptx"}],
            output={"format": "pptx", "route": "xml"},
        )
        content_plan = manager.build_initial_deck_content_plan(requirement)

        class _FailingChildRunner:
            closed = False

            async def run_async(self, **_kwargs):
                output_dir = resolve_workspace_path(tool_context.state["ppt_private_skill_output_dir"]) / "output"
                output_dir.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    prefix="deck_after_error_",
                    suffix=".pptx",
                    dir=output_dir,
                    delete=False,
                ) as handle:
                    pptx_name = handle.name
                Path(pptx_name).unlink()
                deck = Presentation()
                deck.slides.add_slide(deck.slide_layouts[0])
                deck.save(pptx_name)
                raise ValueError("optional QA failed after PPTX generation")
                yield None

            async def close(self):
                self.closed = True

        fake_runner = _FailingChildRunner()
        with patch(
            "src.productions.ppt.ppt_product_manager.ppt_product_manager._build_child_runner",
            return_value=fake_runner,
        ):
            result = await manager.execute_private_ppt_skill(
                requirement=requirement,
                content_plan=content_plan,
                system_selection={
                    "system_type": "private_skill",
                    "route": "xml",
                    "skill_name": "pptx",
                    "output_format": "pptx",
                    "reason": "Use pptx skill for uploaded template.",
                },
                tool_context=tool_context,
                expert_agents={},
                app_name="creative_claw",
                artifact_service=None,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["artifact_type"], "pptx")
        self.assertEqual(result["source"], "private_skill_pptx_recovery")
        self.assertIn("optional QA failed", result["execution_warning"])
        self.assertEqual(tool_context.state["final_file_paths"], [result["pptx_path"]])
        self.assertEqual(
            tool_context.state["ppt_private_skill_execution_output"]["status"],
            "success_with_warning",
        )
        self.assertTrue(resolve_workspace_path(result["pptx_path"]).exists())
        self.assertTrue(fake_runner.closed)

    async def test_interactive_workflow_pauses_for_two_confirmations(self) -> None:
        image_path = _write_test_image("interactive_kid_word_asset.png")
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-manager-interactive-test", "turn_index": 1, "step": 1})
        resolved_assets: list[str] = []

        async def _asset_resolver(asset, _page, _requirement):
            resolved_assets.append(asset.asset_id)
            return {
                "asset_id": asset.asset_id,
                "status": "ready",
                "path": image_path,
                "provider": "test_resolver",
            }

        requirement_result = await manager.run_product_request(
            task="给我做一个ppt，用来给幼儿园小朋友讲英语单词。3页，分别讲 猫、狗、鸭子。",
            inputs=[],
            output={"format": "pptx"},
            tool_context=tool_context,
            asset_resolver=_asset_resolver,
        )

        self.assertEqual(requirement_result["status"], "awaiting_requirement_confirmation")
        self.assertEqual(tool_context.state["ppt_workflow_state"]["stage"], "awaiting_requirement_confirmation")
        self.assertNotIn("final_file_paths", tool_context.state)
        self.assertIn("summary_markdown", requirement_result["confirmation_request"])
        self.assertIn("### 系统选择", requirement_result["confirmation_request"]["summary_markdown"])
        self.assertEqual(resolved_assets, [])
        self.assertEqual(tool_context.state["ppt_workflow_state"]["waiting_since_turn_index"], 1)

        same_turn_requirement_result = await manager.continue_product_request(
            user_response="确认",
            tool_context=tool_context,
            asset_resolver=_asset_resolver,
        )

        self.assertEqual(same_turn_requirement_result["status"], "awaiting_requirement_confirmation")
        self.assertEqual(tool_context.state["ppt_workflow_state"]["stage"], "awaiting_requirement_confirmation")
        self.assertEqual(resolved_assets, [])

        tool_context.state["turn_index"] = 2
        plan_result = await manager.continue_product_request(
            user_response="确认",
            tool_context=tool_context,
            asset_resolver=_asset_resolver,
        )

        self.assertEqual(plan_result["status"], "awaiting_content_plan_confirmation")
        self.assertEqual(tool_context.state["ppt_workflow_state"]["stage"], "awaiting_content_plan_confirmation")
        self.assertEqual([page["title"] for page in plan_result["deck_content_plan"]["pages"]], ["Cat 猫", "Dog 狗", "Duck 鸭子"])
        self.assertNotIn("### 系统选择", plan_result["confirmation_request"]["summary_markdown"])
        self.assertEqual(resolved_assets, [])
        self.assertEqual(tool_context.state["ppt_workflow_state"]["waiting_since_turn_index"], 2)

        same_turn_plan_result = await manager.continue_product_request(
            user_response="确认",
            tool_context=tool_context,
            asset_resolver=_asset_resolver,
        )

        self.assertEqual(same_turn_plan_result["status"], "awaiting_content_plan_confirmation")
        self.assertEqual(tool_context.state["ppt_workflow_state"]["stage"], "awaiting_content_plan_confirmation")
        self.assertEqual(resolved_assets, [])
        self.assertNotIn("final_file_paths", tool_context.state)

        tool_context.state["turn_index"] = 3
        final_result = await manager.continue_product_request(
            user_response="确认",
            tool_context=tool_context,
            asset_resolver=_asset_resolver,
        )

        self.assertEqual(final_result["status"], "success")
        self.assertEqual(tool_context.state["ppt_workflow_state"]["stage"], "completed")
        self.assertGreaterEqual(len(resolved_assets), 1)
        self.assertTrue(final_result["delivery_manifest"]["final_pptx"].endswith(".pptx"))
        self.assertEqual(tool_context.state["final_file_paths"], [final_result["delivery_manifest"]["final_pptx"]])

    async def test_interactive_workflow_allows_revision_on_later_turn(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-manager-revision-test", "turn_index": 1, "step": 1})

        await manager.run_product_request(
            task="给我做一个ppt，用来给幼儿园小朋友讲英语单词。3页，分别讲 猫、狗、鸭子。",
            inputs=[],
            output={"format": "pptx"},
            tool_context=tool_context,
        )

        tool_context.state["turn_index"] = 2
        plan_result = await manager.continue_product_request(
            user_response="确认",
            tool_context=tool_context,
        )
        self.assertEqual(plan_result["status"], "awaiting_content_plan_confirmation")

        tool_context.state["turn_index"] = 3
        revised_result = await manager.continue_product_request(
            user_response="把第 2 页改成兔子。",
            tool_context=tool_context,
        )

        self.assertEqual(revised_result["status"], "awaiting_content_plan_confirmation")
        self.assertEqual(tool_context.state["ppt_workflow_state"]["stage"], "awaiting_content_plan_confirmation")
        self.assertEqual(tool_context.state["ppt_workflow_state"]["waiting_since_turn_index"], 3)
        self.assertIn("Content plan revision", tool_context.state["ppt_workflow_state"]["confirmed_requirement"]["request_brief"])
        self.assertNotIn("final_file_paths", tool_context.state)

    async def test_requirement_confirmation_revision_updates_structured_fields(self) -> None:
        source_path = _write_markdown_source("Thinking_with_Visual_Primitives.pdf", "%PDF test fixture")
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-manager-requirement-revision-test", "turn_index": 1, "step": 1})

        initial_result = await manager.run_product_request(
            task=(
                "针对这个素材，做一个用于组会和同学讲解的 PPT。"
                "素材为论文/文档 Thinking_with_Visual_Primitives.pdf，需要提炼内容。"
            ),
            inputs={"files": [source_path]},
            output={"format": "pptx", "language": "zh-CN", "use_case": "组会讲解"},
            tool_context=tool_context,
        )

        self.assertEqual(initial_result["status"], "awaiting_requirement_confirmation")
        self.assertEqual(initial_result["confirmed_requirement"]["scenario"], "组会")
        self.assertNotIn("User revision", initial_result["confirmed_requirement"]["topic"])
        self.assertEqual(len(initial_result["confirmed_requirement"]["source_inputs"]), 1)

        tool_context.state["turn_index"] = 2
        page_count_result = await manager.continue_product_request(
            user_response="页数改成15页左右。",
            tool_context=tool_context,
        )

        self.assertEqual(page_count_result["status"], "awaiting_requirement_confirmation")
        requirement_after_page_count = page_count_result["confirmed_requirement"]
        self.assertEqual(requirement_after_page_count["slide_count_policy"]["target"], 15)
        self.assertEqual(requirement_after_page_count["source_inputs"][0]["path"], source_path)
        self.assertNotIn("User revision", requirement_after_page_count["topic"])
        self.assertNotIn("User revision", requirement_after_page_count["request_brief"])

        tool_context.state["turn_index"] = 3
        audience_result = await manager.continue_product_request(
            user_response="受众为同组的同学，场景为组会。",
            tool_context=tool_context,
        )

        self.assertEqual(audience_result["status"], "awaiting_requirement_confirmation")
        revised_requirement = audience_result["confirmed_requirement"]
        self.assertEqual(revised_requirement["audience"], "同组的同学")
        self.assertEqual(revised_requirement["scenario"], "组会")
        self.assertEqual(revised_requirement["slide_count_policy"]["target"], 15)
        self.assertEqual(revised_requirement["source_inputs"][0]["path"], source_path)
        self.assertNotIn("User revision", revised_requirement["topic"])
        self.assertIn("| 受众 | 同组的同学 |", audience_result["confirmation_request"]["summary_markdown"])
        self.assertIn("| 场景 | 组会 |", audience_result["confirmation_request"]["summary_markdown"])

    async def test_requirement_confirmation_stages_external_upload_before_source_conversion(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temp_dir:
            external_pdf = Path(temp_dir) / "Thinking_with_Visual_Primitives.pdf"
            external_pdf.write_bytes(b"%PDF external upload fixture")

            manager = PptProductManager()
            tool_context = SimpleNamespace(
                state={
                    "sid": "ppt-manager-external-upload-test",
                    "channel": "web",
                    "turn_index": 1,
                    "step": 1,
                }
            )
            captured: dict[str, str] = {}

            async def _capturing_source_converter(source_input, parameters: dict) -> dict:
                captured["source_input_path"] = source_input.path
                captured["input_path"] = parameters["input_path"]
                return await _fake_source_converter(source_input, parameters)

            await manager.run_product_request(
                task="针对这个素材，给我做一个ppt，用于组会和同学讲解。",
                inputs=[str(external_pdf)],
                output={"format": "pptx", "language": "zh-CN", "usage": "组会讲解"},
                tool_context=tool_context,
            )

            tool_context.state["turn_index"] = 2
            result = await manager.continue_product_request(
                user_response="确认",
                tool_context=tool_context,
                source_converter=_capturing_source_converter,
            )

        self.assertEqual(result["status"], "awaiting_content_plan_confirmation")
        self.assertNotEqual(captured["input_path"], str(external_pdf))
        self.assertTrue(captured["input_path"].startswith("inbox/web/ppt-manager-external-upload-test/turn_2/"))
        self.assertTrue(resolve_workspace_path(captured["input_path"]).exists())
        self.assertEqual(result["confirmed_requirement"]["source_inputs"][0]["path"], captured["source_input_path"])
        self.assertEqual(result["confirmed_requirement"]["source_inputs"][0]["path"], captured["input_path"])

    async def test_requirement_confirmation_downloads_remote_url_before_source_conversion(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(
            state={
                "sid": "ppt-manager-remote-source-test",
                "channel": "web",
                "turn_index": 1,
                "step": 1,
            }
        )
        source_url = "https://arxiv.org/pdf/1706.03762"
        captured: dict[str, str] = {}

        async def _capturing_source_converter(source_input, parameters: dict) -> dict:
            captured["source_input_path"] = source_input.path
            captured["input_path"] = parameters["input_path"]
            captured["has_url_parameter"] = str("url" in parameters)
            return await _fake_source_converter(source_input, parameters)

        await manager.run_product_request(
            task="针对这个论文，给我做一个ppt，用于组会和同学讲解。",
            inputs=[{"name": "1706.03762", "url": source_url}],
            output={"format": "pptx", "language": "zh-CN", "usage": "组会讲解"},
            tool_context=tool_context,
        )

        tool_context.state["turn_index"] = 2
        fake_response = _FakeRemoteResponse(
            b"%PDF-1.4\nremote pdf fixture\n",
            {"content-type": "application/pdf", "content-length": "28"},
        )
        with patch(
            "src.productions.ppt.ppt_product_manager.ppt_product_manager.urlopen",
            return_value=fake_response,
        ):
            result = await manager.continue_product_request(
                user_response="确认",
                tool_context=tool_context,
                source_converter=_capturing_source_converter,
            )

        self.assertEqual(result["status"], "awaiting_content_plan_confirmation")
        self.assertEqual(captured["has_url_parameter"], "False")
        self.assertTrue(captured["input_path"].startswith("generated/ppt-manager-remote-source-test/turn_2/"))
        self.assertTrue(captured["input_path"].endswith(".pdf"))
        self.assertTrue(resolve_workspace_path(captured["input_path"]).exists())
        self.assertEqual(result["confirmed_requirement"]["source_inputs"][0]["path"], captured["source_input_path"])
        self.assertEqual(result["confirmed_requirement"]["source_inputs"][0]["path"], captured["input_path"])
        self.assertEqual(tool_context.state["ppt_remote_source_downloads"][0]["source_url"], source_url)

    async def test_run_returns_deferred_status_for_unimplemented_xml_route(self) -> None:
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-manager-test", "turn_index": 1, "step": 1})

        result = await manager.run_product_request(
            task="使用 xml route 生成一份增长策略汇报。",
            inputs=[],
            output={"route": "xml", "auto_confirm": True},
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
            output={"format": "pptx", "auto_confirm": True},
            tool_context=tool_context,
            source_converter=_fake_source_converter,
        )

        self.assertEqual(result["status"], "success")
        source_materials = result["confirmed_requirement"]["source_understanding"]
        self.assertEqual(source_materials["markdown_sources"][0]["name"], "launch_brief.md")
        self.assertEqual(source_materials["figures"][0]["alt"], "Activation chart")
        ready_assets = [
            asset
            for page in result["deck_content_plan"]["pages"]
            for asset in page.get("assets", [])
            if asset.get("status") == "ready"
        ]
        self.assertEqual(ready_assets[0]["source_kind"], "material_figure")
        self.assertTrue(ready_assets[0]["path"].endswith("activation.png"))
        self.assertEqual(tool_context.state["ppt_resolved_asset_manifest"]["ready_asset_count"], 1)
        plan_text = str(result["deck_content_plan"])
        self.assertIn("prepared source materials", plan_text)
        self.assertIn("ppt_source_markdown_sources", tool_context.state)
        self.assertIn("ppt_source_figures", tool_context.state)
        self.assertTrue(tool_context.state["ppt_source_output_files"])
        self.assertEqual(tool_context.state["current_output"]["product_line"], "ppt")
        self.assertEqual(tool_context.state["current_output"]["status"], "success")

        html_path = resolve_workspace_path(result["delivery_manifest"]["intermediate_artifacts"][0])
        html_text = html_path.read_text(encoding="utf-8")
        self.assertIn("Growth Launch", html_text)
        self.assertIn("Activation chart", html_text)

        pptx_path = resolve_workspace_path(result["delivery_manifest"]["final_pptx"])
        pptx_text = "\n".join(
            shape.text
            for slide in Presentation(str(pptx_path)).slides
            for shape in slide.shapes
            if getattr(shape, "has_text_frame", False)
        )
        self.assertIn("Growth Launch", pptx_text)
        self.assertIn("Use the provided figures", pptx_text)
        picture_count = sum(
            1
            for slide in Presentation(str(pptx_path)).slides
            for shape in slide.shapes
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE
        )
        self.assertGreaterEqual(picture_count, 1)

    async def test_run_converts_web_uploaded_file_path_strings(self) -> None:
        source_path = _write_markdown_source("creative_agent_NeurIPS_2026_10_.pdf", "%PDF test fixture")
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-manager-web-upload-test", "turn_index": 2, "step": 1})

        result = await manager.run_product_request(
            task="针对这个素材，给我做一个ppt，用来组会上给团队的同学讲解。",
            inputs={"files": [source_path]},
            output={"format": "pptx", "language": "zh-CN", "purpose": "组会讲解", "auto_confirm": True},
            tool_context=tool_context,
            source_converter=_fake_source_converter,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["confirmed_requirement"]["source_inputs"][0]["path"], source_path)
        source_materials = result["confirmed_requirement"]["source_understanding"]
        self.assertEqual(source_materials["document_type"], "pdf")
        self.assertEqual(source_materials["markdown_sources"][0]["name"], "creative_agent_NeurIPS_2026_10_.pdf")
        self.assertTrue(tool_context.state["ppt_source_markdown_sources"])

    async def test_run_uses_existing_markdown_source_without_anything_to_md(self) -> None:
        source_path = _write_markdown_source(
            "local_markdown_brief.md",
            """# Local Markdown Brief

- Retention improved after guided onboarding.
- Sales teams need a simple proof-led deck.
""",
        )
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-manager-local-md-test", "turn_index": 1, "step": 1})

        result = await manager.run_product_request(
            task="基于本地 Markdown 生成 6 页 PPTX。",
            inputs=[{"name": "local_markdown_brief.md", "path": source_path}],
            output={"format": "pptx", "auto_confirm": True},
            tool_context=tool_context,
        )

        self.assertEqual(result["status"], "success")
        source_materials = result["confirmed_requirement"]["source_understanding"]
        self.assertEqual(source_materials["markdown_sources"][0]["method"], "local:markdown_passthrough")
        self.assertEqual(source_materials["markdown_sources"][0]["output_path"], source_path)
        self.assertIn("ppt_markdown_source_texts", tool_context.state)
        self.assertIn("Retention improved", str(result["deck_content_plan"]))

        pptx_path = resolve_workspace_path(result["delivery_manifest"]["final_pptx"])
        pptx_text = "\n".join(
            shape.text
            for slide in Presentation(str(pptx_path)).slides
            for shape in slide.shapes
            if getattr(shape, "has_text_frame", False)
        )
        self.assertIn("Retention improved", pptx_text)

    async def test_content_planning_resolves_pending_generated_asset_before_route(self) -> None:
        image_path = _write_test_image("generated_asset_fixture.png")
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-manager-asset-test", "turn_index": 1, "step": 1})

        def _content_plan_builder(_requirement):
            plan = DeckContentPlan(
                title="AI for Kids",
                core_narrative="Explain AI through concrete classroom examples.",
                pages=[
                    _page(1, "cover"),
                    _page(2, "toc"),
                    _page(3, "chapter_start"),
                    _page(4, "chapter_content"),
                    _page(5, "ending"),
                ],
            )
            plan.pages[3].asset_source_preference = "ai"
            plan.pages[3].assets = [
                DeckPageAsset(
                    asset_id="slide_04_ai_visual",
                    source_kind="image_generation",
                    status="pending",
                    description="A friendly classroom illustration showing students learning AI.",
                    prompt="A friendly classroom illustration showing students learning AI.",
                )
            ]
            return plan

        async def _asset_resolver(asset, _page, _requirement):
            return {
                "asset_id": asset.asset_id,
                "status": "ready",
                "path": image_path,
                "provider": "test_resolver",
            }

        result = await manager.run_product_request(
            task="给小学生做一个 AI 科普 PPTX。",
            inputs=[],
            output={"format": "pptx", "auto_confirm": True},
            tool_context=tool_context,
            content_plan_builder=_content_plan_builder,
            asset_resolver=_asset_resolver,
        )

        self.assertEqual(result["status"], "success")
        resolved_asset = result["deck_content_plan"]["pages"][3]["assets"][0]
        self.assertEqual(resolved_asset["status"], "ready")
        self.assertEqual(resolved_asset["path"], image_path)
        self.assertEqual(tool_context.state["ppt_resolved_asset_manifest"]["ready_asset_count"], 1)
        progress_events = list(tool_context.state.get("orchestration_events") or [])
        image_generation_events = [
            event for event in progress_events if event.get("title") == "PPT Image Generation"
        ]
        self.assertEqual(len(image_generation_events), 2)
        self.assertIn("Status: started", image_generation_events[0]["detail"])
        self.assertIn("Status: success", image_generation_events[1]["detail"])
        self.assertIn("slide_04_ai_visual", image_generation_events[1]["detail"])

        pptx_path = resolve_workspace_path(result["delivery_manifest"]["final_pptx"])
        picture_count = sum(
            1
            for slide in Presentation(str(pptx_path)).slides
            for shape in slide.shapes
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE
        )
        self.assertGreaterEqual(picture_count, 1)

    async def test_illustrated_kid_word_deck_generates_plan_assets(self) -> None:
        image_path = _write_test_image("kid_word_generated_asset.png")
        manager = PptProductManager()
        tool_context = SimpleNamespace(state={"sid": "ppt-manager-kid-word-test", "turn_index": 1, "step": 1})

        async def _asset_resolver(asset, _page, _requirement):
            return {
                "asset_id": asset.asset_id,
                "status": "ready",
                "path": image_path,
                "provider": "test_resolver",
            }

        result = await manager.run_product_request(
            task="给我做一个ppt，用来给幼儿园小朋友讲英语单词。图文并茂。小于10页。",
            inputs=[],
            output={"format": "pptx", "auto_confirm": True},
            tool_context=tool_context,
            asset_resolver=_asset_resolver,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["confirmed_requirement"]["topic"], "英语单词")
        self.assertEqual(result["confirmed_requirement"]["audience"], "幼儿园小朋友")
        self.assertLessEqual(result["confirmed_requirement"]["slide_count_policy"]["target"], 9)

        pages = result["deck_content_plan"]["pages"]
        page_titles = [page["title"] for page in pages]
        self.assertIn("Apple 苹果", page_titles)
        self.assertIn("Cat 猫", page_titles)
        self.assertIn("Dog 狗", page_titles)
        self.assertNotIn("Context", page_titles)
        self.assertNotIn("Insight", page_titles)
        self.assertNotIn("Next Steps", page_titles)
        self.assertNotIn("No source file", str(pages))
        self.assertNotIn("ContentPlanningAgent", str(pages))

        ai_pages = [page for page in pages if page["asset_source_preference"] == "ai"]
        self.assertGreaterEqual(len(ai_pages), 1)
        self.assertTrue(all(page["page_type"] == "content" for page in pages))
        self.assertTrue(all(page["asset_source_preference"] == "ai" for page in pages))

        ready_assets = [
            asset
            for page in pages
            for asset in page.get("assets", [])
            if asset.get("status") == "ready"
        ]
        self.assertGreaterEqual(len(ready_assets), 1)
        self.assertTrue(all(asset["source_kind"] == "image_generation" for asset in ready_assets))
        self.assertGreaterEqual(tool_context.state["ppt_resolved_asset_manifest"]["ready_asset_count"], 1)

        pptx_path = resolve_workspace_path(result["delivery_manifest"]["final_pptx"])
        picture_count = sum(
            1
            for slide in Presentation(str(pptx_path)).slides
            for shape in slide.shapes
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE
        )
        self.assertGreaterEqual(picture_count, 1)

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

    def test_deck_content_plan_allows_no_template_page_types(self) -> None:
        plan = DeckContentPlan(
            title="Demo deck",
            core_narrative="A concise direct narrative.",
            pages=[
                _page(1, "content"),
                _page(2, "quote"),
                _page(3, "activity"),
            ],
        )

        self.assertEqual(len(plan.pages), 3)
        self.assertEqual({page.page_type for page in plan.pages}, {"content", "quote", "activity"})

        with self.assertRaisesRegex(ValueError, "duplicate slide numbers"):
            DeckContentPlan(
                title="Broken deck",
                core_narrative="Duplicate slide numbers.",
                pages=[
                    _page(1, "content"),
                    _page(1, "content"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
