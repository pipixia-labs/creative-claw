import unittest
from types import SimpleNamespace

from google.adk.agents import LlmAgent

from src.productions.design.design_product_manager import (
    DESIGN_PRODUCT_RESULT_SCHEMA_VERSION,
    DesignProductManager,
    ProductDesignSkillRegistry,
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
                "emit_design_progress",
                "generate_design_artifact",
                "save_design_artifact",
                "validate_design_artifact",
                "register_design_delivery",
            },
        )
        self.assertIn("private product-design skills", manager.instruction)
        self.assertIn("register_design_delivery", manager.instruction)

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

        import asyncio

        result = asyncio.run(
            manager.run_product_request(
                task="设计一个 landing page。",
                tool_context=tool_context,
            )
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("ADK invocation context", result["message"])


if __name__ == "__main__":
    unittest.main()
