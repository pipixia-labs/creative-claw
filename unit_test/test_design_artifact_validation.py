import tempfile
import unittest
from pathlib import Path

from src.productions.design.design_product_manager import BrowserViewport, validate_design_artifact
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
        self.assertTrue(result["checks"]["has_layout_css"])
        self.assertTrue(result["checks"]["has_responsive_signal"])
        self.assertTrue(result["checks"]["has_viewport_meta"])

    def test_validate_design_artifact_warns_for_weak_preview_quality(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            output_file = Path(tmpdir) / "thin.html"
            output_file.write_text(
                """<!doctype html>
<html lang="en">
<body>
  <main><h1>Dashboard</h1><p>Thin.</p></main>
</body>
</html>
""",
                encoding="utf-8",
            )

            result = validate_design_artifact(output_file).to_dict()

        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["errors"], [])
        self.assertFalse(result["checks"]["has_viewport_meta"])
        self.assertFalse(result["checks"]["has_layout_css"])
        self.assertIn("preview quality: missing viewport meta tag", result["warnings"])

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

    def test_validate_design_artifact_can_run_browser_preview_checks(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            output_file = Path(tmpdir) / "dashboard.html"
            output_file.write_text(
                """<!doctype html>
<html lang="en">
<head><meta name="viewport" content="width=device-width, initial-scale=1"><style>main{display:grid}</style></head>
<body><main><section><h1>Operations Dashboard</h1><p>Enough visible content for browser preview validation.</p></section></main></body>
</html>
""",
                encoding="utf-8",
            )

            def _fake_browser_runner(path: Path, viewports: tuple[BrowserViewport, ...]) -> dict[str, object]:
                self.assertEqual(path, output_file)
                self.assertEqual(viewports[0].name, "desktop")
                return {
                    "checks": {
                        "browser_preview_available": True,
                        "browser_preview_checked": True,
                        "browser_desktop_no_console_errors": False,
                    },
                    "errors": ["browser preview: desktop console errors: ReferenceError"],
                    "warnings": [],
                }

            result = validate_design_artifact(
                output_file,
                browser_preview=True,
                browser_viewports=(BrowserViewport(name="desktop", width=1280, height=800),),
                browser_preview_runner=_fake_browser_runner,
            ).to_dict()

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["checks"]["browser_preview_available"])
        self.assertFalse(result["checks"]["browser_desktop_no_console_errors"])
        self.assertIn("browser preview: desktop console errors: ReferenceError", result["errors"])


if __name__ == "__main__":
    unittest.main()
