import tempfile
import unittest
from pathlib import Path

from src.agents.design_product_manager import validate_design_artifact
from src.runtime.workspace import workspace_root


class DesignArtifactValidationTests(unittest.TestCase):
    def test_validate_design_artifact_passes_for_structured_html(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            output_file = Path(tmpdir) / "dashboard.html"
            output_file.write_text(
                """<!doctype html>
<html lang="en">
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    main { display: grid; gap: 16px; }
    @media (max-width: 720px) { main { display: flex; flex-direction: column; } }
  </style>
</head>
<body>
  <main>
    <section><h1>Operations Dashboard</h1><p>DAU, conversion, retention, and ROI are presented for daily review.</p></section>
  </main>
</body>
</html>
""",
                encoding="utf-8",
            )

            result = validate_design_artifact(output_file).to_dict()

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["checks"]["exists"])
        self.assertTrue(result["checks"]["parseable_html"])
        self.assertTrue(result["checks"]["has_visible_text"])
        self.assertNotIn("has_layout_css", result["checks"])

    def test_validate_design_artifact_errors_for_invalid_html(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            output_file = Path(tmpdir) / "broken.html"
            output_file.write_text("hello", encoding="utf-8")

            result = validate_design_artifact(output_file).to_dict()

        self.assertEqual(result["status"], "error")
        self.assertIn("artifact is missing an <html> tag", result["errors"])
        self.assertIn("artifact is missing a <body> tag", result["errors"])

    def test_validate_design_artifact_rejects_outside_workspace_path(self) -> None:
        result = validate_design_artifact("/tmp/outside-workspace.html").to_dict()

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["checks"]["workspace_path"])


if __name__ == "__main__":
    unittest.main()
