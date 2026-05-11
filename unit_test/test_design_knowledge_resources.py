import json
import unittest
from pathlib import Path

from src.productions.design.design_systems import (
    list_design_systems,
    read_design_system,
    resolve_design_system_preview,
)


class DesignKnowledgeResourceTests(unittest.TestCase):
    def _project_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _design_root(self) -> Path:
        return self._project_root() / "src" / "productions" / "design"

    def _design_system_root(self) -> Path:
        return self._design_root() / "design-systems"

    def _frame_root(self) -> Path:
        return self._design_root() / "frames"

    def _schema_root(self) -> Path:
        return self._design_root() / "design_product_manager" / "schemas"

    def test_design_systems_are_packaged_with_production_design_resources(self) -> None:
        design_systems = list_design_systems()
        ids = {item.id for item in design_systems}

        self.assertGreaterEqual(len(ids), 60)
        self.assertIn("claude", ids)
        self.assertIn("stripe", ids)
        self.assertIn("vercel", ids)
        self.assertIn("apple", ids)
        for item in design_systems:
            design_path = self._design_system_root() / item.id / "DESIGN.md"
            self.assertTrue(design_path.is_file(), item.id)

    def test_core_design_systems_are_readable(self) -> None:
        for design_system_id in ("claude", "stripe", "vercel", "apple"):
            body = read_design_system(design_system_id)

            self.assertIsNotNone(body, design_system_id)
            self.assertTrue((body or "").strip(), design_system_id)
            self.assertIn("#", body or "", design_system_id)

    def test_design_system_preview_paths_exist(self) -> None:
        for item in list_design_systems():
            preview = resolve_design_system_preview(item.id, dark=False)
            dark_preview = resolve_design_system_preview(item.id, dark=True)

            self.assertIsNotNone(preview, item.id)
            self.assertTrue(preview.is_file(), item.id)
            self.assertIsNotNone(dark_preview, item.id)
            self.assertTrue(dark_preview.is_file(), item.id)

    def test_device_frame_resources_are_packaged(self) -> None:
        expected = {
            "README.md",
            "android-pixel.html",
            "browser-chrome.html",
            "ipad-pro.html",
            "iphone-15-pro.html",
            "macbook.html",
        }
        existing = {path.name for path in self._frame_root().iterdir() if path.is_file()}

        self.assertEqual(expected - existing, set())

    def test_contract_schemas_define_stable_versions(self) -> None:
        design_brief_schema = json.loads((self._schema_root() / "design-brief-v1.schema.json").read_text(encoding="utf-8"))
        result_schema = json.loads((self._schema_root() / "design-product-result-v1.schema.json").read_text(encoding="utf-8"))

        self.assertEqual(design_brief_schema["properties"]["schema_version"]["const"], "design-brief-v1")
        self.assertIn("design_system", design_brief_schema["required"])
        self.assertIn("constraints", design_brief_schema["required"])
        self.assertEqual(
            result_schema["properties"]["result_schema_version"]["const"],
            "design-product-result-v1",
        )
        self.assertIn("design_validation", result_schema["required"])

    def test_packaged_design_resource_files_do_not_embed_local_absolute_paths(self) -> None:
        resource_paths = [
            *sorted(self._design_system_root().glob("*/DESIGN.md")),
            *sorted(self._design_system_root().glob("*/preview*.html")),
            *sorted(path for path in self._frame_root().glob("*") if path.is_file()),
            *sorted(self._schema_root().glob("*.json")),
        ]

        self.assertTrue(resource_paths)
        for path in resource_paths:
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("/" + "Users/", content, path.as_posix())


if __name__ == "__main__":
    unittest.main()
