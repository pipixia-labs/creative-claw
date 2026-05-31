from typing import Any
import unittest

from pydantic import BaseModel

from src.agents.experts.schema_utils import (
    as_list,
    as_non_empty_string_list,
    as_prompt_list,
    clean_string,
    current_output_dict,
)


class _Output(BaseModel):
    status: str
    message: str
    output_files: list[dict[str, Any]] | None = None


class ExpertSchemaUtilsTests(unittest.TestCase):
    def test_clean_string_matches_expert_session_state_behavior(self) -> None:
        self.assertEqual(clean_string(None), "")
        self.assertEqual(clean_string(" value "), "value")
        self.assertEqual(clean_string(123), "123")

    def test_prompt_list_preserves_empty_prompt_entries(self) -> None:
        self.assertEqual(as_prompt_list(None), [])
        self.assertEqual(as_prompt_list(None, default_empty_prompt=True), [""])
        self.assertEqual(as_prompt_list(" prompt "), ["prompt"])
        self.assertEqual(as_prompt_list([" a ", None, ""]), ["a", "", ""])

    def test_non_empty_string_list_filters_blank_values(self) -> None:
        self.assertEqual(as_non_empty_string_list(None), [])
        self.assertEqual(as_non_empty_string_list(" path "), ["path"])
        self.assertEqual(as_non_empty_string_list([" a ", "", None, "b"]), ["a", "b"])

    def test_as_list_preserves_existing_list_entries(self) -> None:
        original = ["a", None]
        self.assertIs(as_list(original), original)
        self.assertEqual(as_list(("a", "b")), ["a", "b"])
        self.assertEqual(as_list(None), [])
        self.assertEqual(as_list("a"), ["a"])

    def test_current_output_dict_omits_none_fields(self) -> None:
        self.assertEqual(
            current_output_dict(_Output(status="error", message="boom")),
            {"status": "error", "message": "boom"},
        )


if __name__ == "__main__":
    unittest.main()
