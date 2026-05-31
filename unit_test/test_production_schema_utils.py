from typing import Any
import unittest

from pydantic import BaseModel, Field

from src.productions.schema_utils import (
    clean_string,
    default_empty_dict,
    default_empty_list,
    default_schema_version,
    model_dump_dict,
    require_non_empty_string,
)


class _StatePayload(BaseModel):
    task: str
    output: dict[str, Any] = Field(default_factory=dict)


class ProductionSchemaUtilsTests(unittest.TestCase):
    def test_clean_string_matches_product_schema_behavior(self) -> None:
        self.assertEqual(clean_string(None), "")
        self.assertEqual(clean_string(" value "), "value")
        self.assertEqual(clean_string(123), "123")

    def test_default_empty_list_preserves_non_none_shape(self) -> None:
        original = {"path": "generated/source.md"}

        self.assertEqual(default_empty_list(None), [])
        self.assertIs(default_empty_list(original), original)

    def test_default_empty_dict_only_defaults_missing_values(self) -> None:
        original = {"format": "html"}

        self.assertEqual(default_empty_dict(None), {})
        self.assertIs(default_empty_dict(original), original)

    def test_default_schema_version_uses_cleaned_value_or_default(self) -> None:
        self.assertEqual(default_schema_version(" v2 ", "v1"), "v2")
        self.assertEqual(default_schema_version("", "v1"), "v1")

    def test_require_non_empty_string_preserves_error_message(self) -> None:
        self.assertEqual(require_non_empty_string("task", field_name="task"), "task")
        with self.assertRaisesRegex(ValueError, "task must be non-empty"):
            require_non_empty_string("", field_name="task")

    def test_model_dump_dict_returns_python_payload(self) -> None:
        payload = model_dump_dict(_StatePayload(task="build", output={"format": "html"}))

        self.assertEqual(payload, {"task": "build", "output": {"format": "html"}})


if __name__ == "__main__":
    unittest.main()
