import asyncio
import json
import unittest
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

from google.adk import Context, Workflow
from google.adk.agents import LlmAgent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.sessions import State
from google.adk.workflow import node
from google.genai.types import Content, FunctionCall, Part
from pydantic import PrivateAttr

from src.productions.design.design_product_manager import (
    DESIGN_BRIEF_FORM_SCHEMA_VERSION,
    DESIGN_PRODUCT_EXPERT_ALLOWLIST,
    DESIGN_PRODUCT_RESULT_SCHEMA_VERSION,
    DesignCodeGenerationAgent,
    DesignBriefFormExpert,
    build_design_code_generation_constraints,
    build_design_code_generation_prompt,
    DesignProductManager,
    DesignProductRequest,
    DesignProductResult,
    ProductDesignSkillRegistry,
    normalize_question_form_block,
    parse_form_answers,
    validate_question_form_schema,
)
from src.productions.design.design_product_manager.design_product_manager import (
    DESIGN_PRODUCT_REQUEST_STATE_KEY,
    DESIGN_PRODUCT_RESULT_STATE_KEY,
)
from src.productions.design.design_product_manager.brief_form import (
    DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY,
    DESIGN_BRIEF_FORM_STATE_KEY,
    _build_design_brief_form_prompt,
)
from src.productions.design.design_systems import list_design_systems
from src.runtime.workspace import resolve_workspace_path
from src.skills.registry import SkillRegistry


class _DesignProductManagerToolCallingFakeLlm(BaseLlm):
    """Fake design product model that drives a planned tool sequence."""

    _function_calls: list[FunctionCall] = PrivateAttr()
    _final_text: str = PrivateAttr()
    _requests: list[LlmRequest] = PrivateAttr(default_factory=list)

    def __init__(
        self,
        *,
        function_calls: list[FunctionCall],
        final_text: str,
    ) -> None:
        super().__init__(model="fake-design-product-manager")
        self._function_calls = function_calls
        self._final_text = final_text

    @property
    def requests(self) -> list[LlmRequest]:
        return self._requests

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        self._requests.append(llm_request)
        call_index = len(self._requests) - 1
        if call_index < len(self._function_calls):
            yield LlmResponse(
                content=Content(
                    role="model",
                    parts=[Part(function_call=self._function_calls[call_index])],
                )
            )
            return
        yield LlmResponse(
            content=Content(role="model", parts=[Part(text=self._final_text)])
        )


def _function_declaration_names(llm_request: LlmRequest) -> list[str]:
    names: list[str] = []
    for tool in llm_request.config.tools or []:
        for declaration in tool.function_declarations or []:
            if declaration.name:
                names.append(declaration.name)
    return names


def _design_system_option_ids(question: dict) -> list[str]:
    available_ids = {summary.id for summary in list_design_systems()}
    option_ids = [
        option["value"]
        for option in question["options"]
        if option["value"] != "decide_for_me"
    ]
    assert len(option_ids) == len(set(option_ids))
    assert set(option_ids).issubset(available_ids)
    return option_ids


