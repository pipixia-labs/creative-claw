import unittest

from src.agents.design_product_manager import DesignProductManager
from src.agents.design_product_manager.design_product_manager import DESIGN_BRIEF_SCHEMA_VERSION


class DesignProductManagerTests(unittest.TestCase):
    def test_prepare_dashboard_brief_selects_resource_defaults(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(
            prompt="设计一个 analytics dashboard，展示 DAU、转化率、留存和渠道 ROI。",
            scenario="dashboard",
        )

        self.assertEqual(brief.selection.surface, "dashboard")
        self.assertEqual(brief.selection.brief_schema_id, "brief_elements.dashboard")
        self.assertEqual(brief.selection.task_skill, "dashboard")
        self.assertEqual(brief.selection.design_system, "linear-app")
        self.assertEqual(brief.selection.device_frame, "browser-chrome")
        self.assertFalse(brief.needs_clarification)
        self.assertEqual(brief.design_brief["schema_version"], DESIGN_BRIEF_SCHEMA_VERSION)
        self.assertEqual(brief.code_generation_request["design_brief"]["surface"], "dashboard")
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

    def test_prepare_operation_data_ui_brief_uses_specific_schema(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(
            prompt="设计一个运营数据 UI，展示 GMV、转化率、留存和渠道 ROI。",
            scenario="operation_data_ui",
        )

        self.assertEqual(brief.selection.surface, "dashboard")
        self.assertEqual(brief.selection.brief_schema_id, "brief_elements.operation_data_ui")
        self.assertEqual(brief.selection.task_skill, "dashboard")
        self.assertIn(
            "skills/design-knowledge-and-skills/brief-elements/operation-data-ui.json",
            brief.selection.context_files,
        )
        self.assertIn("north_star_metric", brief.design_brief["content_requirements"])

    def test_prepare_admin_console_brief_uses_specific_schema(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(prompt="做一个后台管理 admin console，管理订单、用户和审核状态。")

        self.assertEqual(brief.selection.brief_schema_id, "brief_elements.admin_console")
        self.assertEqual(brief.selection.surface, "dashboard")
        self.assertEqual(brief.selection.task_skill, "dashboard")
        self.assertIn("managed_entities", brief.design_brief["content_requirements"])

    def test_prepare_marketing_campaign_brief_uses_specific_schema(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(prompt="做一个营销活动页 campaign page，推广新品发布并收集预约。")

        self.assertEqual(brief.selection.brief_schema_id, "brief_elements.marketing_campaign_page")
        self.assertEqual(brief.selection.surface, "landing_page")
        self.assertEqual(brief.selection.task_skill, "web-prototype")
        self.assertIn("campaign_name", brief.design_brief["content_requirements"])

    def test_prepare_social_carousel_brief_uses_specific_schema(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(prompt="做一组小红书社媒轮播卡片，介绍新品功能。")

        self.assertEqual(brief.selection.brief_schema_id, "brief_elements.social_carousel")
        self.assertEqual(brief.selection.surface, "social_carousel")
        self.assertEqual(brief.selection.task_skill, "social-carousel")
        self.assertEqual(brief.selection.design_system, "xiaohongshu")

    def test_prepare_html_deck_brief_uses_specific_schema(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(prompt="做一个 HTML deck，用于产品发布演示。")

        self.assertEqual(brief.selection.brief_schema_id, "brief_elements.html_deck")
        self.assertEqual(brief.selection.surface, "deck")
        self.assertEqual(brief.selection.task_skill, "simple-deck")
        self.assertIn("interaction_model", brief.design_brief["content_requirements"])

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

    def test_prepare_landing_brief_selects_landing_defaults(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(prompt="做一个 SaaS landing page，突出产品价值、客户案例和转化行动。")

        self.assertEqual(brief.selection.surface, "landing_page")
        self.assertEqual(brief.selection.task_skill, "saas-landing")
        self.assertEqual(brief.selection.design_system, "stripe")
        self.assertEqual(brief.selection.device_frame, "browser-chrome")
        self.assertIn(
            "skills/design-knowledge-and-skills/brief-elements/landing-page.json",
            brief.selection.context_files,
        )

    def test_prepare_deck_brief_selects_deck_defaults(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(prompt="做一个 weekly update deck，包含进展、风险和下周计划。")

        self.assertEqual(brief.selection.surface, "deck")
        self.assertEqual(brief.selection.task_skill, "weekly-update")
        self.assertEqual(brief.selection.design_system, "warm-editorial")
        self.assertEqual(brief.selection.device_frame, "")
        self.assertIn(
            "skills/design-knowledge-and-skills/brief-elements/deck.json",
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

    def test_build_clarification_result_has_stable_contract(self) -> None:
        manager = DesignProductManager()
        brief = manager.prepare_brief(
            prompt="做一个后台看板。",
            scenario="dashboard",
            allow_assumptions=False,
        )

        result = manager.build_clarification_result(brief)

        self.assertEqual(result["result_schema_version"], "design-product-result-v1")
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["next_action"], "ask_user")
        self.assertEqual(result["brief"]["design_brief"]["schema_version"], DESIGN_BRIEF_SCHEMA_VERSION)
        self.assertEqual(result["code_generation"], None)

    def test_build_generation_result_reports_validation_failure(self) -> None:
        manager = DesignProductManager()
        brief = manager.prepare_brief(prompt="做一个 dashboard。")

        result = manager.build_generation_result(
            brief=brief,
            code_generation_result={
                "status": "success",
                "message": "Generated html code.",
                "output_files": [{"path": "generated/demo.html"}],
            },
            design_validation=[
                {
                    "path": "generated/demo.html",
                    "status": "error",
                    "errors": ["artifact file does not exist"],
                    "warnings": [],
                }
            ],
        )

        self.assertEqual(result["status"], "validation_failed")
        self.assertEqual(result["next_action"], "user_can_request_regeneration")
        self.assertEqual(result["design_issues"][0]["source"], "design_validation")

    def test_build_generation_result_reports_validation_warning(self) -> None:
        manager = DesignProductManager()
        brief = manager.prepare_brief(prompt="做一个 dashboard。")

        result = manager.build_generation_result(
            brief=brief,
            code_generation_result={
                "status": "success",
                "message": "Generated html code.",
                "output_files": [{"path": "generated/demo.html"}],
            },
            design_validation=[
                {
                    "path": "generated/demo.html",
                    "status": "warning",
                    "errors": [],
                    "warnings": ["non-blocking issue"],
                }
            ],
        )

        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["next_action"], "review_validation_warnings")

    def test_resource_filters_exclude_reference_only_and_disabled_resources(self) -> None:
        manifest = {
            "resources": [
                {"type": "task_skill", "id": "task_skill.enabled", "runtimeEnabled": True},
                {"type": "task_skill", "id": "task_skill.disabled", "runtimeEnabled": False},
                {"type": "task_skill", "id": "task_skill.reference", "referenceOnly": True},
            ]
        }

        resources = DesignProductManager._resources_by_type(manifest, "task_skill")

        self.assertEqual([resource["id"] for resource in resources], ["task_skill.enabled"])


if __name__ == "__main__":
    unittest.main()
