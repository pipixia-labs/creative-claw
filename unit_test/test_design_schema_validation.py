import unittest

from src.productions.design.design_product_manager import DesignSchemaValidationError
from src.productions.design.design_product_manager.schema_validation import (
    validate_design_brief_contract,
)


class DesignSchemaValidationTests(unittest.TestCase):
    def test_legacy_schema_validator_still_reports_clear_errors(self) -> None:
        with self.assertRaisesRegex(DesignSchemaValidationError, "design-brief-v1 contract invalid"):
            validate_design_brief_contract({"schema_version": "wrong"})


if __name__ == "__main__":
    unittest.main()