class DesignProductManagerTests(unittest.TestCase):
    def test_design_product_manager_is_llm_agent_with_private_tools(self) -> None:
        manager = DesignProductManager()

        self.assertIsInstance(manager, LlmAgent)
        self.assertEqual(manager.name, "DesignProductManager")
        self.assertEqual(manager.include_contents, "none")
        self.assertEqual(
            {tool.__name__ for tool in manager.tools},
            {
                "list_product_design_skills",
                "read_product_design_skill",
                "list_design_experts",
                "invoke_design_expert",
                "invoke_design_code_generation",
                "emit_design_progress",
                "save_design_artifact",
                "validate_design_artifact",
                "register_design_delivery",
            },
        )
        self.assertIn("private product-design skills", manager.instruction)
        self.assertIn("invoke_design_code_generation", manager.instruction)
        self.assertIn("DesignCodeGenerationAgent", manager.instruction)
        self.assertIn("CodeGenerationExpert", manager.instruction)
        self.assertIn("default and preferred producer", manager.instruction)
        self.assertIn("ImageGenerationAgent output is normally an intermediate asset", manager.instruction)
        self.assertIn("Use AnythingToMD only for user-provided source documents", manager.instruction)
        self.assertIn("especially PDFs, scanned or image-heavy documents", manager.instruction)
        self.assertIn("Do not use AnythingToMD for HTML, TXT, Markdown, or ordinary image files", manager.instruction)
        self.assertIn("do not use AnythingToMD to inspect or verify generated design outputs", manager.instruction)
        self.assertIn("friendly aliases", manager.instruction)
        self.assertIn("do not pass alias keys", manager.instruction)
        self.assertIn("Do not use `save_design_artifact` to create the main final HTML", manager.instruction)
        self.assertIn("register_design_delivery", manager.instruction)
        self.assertIn("HTML is the tool, not the medium", manager.instruction)
        self.assertIn("interaction designer posture", manager.instruction)
        self.assertIn("systems designer posture", manager.instruction)
        self.assertIn("style exploration", manager.instruction)
        self.assertIn("comparison canvas", manager.instruction)
        self.assertIn("brief/design-system artboard", manager.instruction)
        self.assertIn("SCREENS", manager.instruction)
        self.assertIn("VARIANTS", manager.instruction)
        self.assertIn("philosophy, hierarchy, execution, specificity, and restraint", manager.instruction)

    def test_design_product_request_schema_preserves_public_input_shape(self) -> None:
        request = DesignProductRequest.model_validate(
            {
                "task": "  设计一个产品页。 ",
                "inputs": {
                    "product_image": "generated/product-bear.png",
                    "model_glb": {"path": "generated/product-bear.glb"},
                },
                "output": None,
            }
        )

        self.assertEqual(request.task, "设计一个产品页。")
        self.assertEqual(
            request.normalized_inputs(),
            {
                "product_image": "generated/product-bear.png",
                "model_glb": {"path": "generated/product-bear.glb"},
            },
        )
        self.assertEqual(
            request.to_state_dict(),
            {
                "task": "设计一个产品页。",
                "inputs": {
                    "product_image": "generated/product-bear.png",
                    "model_glb": {"path": "generated/product-bear.glb"},
                },
                "output": {},
            },
        )

    def test_design_product_result_schema_builds_stable_dict_contract(self) -> None:
        result = DesignProductResult.model_validate(
            {
                "status": " success ",
                "product_line": "other",
                "message": "  设计产物已完成。 ",
                "final_file_paths": ["generated/design.html"],
            }
        ).to_result_dict()

        self.assertEqual(result["result_schema_version"], DESIGN_PRODUCT_RESULT_SCHEMA_VERSION)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["product_line"], "design")
        self.assertEqual(result["message"], "设计产物已完成。")
        self.assertEqual(result["final_file_paths"], ["generated/design.html"])
        self.assertEqual(result["progress"], [])
        self.assertEqual(result["active_skill"], {})
        self.assertEqual(result["output_files"], [])

    def test_design_code_generation_prompt_uses_design_canvas_contract(self) -> None:
        prompt = build_design_code_generation_prompt("Design a mobile ordering flow.")

        self.assertIn("Design Medium Posture", prompt)
        self.assertIn("Visual Direction Framework", prompt)
        self.assertIn("Style Exploration and Variants", prompt)
        self.assertIn("Multi-Direction Canvas Protocol", prompt)
        self.assertIn("Code Organization for Reviewable Design Artifacts", prompt)
        self.assertIn("Quality Self-Check Before Finalizing", prompt)
        self.assertIn("editorial_monocle", prompt)
        self.assertIn("modern_minimal", prompt)
        self.assertIn("brutalist_experimental", prompt)
        self.assertIn("brief/design-system artboard", prompt)
        self.assertIn("Default to 3 directions", prompt)
        self.assertIn("SCREENS", prompt)
        self.assertIn("VARIANTS", prompt)
        self.assertIn("same domain data", prompt)
        self.assertIn("differ in more than color", prompt)
        self.assertIn("design canvas", prompt.lower())
        self.assertIn("artboards", prompt)
        self.assertIn("not a production application", prompt)
        self.assertIn("stable section, artboard, and component identifiers", prompt)
        self.assertIn("DesignCanvas", prompt)
        self.assertIn("DCViewport", prompt)
        self.assertIn("translate3d(x, y, 0) scale(scale)", prompt)
        self.assertIn("trackpad pinch zoom", prompt)
        self.assertIn("__dc_present", prompt)
        self.assertIn("__dc_set_zoom", prompt)
        self.assertIn("Design a mobile ordering flow.", prompt)

    def test_design_code_generation_constraints_include_embedded_canvas_behavior(self) -> None:
        constraints = build_design_code_generation_constraints(["show three variants"])

        self.assertIn("Embed a DesignCanvas/DCViewport-style scaffold for the main design surface.", constraints)
        self.assertIn(
            "Use transform-based canvas pan/zoom instead of browser-window scrolling for the main design board.",
            constraints,
        )
        self.assertIn("Support host zoom synchronization messages: __dc_present, __dc_zoom, and __dc_set_zoom.", constraints)
        self.assertIn(
            "For style exploration, include a brief/design-system artboard and 2-3 visible direction sections with the same screens/content.",
            constraints,
        )
        self.assertIn(
            "Keep multi-direction variants aligned by information architecture while making the visual systems meaningfully different.",
            constraints,
        )
        self.assertIn(
            "Use named local structures such as SCREENS, VARIANTS, shared domain data, and stable artboard ids.",
            constraints,
        )
        self.assertIn("show three variants", constraints)

    def test_private_product_design_skill_registry_lists_standard_skill_folders(self) -> None:
        registry = ProductDesignSkillRegistry()

        skills = registry.list_skills()
        skill_names = {skill.name for skill in skills}

        self.assertIn("design-canvas-artifact", skill_names)
        self.assertNotIn("poster-page-designer", skill_names)
        self.assertIn("Design Canvas Artifact", registry.read_skill("design-canvas-artifact"))

    def test_global_skill_registry_does_not_expose_private_product_design_skills(self) -> None:
        global_registry = SkillRegistry()

        skill_names = {skill.name for skill in global_registry.list_skills()}

        self.assertNotIn("poster-page-designer", skill_names)
        self.assertNotIn("product-design-skills", skill_names)
        self.assertNotIn("product-page-skills", skill_names)

    def test_private_skill_tools_list_and_read_skills(self) -> None:
        manager = DesignProductManager()
        tool_context = SimpleNamespace(state={})

        listed = manager.list_product_design_skills(tool_context)
        read = manager.read_product_design_skill("design-canvas-artifact", tool_context)

        self.assertEqual(listed["status"], "success")
        self.assertGreaterEqual(listed["count"], 1)
        self.assertEqual(read["status"], "success")
        self.assertEqual(read["name"], "design-canvas-artifact")
        self.assertIn("Design Canvas Artifact", read["content"])
        self.assertEqual(tool_context.state["active_product_design_skill"]["name"], "design-canvas-artifact")

    def test_design_brief_question_form_schema_helpers(self) -> None:
        expert = DesignBriefFormExpert()

        self.assertIsInstance(expert, LlmAgent)
        self.assertEqual(expert.include_contents, "none")
        self.assertIn("cross-task common question framework", expert.instruction)
        self.assertIn("default coverage framework", expert.instruction)
        self.assertIn("up to 5 task-specific questions", expert.instruction)
        self.assertIn("design_system_picker", expert.instruction)
        self.assertIn("design_system_reference", expert.instruction)
        self.assertIn("exactly 6 recommended design systems", expert.instruction)
        self.assertIn("content, mode, workflow, platform", expert.instruction)
        self.assertIn("visual style, color, design system", expert.instruction)
        self.assertIn("visual_direction", expert.instruction)
        self.assertIn("style_exploration", expert.instruction)
        self.assertIn("explore_2_3_directions", expert.instruction)
        self.assertNotIn("Never exceed 7 questions", expert.instruction)

        form = validate_question_form_schema(
            {
                "id": "design-brief",
                "title": "确认设计需求",
                "questions": [
                    {
                        "id": "tone",
                        "label": "视觉调性",
                        "type": "multi_choice",
                        "required": True,
                        "maxSelections": 2,
                        "allowOther": True,
                        "options": [
                            {"value": "decide_for_me", "label": "为我决定"},
                            {"value": "minimal", "label": "极简"},
                            {"value": "editorial", "label": "杂志风"},
                        ],
                    },
                    {
                        "id": "screen_count",
                        "label": "想看几个屏幕？",
                        "type": "range",
                        "required": False,
                        "min": 3,
                        "max": 12,
                        "default": 6,
                    },
                    {
                        "id": "design_system_reference",
                        "label": "设计系统参考？",
                        "type": "single_choice",
                        "presentation": "design_system_picker",
                        "resource": "design_systems",
                        "required": False,
                        "allowOther": True,
                        "options": [
                            {"value": "decide_for_me", "label": "为我决定"},
                        ],
                    },
                ],
            }
        )
        block = normalize_question_form_block(
            f"<cc-question-form>{json.dumps(form, ensure_ascii=False)}</cc-question-form>"
        )
        fenced_block = normalize_question_form_block(
            f"<cc-question-form>\n```json\n{json.dumps(form, ensure_ascii=False)}\n```\n</cc-question-form>"
        )
        answers = parse_form_answers(
            '[cc-form-answers id="design-brief" version="design-brief-form-v1"]\n'
            '{"tone":["minimal"]}\n'
            "[/cc-form-answers]"
        )
        question_by_id = {question["id"]: question for question in form["questions"]}

        self.assertEqual(form["version"], DESIGN_BRIEF_FORM_SCHEMA_VERSION)
        self.assertTrue(question_by_id["tone"]["allowOther"])
        self.assertEqual(question_by_id["tone"]["options"][-1]["value"], "decide_for_me")
        self.assertEqual(question_by_id["screen_count"]["type"], "range")
        self.assertEqual(question_by_id["screen_count"]["default"], 6)
        self.assertEqual(question_by_id["design_system_reference"]["presentation"], "design_system_picker")
        self.assertEqual(question_by_id["design_system_reference"]["resource"], "design_systems")
        self.assertEqual(len(question_by_id["design_system_reference"]["options"]), 7)
        self.assertEqual(question_by_id["design_system_reference"]["options"][-1]["value"], "decide_for_me")
        self.assertEqual(len(_design_system_option_ids(question_by_id["design_system_reference"])), 6)
        self.assertIn("<cc-question-form>", block)
        self.assertIn("<cc-question-form>", fenced_block)
        self.assertEqual(answers["id"], "design-brief")
        self.assertEqual(answers["answers"]["tone"], ["minimal"])

    def test_design_brief_question_form_allows_more_than_seven_questions(self) -> None:
        questions = [
            {
                "id": f"question_{index}",
                "label": f"问题 {index}",
                "type": "single_choice",
                "required": False,
                "options": [
                    {"value": "decide_for_me", "label": "为我决定"},
                    {"value": "custom", "label": "自定义"},
                ],
            }
            for index in range(1, 10)
        ]

        form = validate_question_form_schema(
            {
                "id": "expanded-design-brief",
                "title": "确认设计需求",
                "questions": questions,
            }
        )

        self.assertEqual(len(form["questions"]), 10)
        self.assertEqual(form["questions"][-1]["id"], "design_system_reference")
        self.assertEqual(form["questions"][-1]["presentation"], "design_system_picker")
        self.assertEqual(form["questions"][-1]["resource"], "design_systems")
        for question in form["questions"]:
            if question["type"] in {"single_choice", "multi_choice"}:
                self.assertEqual(question["options"][-1]["value"], "decide_for_me")
        self.assertEqual(len(_design_system_option_ids(form["questions"][-1])), 6)

    def test_design_brief_question_form_uses_supplied_interaction_language(self) -> None:
        prompt = _build_design_brief_form_prompt(
            "Create a single-file HTML design for a multi-center clinical trial dashboard.",
            interaction_language="en",
        )
        form = validate_question_form_schema(
            {
                "id": "clinical-trial-design-brief",
                "version": DESIGN_BRIEF_FORM_SCHEMA_VERSION,
                "uiLanguage": "en",
                "title": "Confirm design requirements",
                "questions": [
                    {
                        "id": "goal",
                        "label": "What should the design emphasize?",
                        "type": "short_text",
                        "required": True,
                    }
                ],
            }
        )
        design_system_question = form["questions"][-1]

        self.assertIn("Use English for every user-facing form title", prompt)
        self.assertIn('Set `uiLanguage` to "en"', prompt)
        self.assertEqual(form["uiLanguage"], "en")
        self.assertEqual(form["submitLabel"], "Confirm and continue")
        self.assertEqual(
            design_system_question["label"],
            "Which design system should this reference?",
        )
        self.assertEqual(design_system_question["options"][-1]["label"], "Decide for me")

    def test_design_brief_question_form_orders_content_before_style_questions(self) -> None:
        form = validate_question_form_schema(
            {
                "id": "ordered-design-brief",
                "title": "确认设计需求",
                "questions": [
                    {
                        "id": "visual_style",
                        "label": "视觉风格偏好？",
                        "type": "single_choice",
                        "required": False,
                        "options": [
                            {"value": "decide_for_me", "label": "为我决定"},
                            {"value": "minimal", "label": "现代极简"},
                        ],
                    },
                    {
                        "id": "color_direction",
                        "label": "色彩方向？",
                        "type": "single_choice",
                        "required": False,
                        "options": [
                            {"value": "decide_for_me", "label": "为我决定"},
                            {"value": "warm", "label": "暖色"},
                        ],
                    },
                    {
                        "id": "scope_modules",
                        "label": "希望包含哪些核心页面？",
                        "type": "multi_choice",
                        "required": False,
                        "options": [
                            {"value": "decide_for_me", "label": "为我决定"},
                            {"value": "home", "label": "首页"},
                        ],
                    },
                    {
                        "id": "brand_notes",
                        "label": "品牌名、参考 App 或其他要求",
                        "type": "long_text",
                        "required": False,
                    },
                    {
                        "id": "primary_workflow",
                        "label": "主要使用场景？",
                        "type": "single_choice",
                        "required": False,
                        "options": [
                            {"value": "decide_for_me", "label": "为我决定"},
                            {"value": "browse_order", "label": "浏览并下单"},
                        ],
                    },
                    {
                        "id": "design_system_reference",
                        "label": "设计系统参考？",
                        "type": "single_choice",
                        "presentation": "design_system_picker",
                        "resource": "design_systems",
                        "required": False,
                        "allowOther": True,
                        "options": [
                            {"value": "decide_for_me", "label": "为我决定"},
                            {"value": "claude", "label": "Claude", "description": "适合克制温和的产品界面。"},
                        ],
                    },
                ],
            }
        )

        question_ids = [question["id"] for question in form["questions"]]

        self.assertEqual(question_ids[:2], ["scope_modules", "primary_workflow"])
        self.assertEqual(question_ids[-1], "brand_notes")
        self.assertLess(question_ids.index("primary_workflow"), question_ids.index("visual_style"))
        self.assertLess(question_ids.index("color_direction"), question_ids.index("design_system_reference"))
        self.assertLess(question_ids.index("design_system_reference"), question_ids.index("brand_notes"))

    def test_design_brief_question_form_normalizes_design_system_question(self) -> None:
        form = validate_question_form_schema(
            {
                "id": "design-brief",
                "title": "确认设计需求",
                "questions": [
                    {
                        "id": "visual_style",
                        "label": "视觉风格？",
                        "type": "single_choice",
                        "required": False,
                        "options": [
                            {"value": "decide_for_me", "label": "为我决定"},
                            {"value": "minimal", "label": "极简"},
                        ],
                    },
                    {
                        "id": "style_library",
                        "label": "设计系统参考？",
                        "type": "single_choice",
                        "presentation": "design_system_picker",
                        "required": True,
                        "options": [
                            {"value": "claude", "label": "Claude", "description": "适合温和、克制的产品体验。"},
                            {"value": "not-real", "label": "Invalid"},
                        ],
                    },
                ],
            }
        )

        design_system_question = form["questions"][1]
        self.assertEqual(design_system_question["id"], "design_system_reference")
        self.assertEqual(design_system_question["type"], "single_choice")
        self.assertEqual(design_system_question["presentation"], "design_system_picker")
        self.assertEqual(design_system_question["resource"], "design_systems")
        self.assertFalse(design_system_question["required"])
        self.assertTrue(design_system_question["allowOther"])
        self.assertIn(
            {"value": "decide_for_me", "label": "为我决定"},
            design_system_question["options"],
        )
        option_ids = _design_system_option_ids(design_system_question)
        self.assertEqual(len(option_ids), 6)
        self.assertEqual(option_ids[0], "claude")
        self.assertEqual(design_system_question["options"][-1]["value"], "decide_for_me")
        self.assertNotIn("not-real", option_ids)
        self.assertTrue(
            all(
                option.get("description")
                for option in design_system_question["options"]
                if option["value"] != "decide_for_me"
            )
        )

    def test_private_design_expert_tools_list_allowlist_and_reject_other_experts(self) -> None:
        manager = DesignProductManager()
        tool_context = SimpleNamespace(state={})

        listed = manager.list_design_experts(tool_context)
        rejected = asyncio.run(
            manager.invoke_design_expert(
                agent_name="VideoGenerationAgent",
                prompt="{}",
                tool_context=tool_context,
            )
        )

        self.assertEqual(listed["status"], "success")
        self.assertEqual(
            [expert["name"] for expert in listed["experts"]],
            list(DESIGN_PRODUCT_EXPERT_ALLOWLIST),
        )
        self.assertEqual(rejected["status"], "error")
        self.assertIn("Allowed experts", rejected["message"])

    def test_invoke_design_expert_uses_shared_dispatcher(self) -> None:
        manager = DesignProductManager()
        manager._expert_agents = {"CodeGenerationExpert": object()}
        tool_context = SimpleNamespace(state={})
        expected_tool_result = {
            "agent_name": "CodeGenerationExpert",
            "status": "success",
            "message": "generated",
            "output_files": [{"path": "generated/design.html"}],
        }
        mocked_dispatch = AsyncMock(
            return_value=SimpleNamespace(tool_result=expected_tool_result)
        )

        with patch(
            "src.productions.design.design_product_manager.design_product_manager.dispatch_expert_request",
            new=mocked_dispatch,
        ):
            result = asyncio.run(
                manager.invoke_design_expert(
                    agent_name="CodeGenerationExpert",
                    prompt='{"prompt":"Build a landing page","language":"html"}',
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result, expected_tool_result)
        mocked_dispatch.assert_awaited_once()
        forwarded_request = mocked_dispatch.await_args.args[0]
        self.assertEqual(forwarded_request.agent_name, "CodeGenerationExpert")
        self.assertEqual(
            forwarded_request.prompt,
            '{"prompt":"Build a landing page","language":"html"}',
        )
        self.assertEqual(
            tool_context.state["design_product_last_expert_result"],
            expected_tool_result,
        )
        self.assertEqual(tool_context.state["design_product_generation"], expected_tool_result)

    def test_invoke_design_expert_resolves_input_aliases(self) -> None:
        manager = DesignProductManager()
        manager._expert_agents = {"ImageUnderstandingAgent": object()}
        tool_context = SimpleNamespace(
            state={
                DESIGN_PRODUCT_REQUEST_STATE_KEY: {
                    "inputs": {
                        "product_image": "generated/product-bear.png",
                    }
                }
            }
        )
        expected_tool_result = {
            "agent_name": "ImageUnderstandingAgent",
            "status": "success",
            "message": "analyzed",
            "output_files": [],
        }
        mocked_dispatch = AsyncMock(
            return_value=SimpleNamespace(tool_result=expected_tool_result)
        )

        with patch(
            "src.productions.design.design_product_manager.design_product_manager.dispatch_expert_request",
            new=mocked_dispatch,
        ):
            result = asyncio.run(
                manager.invoke_design_expert(
                    agent_name="ImageUnderstandingAgent",
                    prompt='{"input_path":"product_image","mode":"all"}',
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result, expected_tool_result)
        forwarded_prompt = json.loads(mocked_dispatch.await_args.args[0].prompt)
        self.assertEqual(forwarded_prompt["input_path"], "generated/product-bear.png")

    def test_invoke_design_code_generation_uses_private_agent(self) -> None:
        manager = DesignProductManager()
        tool_context = SimpleNamespace(
            state={
                "sid": "design-private-codegen-test",
                "turn_index": 1,
                "step": 2,
                "expert_step": 0,
            }
        )
        expected_output = {
            "status": "success",
            "message": "Generated html code at generated/design-canvas.html.",
            "output_path": "generated/design-canvas.html",
            "output_files": [
                {
                    "path": "generated/design-canvas.html",
                    "description": "Design artifact generated by DesignCodeGenerationAgent.",
                    "source": "design_code_generation_agent",
                }
            ],
            "language": "html",
            "error_type": "",
            "retryable": False,
            "raw_error_summary": "",
            "warnings": [],
        }
        mocked_generation = AsyncMock(return_value=expected_output)

        with patch.object(DesignCodeGenerationAgent, "run_generation", new=mocked_generation):
            result = asyncio.run(
                manager.invoke_design_code_generation(
                    prompt="Build a Claude Design-style mobile app design canvas.",
                    output_path="generated/design-canvas.html",
                    context_files=["skills/product-design-skills/design-canvas-artifact/SKILL.md"],
                    constraints=["show three variants"],
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result["status"], "success")
        mocked_generation.assert_awaited_once()
        self.assertEqual(
            tool_context.state["design_product_last_code_generation_result"]["status"],
            "success",
        )
        self.assertEqual(tool_context.state["design_product_generation"]["language"], "html")
        self.assertEqual(tool_context.state["new_files"][0]["source"], "design_code_generation_agent")

    def test_invoke_design_code_generation_resolves_context_file_aliases(self) -> None:
        manager = DesignProductManager()
        tool_context = SimpleNamespace(
            state={
                DESIGN_PRODUCT_REQUEST_STATE_KEY: {
                    "inputs": {
                        "product_image": "generated/product-bear.png",
                    }
                }
            }
        )
        expected_output = {
            "status": "success",
            "message": "Generated html code at generated/design-canvas.html.",
            "output_path": "generated/design-canvas.html",
            "output_files": [],
            "language": "html",
            "error_type": "",
            "retryable": False,
            "raw_error_summary": "",
            "warnings": [],
        }
        mocked_generation = AsyncMock(return_value=expected_output)

        with patch.object(DesignCodeGenerationAgent, "run_generation", new=mocked_generation):
            result = asyncio.run(
                manager.invoke_design_code_generation(
                    prompt="Build a product page.",
                    context_files=["product_image"],
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            mocked_generation.await_args.kwargs["context_files"],
            ["generated/product-bear.png"],
        )

    def test_invoke_design_code_generation_injects_selected_design_system_state(self) -> None:
        manager = DesignProductManager()
        tool_context = SimpleNamespace(
            state={
                "design_product_selected_design_system": {
                    "id": "claude",
                    "title": "Claude",
                    "body": "# Claude\n\nAuthoritative palette and typography rules.",
                }
            }
        )
        expected_output = {
            "status": "success",
            "message": "Generated html code at generated/design-canvas.html.",
            "output_path": "generated/design-canvas.html",
            "output_files": [],
            "language": "html",
            "error_type": "",
            "retryable": False,
            "raw_error_summary": "",
            "warnings": [],
        }
        mocked_generation = AsyncMock(return_value=expected_output)

        with patch.object(DesignCodeGenerationAgent, "run_generation", new=mocked_generation):
            result = asyncio.run(
                manager.invoke_design_code_generation(
                    prompt="Build a compact design canvas.",
                    output_path="generated/design-canvas.html",
                    constraints=["Use visible artboards."],
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result["status"], "success")
        mocked_generation.assert_awaited_once()
        kwargs = mocked_generation.await_args.kwargs
        self.assertIn("# Selected design system (authoritative DESIGN.md)", kwargs["prompt"])
        self.assertIn("Design system: Claude (claude)", kwargs["prompt"])
        self.assertIn("Authoritative palette and typography rules.", kwargs["prompt"])
        self.assertIn(
            "Use the selected design system Claude (claude) as the authoritative visual system.",
            kwargs["constraints"],
        )

    def test_progress_save_validate_and_register_delivery_tools(self) -> None:
        manager = DesignProductManager()
        tool_context = SimpleNamespace(
            state={
                "sid": "design-product-manager-test",
                "turn_index": 1,
                "step": 0,
                "expert_step": 0,
            }
        )

        progress = manager.emit_design_progress(
            stage="design_planning",
            status="started",
            message="Choosing private design skill.",
            tool_context=tool_context,
        )
        saved = manager.save_design_artifact(
            file_name="artifact.html",
            content="""<!doctype html>
<html lang="en">
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    main { display: grid; gap: 16px; }
    @media (max-width: 720px) { main { display: flex; flex-direction: column; } }
  </style>
</head>
<body>
  <main>
    <section>
      <h1>Private Skill Design</h1>
      <p>This generated design artifact has enough visible text and semantic structure for validation.</p>
    </section>
  </main>
</body>
</html>""",
            description="Test design artifact.",
            tool_context=tool_context,
        )
        output_path = saved["output_path"]
        validation = manager.validate_design_artifact(
            paths=[output_path],
            browser_preview=False,
            tool_context=tool_context,
        )
        delivery = manager.register_design_delivery(
            status="success",
            reply_text="设计产物已完成。",
            final_file_paths=[output_path],
            tool_context=tool_context,
        )

        self.assertEqual(progress["status"], "success")
        self.assertEqual(saved["status"], "success")
        self.assertTrue(resolve_workspace_path(output_path).exists())
        self.assertEqual(validation["status"], "success")
        self.assertEqual(delivery["result_schema_version"], DESIGN_PRODUCT_RESULT_SCHEMA_VERSION)
        self.assertEqual(delivery["status"], "success")
        self.assertEqual(delivery["final_file_paths"], [output_path])
        self.assertEqual(tool_context.state["final_file_paths"], [output_path])
        self.assertEqual(tool_context.state["design_product_result"]["message"], "设计产物已完成。")

    def test_run_product_request_requires_adk_invocation_context(self) -> None:
        manager = DesignProductManager()
        tool_context = SimpleNamespace(state={})

        result = asyncio.run(
            manager.run_product_request(
                task="设计一个 landing page。",
                tool_context=tool_context,
            )
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("ADK invocation context", result["message"])

    def test_run_product_request_rejects_non_object_output(self) -> None:
        manager = DesignProductManager()
        tool_context = SimpleNamespace(state={})

        result = asyncio.run(
            manager.run_product_request(
                task="设计一个 landing page。",
                output=["html"],
                tool_context=tool_context,
            )
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("output to be an object", result["message"])

    def test_run_product_request_emits_start_progress_for_valid_design_request(self) -> None:
        manager = DesignProductManager()
        tool_context = SimpleNamespace(
            state={"channel": "web", "sid": "design-progress-test", "turn_index": 1},
            _invocation_context=SimpleNamespace(user_id="user-1"),
        )
        form_message = (
            "<cc-question-form>\n"
            '{"id":"design-brief","version":"design-brief-form-v1","title":"确认需求","questions":['
            '{"id":"goal","label":"目标","type":"short_text","required":true}'
            "]}\n"
            "</cc-question-form>"
        )

        with patch.object(DesignBriefFormExpert, "generate_form", new=AsyncMock(return_value=form_message)):
            result = asyncio.run(
                manager.run_product_request(
                    task="设计一个活动海报 HTML。",
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(tool_context.state["orchestration_events"][0]["title"], "Design Product")
        self.assertEqual(tool_context.state["orchestration_events"][0]["stage"], "design_product")
        self.assertIn(
            "DesignProductManager is working on the design request.",
            tool_context.state["orchestration_events"][0]["detail"],
        )

    def test_web_design_product_request_returns_generated_brief_form_first(self) -> None:
        manager = DesignProductManager()
        tool_context = SimpleNamespace(
            state={"channel": "web"},
            _invocation_context=SimpleNamespace(user_id="user-1"),
        )
        form_message = (
            "<cc-question-form>\n"
            '{"id":"design-brief","version":"design-brief-form-v1","title":"确认需求","questions":['
            '{"id":"goal","label":"目标","type":"short_text","required":true}'
            "]}\n"
            "</cc-question-form>"
        )

        with patch.object(DesignBriefFormExpert, "generate_form", new=AsyncMock(return_value=form_message)):
            result = asyncio.run(
                manager.run_product_request(
                    task="设计一个 AI 产品落地页。",
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result["status"], "needs_input")
        self.assertIn("<cc-question-form>", result["message"])
        self.assertEqual(
            tool_context.state["design_product_brief_form_pending_task"],
            "设计一个 AI 产品落地页。",
        )
        self.assertEqual(tool_context.state["final_file_paths"], [])

    def test_web_design_product_request_uses_workflow_for_real_adk_context(self) -> None:
        form_message = (
            "<cc-question-form>\n"
            '{"id":"design-brief","version":"design-brief-form-v1","title":"确认需求","questions":['
            '{"id":"goal","label":"目标","type":"short_text","required":true}'
            "]}\n"
            "</cc-question-form>"
        )
        fake_llm = _DesignProductManagerToolCallingFakeLlm(
            function_calls=[],
            final_text=form_message,
        )
        manager = DesignProductManager()
        manager._brief_form_expert = DesignBriefFormExpert(model=fake_llm)
        session_service = InMemorySessionService()
        artifact_service = InMemoryArtifactService()

        @node(name="DesignBriefFormWorkflowHarnessNode", rerun_on_resume=True)
        async def design_harness(ctx: Context, node_input: str) -> dict:
            return await manager.run_product_request(
                task="设计一个 AI 产品落地页。",
                tool_context=ctx,
                app_name="creative_claw",
                artifact_service=artifact_service,
            )

        workflow = Workflow(
            name="DesignBriefFormWorkflowHarness",
            edges=[("START", design_harness)],
        )
        runner = Runner(
            node=workflow,
            session_service=session_service,
            artifact_service=artifact_service,
        )
        user_id = "user-design-brief-workflow"
        session_id = "session-design-brief-workflow"

        async def _run():
            try:
                await session_service.create_session(
                    app_name=workflow.name,
                    user_id=user_id,
                    session_id=session_id,
                    state={"channel": "web"},
                )
                async for _ in runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=Content(role="user", parts=[Part(text="Run design brief form")]),
                ):
                    pass
                return await session_service.get_session(
                    app_name=workflow.name,
                    user_id=user_id,
                    session_id=session_id,
                )
            finally:
                await runner.close()

        with patch.object(
            DesignBriefFormExpert,
            "generate_form",
            new=AsyncMock(side_effect=AssertionError("fallback generate_form should not run")),
        ):
            session = asyncio.run(_run())

        self.assertIsNotNone(session)
        self.assertEqual(session.state["design_product_result"]["status"], "needs_input")
        self.assertEqual(session.state[DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY], "设计一个 AI 产品落地页。")
        self.assertIn("<cc-question-form>", session.state[DESIGN_BRIEF_FORM_STATE_KEY]["message"])
        self.assertIn("design_system_reference", session.state[DESIGN_BRIEF_FORM_STATE_KEY]["message"])
        self.assertEqual(session.state["final_file_paths"], [])
        self.assertGreaterEqual(len(fake_llm.requests), 1)
        self.assertIn("# Available design system catalog", fake_llm.requests[0].contents[0].parts[0].text)

    def test_run_product_request_preserves_dict_inputs_for_child_runner(self) -> None:
        manager = DesignProductManager()
        result_payload = {
            "result_schema_version": DESIGN_PRODUCT_RESULT_SCHEMA_VERSION,
            "status": "success",
            "product_line": "design",
            "message": "设计产物已完成。",
            "final_file_paths": [],
            "progress": [],
            "active_skill": {},
            "experts": [],
            "expert_history": [],
            "last_expert_result": {},
            "code_generation_history": [],
            "last_code_generation_result": {},
            "generation": {},
            "validation": [],
            "output_files": [],
        }
        tool_context = SimpleNamespace(
            state=State({"channel": "cli"}, {}),
            _invocation_context=SimpleNamespace(user_id="user-1"),
        )
        captured = {}

        async def _fake_agent_tool_transport(**kwargs):
            captured["message"] = kwargs["request"]
            captured["request_inputs"] = kwargs["initial_state"][DESIGN_PRODUCT_REQUEST_STATE_KEY]["inputs"]
            captured["request_output"] = kwargs["initial_state"][DESIGN_PRODUCT_REQUEST_STATE_KEY]["output"]
            tool_context.state[DESIGN_PRODUCT_RESULT_STATE_KEY] = result_payload
            tool_context.state["final_response"] = result_payload["message"]
            tool_context.state["final_file_paths"] = []

        inputs = {
            "product_image": "generated/product-bear.png",
            "model_glb": {
                "path": "generated/product-bear.glb",
                "kind": "3d_model",
            },
        }
        with patch(
            "src.productions.design.design_product_manager.design_product_manager._run_design_product_agent_tool",
            _fake_agent_tool_transport,
        ):
            result = asyncio.run(
                manager.run_product_request(
                    task="设计一个产品售卖网页。",
                    inputs=inputs,
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(captured["request_inputs"], inputs)
        self.assertEqual(captured["request_output"], {})
        self.assertIn('"product_image": "generated/product-bear.png"', captured["message"])
        self.assertIn('"path": "generated/product-bear.glb"', captured["message"])
        self.assertNotIn("['product_image'", captured["message"])

    def test_run_product_request_agenttool_main_path_registers_delivery(self) -> None:
        fake_llm = _DesignProductManagerToolCallingFakeLlm(
            function_calls=[
                FunctionCall(
                    name="register_design_delivery",
                    args={
                        "status": "success",
                        "reply_text": "设计产物已完成。",
                        "final_file_paths": [],
                    },
                )
            ],
            final_text="DesignProductManager registered the design delivery.",
        )
        manager = DesignProductManager(model=fake_llm)
        session_service = InMemorySessionService()
        artifact_service = InMemoryArtifactService()

        @node(name="DesignProductAgentToolHarnessNode", rerun_on_resume=True)
        async def design_harness(ctx: Context, node_input: str) -> dict:
            return await manager.run_product_request(
                task="设计一个 SaaS 设置页。",
                inputs={"reference_image": "generated/reference.png"},
                output={"format": "html"},
                tool_context=ctx,
                app_name="creative_claw",
                artifact_service=artifact_service,
            )

        workflow = Workflow(
            name="DesignProductAgentToolHarness",
            edges=[("START", design_harness)],
        )
        runner = Runner(
            node=workflow,
            session_service=session_service,
            artifact_service=artifact_service,
        )
        user_id = "user-design-agenttool"
        session_id = "session-design-agenttool"

        async def _run():
            try:
                await session_service.create_session(
                    app_name=workflow.name,
                    user_id=user_id,
                    session_id=session_id,
                    state={"channel": "cli"},
                )
                async for _ in runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=Content(role="user", parts=[Part(text="Run design product")]),
                ):
                    pass
                return await session_service.get_session(
                    app_name=workflow.name,
                    user_id=user_id,
                    session_id=session_id,
                )
            finally:
                await runner.close()

        session = asyncio.run(_run())

        self.assertIsNotNone(session)
        self.assertEqual(session.state["design_product_result"]["status"], "success")
        self.assertEqual(session.state["design_product_result"]["message"], "设计产物已完成。")
        self.assertEqual(session.state["current_output"]["status"], "success")
        self.assertEqual(session.state["final_file_paths"], [])
        self.assertNotIn("DesignProductManager failed", session.state["design_product_result"]["message"])
        first_request_tools = _function_declaration_names(fake_llm.requests[0])
        self.assertIn("register_design_delivery", first_request_tools)
        self.assertIn("invoke_design_code_generation", first_request_tools)
        self.assertGreaterEqual(len(fake_llm.requests), 2)

    def test_submitted_web_form_answers_clear_pending_brief_form_state(self) -> None:
        manager = DesignProductManager()
        answer_block = (
            '[cc-form-answers id="design-brief" version="design-brief-form-v1"]\n'
            '{"visual_direction":"decide_for_me"}\n'
            "[/cc-form-answers]"
        )
        result_payload = {
            "result_schema_version": DESIGN_PRODUCT_RESULT_SCHEMA_VERSION,
            "status": "success",
            "product_line": "design",
            "message": "设计产物已完成。",
            "final_file_paths": [],
            "progress": [],
            "active_skill": {},
            "experts": [],
            "expert_history": [],
            "last_expert_result": {},
            "code_generation_history": [],
            "last_code_generation_result": {},
            "generation": {},
            "validation": [],
            "output_files": [],
        }
        state = State(
            {
                "channel": "web",
                DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY: "设计一个股票新闻 App。",
                DESIGN_BRIEF_FORM_STATE_KEY: {
                    "schema_version": DESIGN_BRIEF_FORM_SCHEMA_VERSION,
                    "message": "<cc-question-form>{}</cc-question-form>",
                },
            },
            {},
        )
        tool_context = SimpleNamespace(
            state=state,
            _invocation_context=SimpleNamespace(user_id="user-1"),
        )

        async def _fake_agent_tool_transport(**kwargs):
            tool_context.state[DESIGN_PRODUCT_RESULT_STATE_KEY] = result_payload
            tool_context.state["final_response"] = result_payload["message"]
            tool_context.state["final_file_paths"] = []

        with patch(
            "src.productions.design.design_product_manager.design_product_manager._run_design_product_agent_tool",
            _fake_agent_tool_transport,
        ):
            result = asyncio.run(
                manager.run_product_request(
                    task=answer_block,
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(tool_context.state["design_product_brief_form_answers"]["id"], "design-brief")
        self.assertIsNone(tool_context.state.get(DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY))
        self.assertIsNone(tool_context.state.get(DESIGN_BRIEF_FORM_STATE_KEY))


if __name__ == "__main__":
    unittest.main()
