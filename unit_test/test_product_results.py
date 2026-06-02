import unittest

from src.runtime.product_results import (
    is_completed_product_result,
    is_completed_page_product_result,
    is_product_confirmation_result,
    is_terminal_product_result,
    slim_product_result,
)


class ProductResultSlimmingTests(unittest.TestCase):
    def test_page_result_keeps_only_user_facing_delivery_fields(self) -> None:
        result = slim_product_result(
            {
                "result_schema_version": "page-product-result-v1",
                "status": "success",
                "product_line": "page",
                "message": "页面已完成。",
                "final_file_paths": ["generated/page.html"],
                "active_skill": {"content": "large skill body"},
                "expert_history": [{"large": "payload"}],
                "output_files": [{"path": "generated/page.html"}],
            }
        )

        self.assertEqual(
            result,
            {
                "result_schema_version": "page-product-result-v1",
                "status": "success",
                "product_line": "page",
                "message": "页面已完成。",
                "final_file_paths": ["generated/page.html"],
            },
        )
        self.assertTrue(is_completed_page_product_result(result))

    def test_page_result_without_final_paths_is_not_completed(self) -> None:
        result = slim_product_result(
            {
                "result_schema_version": "page-product-result-v1",
                "status": "success",
                "product_line": "page",
                "message": "页面已完成。",
                "final_file_paths": [],
            }
        )

        self.assertFalse(is_completed_page_product_result(result))

    def test_design_success_result_is_completed_product_result(self) -> None:
        result = slim_product_result(
            {
                "result_schema_version": "design-product-result-v1",
                "status": "success",
                "product_line": "design",
                "message": "设计稿已完成。",
                "final_file_paths": ["generated/design.html"],
                "internal_review_notes": {"large": "payload"},
            }
        )

        self.assertEqual(
            result,
            {
                "result_schema_version": "design-product-result-v1",
                "status": "success",
                "product_line": "design",
                "message": "设计稿已完成。",
                "final_file_paths": ["generated/design.html"],
            },
        )
        self.assertTrue(is_completed_product_result(result))
        self.assertFalse(is_completed_page_product_result(result))

    def test_page_error_result_is_terminal_but_not_completed(self) -> None:
        result = slim_product_result(
            {
                "result_schema_version": "page-product-result-v1",
                "status": "error",
                "product_line": "page",
                "message": "PageProductManager finished without registering a page delivery.",
                "final_file_paths": [],
            }
        )

        self.assertFalse(is_completed_product_result(result))
        self.assertTrue(is_terminal_product_result(result))

    def test_ppt_non_success_terminal_statuses_end_the_turn(self) -> None:
        for status in ("generation_failed", "route_not_implemented", "needs_clarification"):
            with self.subTest(status=status):
                result = slim_product_result(
                    {
                        "result_schema_version": "ppt-product-result-v1",
                        "status": status,
                        "product_line": "ppt",
                        "message": "PPT product manager could not deliver a deck yet.",
                        "final_file_paths": [],
                    }
                )

                self.assertFalse(is_completed_product_result(result))
                self.assertTrue(is_terminal_product_result(result))

    def test_product_in_progress_result_is_not_terminal(self) -> None:
        result = slim_product_result(
            {
                "result_schema_version": "page-product-result-v1",
                "status": "in_progress",
                "product_line": "page",
                "message": "PageProductManager is still working.",
                "final_file_paths": [],
            }
        )

        self.assertFalse(is_terminal_product_result(result))

    def test_ppt_confirmation_result_merges_summary_into_message(self) -> None:
        result = slim_product_result(
            {
                "result_schema_version": "ppt-product-result-v1",
                "status": "awaiting_requirement_confirmation",
                "product_line": "ppt",
                "message": "请确认 PPT 需求参数。",
                "confirmed_requirement": {"large": "payload"},
                "confirmation_request": {
                    "summary_markdown": "## 需求摘要\n- 5 页",
                    "expected_user_action": "回复“确认”继续。",
                },
            }
        )

        self.assertEqual(result["final_file_paths"], [])
        self.assertIn("请确认 PPT 需求参数。", result["message"])
        self.assertIn("## 需求摘要", result["message"])
        self.assertIn("回复“确认”继续。", result["message"])
        self.assertNotIn("confirmed_requirement", result)
        self.assertNotIn("confirmation_request", result)
        self.assertTrue(is_product_confirmation_result(result))

    def test_ppt_success_result_uses_delivery_manifest_final_pptx(self) -> None:
        result = slim_product_result(
            {
                "result_schema_version": "ppt-product-result-v1",
                "status": "success",
                "product_line": "ppt",
                "message": "PPT 已完成。",
                "delivery_manifest": {
                    "final_pptx": "generated/deck.pptx",
                    "previews": ["generated/preview-1.png"],
                },
            }
        )

        self.assertEqual(result["final_file_paths"], ["generated/deck.pptx"])
        self.assertTrue(is_completed_product_result(result))
        self.assertFalse(is_completed_page_product_result(result))


if __name__ == "__main__":
    unittest.main()
