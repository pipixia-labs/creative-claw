import unittest

from src.agents.design_product_manager import DesignProductManager


class DesignProductManagerTests(unittest.TestCase):
    def test_prepare_dashboard_brief_selects_resource_defaults(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(
            prompt="设计一个运营数据 dashboard，展示 DAU、转化率、留存和渠道 ROI。",
            scenario="operation_data_ui",
        )

        self.assertEqual(brief.selection.surface, "dashboard")
        self.assertEqual(brief.selection.task_skill, "dashboard")
        self.assertEqual(brief.selection.design_system, "linear-app")
        self.assertEqual(brief.selection.device_frame, "browser-chrome")
        self.assertFalse(brief.needs_clarification)
        self.assertIn(
            "skills/design-knowledge-and-skills/brief-elements/dashboard.json",
            brief.selection.context_files,
        )
        self.assertIn(
            "skills/design-knowledge-and-skills/skills/dashboard/SKILL.md",
            brief.selection.context_files,
        )
        self.assertIn(
            "skills/design-knowledge-and-skills/design-systems/linear-app/DESIGN.md",
            brief.selection.context_files,
        )
        self.assertEqual(brief.code_generation_request["language"], "html")

    def test_prepare_mobile_brief_uses_mobile_surface_and_frame(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(prompt="做一个 mobile app 的三屏 onboarding 原型。")

        self.assertEqual(brief.selection.surface, "mobile_app")
        self.assertEqual(brief.selection.task_skill, "mobile-onboarding")
        self.assertEqual(brief.selection.device_frame, "iphone-15-pro")
        self.assertIn(
            "skills/design-knowledge-and-skills/assets/frames/iphone-15-pro.html",
            brief.selection.context_files,
        )

    def test_prepare_brief_can_return_clarification_questions(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(
            prompt="做一个后台看板。",
            scenario="dashboard",
            allow_assumptions=False,
            max_questions=4,
        )

        self.assertTrue(brief.needs_clarification)
        self.assertEqual(len(brief.questions), 4)
        self.assertEqual(brief.questions[0]["field"], "business_domain")
        self.assertIn("business_domain", brief.missing_fields)

    def test_overrides_select_specific_design_resources(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(
            prompt="做一个 SaaS landing page。",
            design_system="stripe",
            task_skill="web-prototype",
            device_frame="browser-chrome",
        )

        self.assertEqual(brief.selection.surface, "landing_page")
        self.assertEqual(brief.selection.design_system, "stripe")
        self.assertEqual(brief.selection.task_skill, "web-prototype")
        self.assertEqual(brief.selection.device_frame, "browser-chrome")


if __name__ == "__main__":
    unittest.main()
