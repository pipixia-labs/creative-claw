import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.adk.agents import LlmAgent

from src.productions.design.design_product_manager import (
    DESIGN_BRIEF_FORM_SCHEMA_VERSION,
    DESIGN_PRODUCT_EXPERT_ALLOWLIST,
    DESIGN_PRODUCT_RESULT_SCHEMA_VERSION,
    DesignCodeGenerationAgent,
    DesignBriefFormExpert,
    build_design_code_generation_constraints,
    build_design_code_generation_prompt,
    DesignProductManager,
    ProductDesignSkillRegistry,
    normalize_question_form_block,
    parse_form_answers,
    validate_question_form_schema,
)
from src.runtime.workspace import resolve_workspace_path
from src.skills.registry import SkillRegistry


class DesignProductManagerTests(unittest.TestCase):
    def test_design_product_manager_is_llm_agent_with_private_tools(self) -> None:
        manager = DesignProductManager()

        self.assertIsInstance(manager, LlmAgent)
        self.assertEqual(manager.name, "DesignProductManager")
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
        self.assertIn("Do not use `save_design_artifact` to create the main final HTML", manager.instruction)
        self.assertIn("register_design_delivery", manager.instruction)

    def test_design_code_generation_prompt_uses_design_canvas_contract(self) -> None:
        prompt = build_design_code_generation_prompt("Design a mobile ordering flow.")

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
        self.assertIn("show three variants", constraints)

    def test_private_product_design_skill_registry_lists_standard_skill_folders(self) -> None:
        registry = ProductDesignSkillRegistry()

        skills = registry.list_skills()
        skill_names = {skill.name for skill in skills}

        self.assertIn("aaa-skill", skill_names)
        self.assertIn("bbb-skill", skill_names)
        self.assertIn("AAA Skill", registry.read_skill("aaa-skill"))

    def test_global_skill_registry_does_not_expose_private_product_design_skills(self) -> None:
        global_registry = SkillRegistry()

        skill_names = {skill.name for skill in global_registry.list_skills()}

        self.assertNotIn("aaa-skill", skill_names)
        self.assertNotIn("bbb-skill", skill_names)
        self.assertNotIn("product-design-skills", skill_names)

    def test_private_skill_tools_list_and_read_skills(self) -> None:
        manager = DesignProductManager()
        tool_context = SimpleNamespace(state={})

        listed = manager.list_product_design_skills(tool_context)
        read = manager.read_product_design_skill("bbb-skill", tool_context)

        self.assertEqual(listed["status"], "success")
        self.assertGreaterEqual(listed["count"], 2)
        self.assertEqual(read["status"], "success")
        self.assertEqual(read["name"], "bbb-skill")
        self.assertIn("BBB Skill", read["content"])
        self.assertEqual(tool_context.state["active_product_design_skill"]["name"], "bbb-skill")

    def test_design_brief_question_form_schema_helpers(self) -> None:
        expert = DesignBriefFormExpert()

        self.assertIsInstance(expert, LlmAgent)
        self.assertIn("cross-task common question framework", expert.instruction)
        self.assertIn("default coverage framework", expert.instruction)
        self.assertIn("up to 5 task-specific questions", expert.instruction)
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
                            {"value": "minimal", "label": "极简"},
                            {"value": "editorial", "label": "杂志风"},
                            {"value": "decide_for_me", "label": "为我决定"},
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

        self.assertEqual(form["version"], DESIGN_BRIEF_FORM_SCHEMA_VERSION)
        self.assertTrue(form["questions"][0]["allowOther"])
        self.assertEqual(form["questions"][1]["type"], "range")
        self.assertEqual(form["questions"][1]["default"], 6)
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

        self.assertEqual(len(form["questions"]), 9)

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
            "src.productions.design.design_product_manager.design_product_manager.dispatch_expert_call",
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
        self.assertEqual(
            tool_context.state["design_product_last_expert_result"],
            expected_tool_result,
        )
        self.assertEqual(tool_context.state["design_product_generation"], expected_tool_result)

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


if __name__ == "__main__":
    unittest.main()
