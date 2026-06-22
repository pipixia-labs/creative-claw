import unittest

from src.runtime.product_protocol import ProductToolRequest
from src.runtime.product_results import ProductToolResult


class ProductProtocolTests(unittest.TestCase):
    def test_product_tool_request_defaults_match_existing_tool_semantics(self) -> None:
        request = ProductToolRequest(
            product_line="ppt",
            task="  build a deck  ",
            inputs=None,
            output=None,
        )

        self.assertEqual(request.product_line, "ppt")
        self.assertEqual(request.task, "build a deck")
        self.assertEqual(request.inputs, [])
        self.assertEqual(request.output, {})
        self.assertEqual(
            request.to_event_args(),
            {"task": "build a deck", "inputs": [], "output": {}},
        )

    def test_product_tool_request_preserves_non_empty_shapes_for_product_validation(self) -> None:
        request = ProductToolRequest(
            product_line="design",
            task="design",
            inputs={"product_image": {"path": "generated/input.png"}},
            output={"format": "html"},
            interaction_language="english",
        )

        manager_kwargs = request.to_manager_kwargs()

        self.assertEqual(manager_kwargs["inputs"], {"product_image": {"path": "generated/input.png"}})
        self.assertEqual(manager_kwargs["output"], {"format": "html"})
        self.assertEqual(manager_kwargs["interaction_language"], "en")
        self.assertEqual(request.to_event_args()["interaction_language"], "en")
        self.assertIsNot(manager_kwargs["inputs"], request.inputs)
        self.assertIsNot(manager_kwargs["output"], request.output)

    def test_falsey_input_and_output_shapes_keep_orchestrator_compatibility(self) -> None:
        request = ProductToolRequest(
            product_line="page",
            task="page",
            inputs={},
            output=[],
        )

        self.assertEqual(request.inputs, [])
        self.assertEqual(request.output, {})

    def test_product_tool_result_filters_empty_fields_but_keeps_final_paths_key(self) -> None:
        result = ProductToolResult(
            result_schema_version="page-product-result-v1",
            status="success",
            product_line="page",
            message="done",
            final_file_paths=[],
        )

        self.assertEqual(
            result.to_dict(),
            {
                "result_schema_version": "page-product-result-v1",
                "status": "success",
                "product_line": "page",
                "message": "done",
                "final_file_paths": [],
            },
        )

    def test_product_tool_result_normalizes_final_paths(self) -> None:
        result = ProductToolResult(
            status="success",
            product_line="ppt",
            message="done",
            final_file_paths=[" generated/deck.pptx ", "", None],
        )

        self.assertEqual(result.final_file_paths, ["generated/deck.pptx"])


if __name__ == "__main__":
    unittest.main()
