import unittest

from src.productions.design.design_product_manager import (
    DesignProductManager,
    DesignSchemaValidationError,
    validate_design_brief_contract,
    validate_design_result_contract,
)


class DesignSchemaValidationTests(unittest.TestCase):
    def test_prepared_brief_matches_design_brief_schema(self) -> None:
        manager = DesignProductManager()
        brief = manager.prepare_brief(
            prompt="设计一个运营数据 dashboard，展示 DAU、转化率和留存。",
            scenario="operation_data_ui",
        )

        validate_design_brief_contract(brief.design_brief)

    def test_clarification_result_matches_result_schema(self) -> None:
        manager = DesignProductManager()
        brief = manager.prepare_brief(
            prompt="做一个后台看板。",
            scenario="dashboard",
            allow_assumptions=False,
        )
        result = manager.build_clarification_result(brief)

        validate_design_result_contract(result)

    def test_generation_result_matches_result_schema(self) -> None:
        manager = DesignProductManager()
        brief = manager.prepare_brief(
            prompt="做一个 AI CRM pricing page。",
            scenario="pricing_page",
        )
        result = manager.build_generation_result(
            brief=brief,
            code_generation_result={
                "status": "success",
                "message": "Generated html code.",
                "output_files": [{"path": "generated/design-demo/turn_1/design.html"}],
            },
            design_validation=[
                {
                    "status": "pass",
                    "path": "generated/design-demo/turn_1/design.html",
                    "errors": [],
                    "warnings": [],
                    "checks": {"exists": True},
                }
            ],
        )

        validate_design_result_contract(result)

    def test_invalid_contract_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(DesignSchemaValidationError, "design-brief-v1 contract invalid"):
            validate_design_brief_contract({"schema_version": "wrong"})


if __name__ == "__main__":
    unittest.main()
