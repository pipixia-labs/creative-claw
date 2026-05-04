import json
import unittest
from pathlib import Path

from src.productions.design.design_product_manager import DesignProductManager
from src.productions.design.design_product_manager.design_product_manager import DESIGN_BRIEF_SCHEMA_VERSION


class DesignProductManagerTests(unittest.TestCase):
    def test_instruction_does_not_expose_code_generation_expert(self) -> None:
        manager = DesignProductManager()

        instruction = manager.build_instruction()

        self.assertNotIn("CodeGenerationExpert", instruction)
        self.assertIn("Own code-backed design artifacts", instruction)

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

    def test_prepare_pricing_page_brief_uses_specific_schema(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(prompt="做一个 AI CRM pricing page，包含三档套餐和企业版预约演示。")

        self.assertEqual(brief.selection.brief_schema_id, "brief_elements.pricing_page")
        self.assertEqual(brief.selection.surface, "landing_page")
        self.assertEqual(brief.selection.task_skill, "pricing-page")
        self.assertIn("feature_comparison", brief.design_brief["content_requirements"])

    def test_prepare_docs_page_brief_uses_specific_schema(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(prompt="做一个 developer docs page，介绍 API quickstart 和配置示例。")

        self.assertEqual(brief.selection.brief_schema_id, "brief_elements.docs_page")
        self.assertEqual(brief.selection.surface, "docs_page")
        self.assertEqual(brief.selection.task_skill, "docs-page")
        self.assertEqual(brief.selection.device_frame, "browser-chrome")
        self.assertIn("information_architecture", brief.design_brief["content_requirements"])

    def test_prepare_kanban_board_brief_uses_specific_schema(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(prompt="做一个内容生产 kanban board，展示待写作、待审核和已发布。")

        self.assertEqual(brief.selection.brief_schema_id, "brief_elements.kanban_board")
        self.assertEqual(brief.selection.surface, "dashboard")
        self.assertEqual(brief.selection.task_skill, "kanban-board")
        self.assertIn("status_transitions", brief.design_brief["content_requirements"])

    def test_prepare_magazine_poster_brief_uses_specific_schema(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(prompt="做一张 magazine poster，用于新品发布，主标题 FlowKit Launch。")

        self.assertEqual(brief.selection.brief_schema_id, "brief_elements.magazine_poster")
        self.assertEqual(brief.selection.surface, "poster")
        self.assertEqual(brief.selection.task_skill, "magazine-poster")
        self.assertIn("visual_subject", brief.design_brief["content_requirements"])

    def test_prepare_wireframe_brief_uses_specific_schema(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(prompt="给习惯打卡 app 做一个 low fidelity wireframe。")

        self.assertEqual(brief.selection.brief_schema_id, "brief_elements.wireframe_sketch")
        self.assertEqual(brief.selection.surface, "wireframe")
        self.assertEqual(brief.selection.task_skill, "wireframe-sketch")
        self.assertEqual(brief.selection.device_frame, "browser-chrome")
        self.assertIn("primary_flow", brief.design_brief["content_requirements"])

    def test_prepare_remaining_builtin_task_skill_scenarios(self) -> None:
        manager = DesignProductManager()
        cases = [
            ("写一篇 blog post article，介绍产品案例。", "blog_post", "brief_elements.blog_post", "blog-post", "core_argument"),
            ("做一次 design critique，review my landing page。", "critique", "brief_elements.critique", "critique", "review_goal"),
            ("做一个 dating app matchmaking dashboard。", "dating_web", "brief_elements.dating_web", "dating-web", "matching_goal"),
            ("做一份 digital guide ebook。", "digital_eguide", "brief_elements.digital_eguide", "digital-eguide", "learning_outcome"),
            ("做一个 product launch email template。", "email_marketing", "brief_elements.email_marketing", "email-marketing", "campaign_goal"),
            ("做一份 service runbook 给 on-call 使用。", "eng_runbook", "brief_elements.eng_runbook", "eng-runbook", "alert_types"),
            ("做一个 quarterly finance report。", "finance_report", "brief_elements.finance_report", "finance-report", "core_metrics"),
            ("做一个 gamified habit tracker app。", "gamified_app", "brief_elements.gamified_app", "gamified-app", "core_loop"),
            ("做一个 magazine web PPT，用于发布会。", "guizang_ppt", "brief_elements.guizang_ppt", "guizang-ppt", "editorial_theme"),
            ("做一个 new hire onboarding guide。", "hr_onboarding", "brief_elements.hr_onboarding", "hr-onboarding", "must_do_tasks"),
            ("做一个 html video kinetic typography composition。", "hyperframes", "brief_elements.hyperframes", "hyperframes", "scene_outline"),
            ("做一个 image poster key art。", "image_poster", "brief_elements.image_poster", "image-poster", "subject"),
            ("做一个 invoice billing statement。", "invoice", "brief_elements.invoice", "invoice", "line_items"),
            ("整理 meeting notes 和 action items。", "meeting_notes", "brief_elements.meeting_notes", "meeting-notes", "action_items"),
            ("做一个 mobile onboarding flow。", "mobile_onboarding", "brief_elements.mobile_onboarding", "mobile-onboarding", "activation_goal"),
            ("做一组 motion design title card frames。", "motion_frames", "brief_elements.motion_frames", "motion-frames", "frame_sequence"),
            ("写一个 PM spec PRD。", "pm_spec", "brief_elements.pm_spec", "pm-spec", "problem_statement"),
            ("做一个 replit deck。", "replit_deck", "brief_elements.replit_deck", "replit-deck", "story_mode"),
            ("做一个 pixel art sprite animation。", "sprite_animation", "brief_elements.sprite_animation", "sprite-animation", "sprite_style"),
            ("做一个 team OKRs 页面。", "team_okrs", "brief_elements.team_okrs", "team-okrs", "key_results"),
            ("做一个 tweak panel 和 live controls。", "tweaks", "brief_elements.tweaks", "tweaks", "adjustable_parameters"),
            ("做一个 shortform video reel。", "video_shortform", "brief_elements.video_shortform", "video-shortform", "story_beats"),
            ("做一个 weekly update deck。", "weekly_update", "brief_elements.weekly_update", "weekly-update", "wins"),
        ]

        for prompt, scenario, expected_schema, expected_skill, expected_requirement in cases:
            with self.subTest(scenario=scenario):
                brief = manager.prepare_brief(prompt=prompt, scenario=scenario)
                self.assertEqual(brief.selection.brief_schema_id, expected_schema)
                self.assertEqual(brief.selection.task_skill, expected_skill)
                self.assertIn(expected_requirement, brief.design_brief["content_requirements"])

    def test_prepare_all_builtin_scenarios_use_existing_context_files(self) -> None:
        manager = DesignProductManager()
        project_root = Path(__file__).resolve().parents[1]
        brief_root = project_root / "skills" / "design-knowledge-and-skills" / "brief-elements"

        for path in sorted(brief_root.glob("*.json")):
            brief_element = json.loads(path.read_text(encoding="utf-8"))
            scenario = brief_element["id"].split(".", 1)[1]
            with self.subTest(scenario=scenario):
                brief = manager.prepare_brief(
                    prompt=f"Create a design artifact for {brief_element['title']}.",
                    scenario=scenario,
                )
                missing_context_files = [
                    context_file
                    for context_file in brief.selection.context_files
                    if not (project_root / context_file).exists()
                ]
                self.assertEqual(missing_context_files, [])

    def test_prepare_web_ppt_prefers_magazine_web_ppt_schema(self) -> None:
        manager = DesignProductManager()

        brief = manager.prepare_brief(prompt="做一个网页PPT，用于产品发布会。")

        self.assertEqual(brief.selection.brief_schema_id, "brief_elements.guizang_ppt")
        self.assertEqual(brief.selection.task_skill, "guizang-ppt")

    def test_select_brief_resource_raises_clear_error_without_runtime_brief_elements(self) -> None:
        manager = DesignProductManager()

        with self.assertRaisesRegex(RuntimeError, "No runtime-enabled brief element schemas"):
            manager._select_brief_resource({"resources": []}, prompt="做一个 dashboard。", scenario="")

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

        brief = manager.prepare_brief(prompt="做一个 pitch deck，包含问题、方案和路线图。")

        self.assertEqual(brief.selection.surface, "deck")
        self.assertEqual(brief.selection.task_skill, "simple-deck")
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
                    "checks": {"exists": False},
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
                    "checks": {"exists": True},
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
