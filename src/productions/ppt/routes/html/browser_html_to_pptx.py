"""Browser-backed HTML-to-PPTX conversion wrapper for the PPT HTML route."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from src.productions.ppt.schemas import HtmlTemplatePackage


@dataclass(frozen=True)
class BrowserHtmlToPptxResult:
    """Result returned by the browser-backed HTML-to-PPTX converter."""

    ok: bool
    pptx_path: Path
    engine: str = "node_playwright_pptxgenjs"
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)
    unavailable: bool = False


def convert_html_pages_with_browser(
    *,
    html_pages: list[str],
    pptx_path: Path,
    template: HtmlTemplatePackage,
    timeout_seconds: int = 90,
) -> BrowserHtmlToPptxResult:
    """Convert HTML pages to editable PPTX using Chromium layout and pptxgenjs.

    This wrapper keeps the JavaScript converter fixed and trusted. The LLM only
    provides slide HTML; it never provides executable conversion code.
    """
    node_path = shutil.which("node")
    if not node_path:
        return BrowserHtmlToPptxResult(
            ok=False,
            pptx_path=pptx_path,
            errors=["Node.js is not available for browser-backed HTML-to-PPTX conversion."],
            unavailable=True,
        )

    script_path = Path(__file__).with_name("node_html_to_pptx") / "html_to_pptx_converter.js"
    if not script_path.exists():
        return BrowserHtmlToPptxResult(
            ok=False,
            pptx_path=pptx_path,
            errors=[f"Browser HTML-to-PPTX converter script is missing: {script_path}"],
            unavailable=True,
        )

    clean_pages = [str(page or "").strip() for page in html_pages if str(page or "").strip()]
    if not clean_pages:
        return BrowserHtmlToPptxResult(
            ok=False,
            pptx_path=pptx_path,
            errors=["No generated HTML pages were available for browser-backed PPTX conversion."],
        )

    with tempfile.TemporaryDirectory(prefix="ppt_html_to_pptx_", dir=str(pptx_path.parent)) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        page_specs = []
        for index, html_page in enumerate(clean_pages, start=1):
            html_path = temp_dir / f"slide_{index:03d}.html"
            html_path.write_text(
                _wrap_html_page(html_page, template=template),
                encoding="utf-8",
            )
            page_specs.append(
                {
                    "slideNumber": index,
                    "htmlPath": str(html_path),
                }
            )

        report_path = temp_dir / "conversion_report.json"
        manifest_path = temp_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "title": "Creative Claw PPT",
                    "outputPath": str(pptx_path),
                    "reportPath": str(report_path),
                    "viewportWidth": template.viewport_width,
                    "viewportHeight": template.viewport_height,
                    "aspectRatio": template.aspect_ratio,
                    "pages": page_specs,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        try:
            completed = subprocess.run(
                [node_path, str(script_path), str(manifest_path)],
                cwd=_project_root(),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return BrowserHtmlToPptxResult(
                ok=False,
                pptx_path=pptx_path,
                errors=[f"Browser HTML-to-PPTX conversion timed out after {timeout_seconds}s: {exc}"],
            )

        report = _read_json_report(report_path)
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        report_errors = _extract_report_errors(report)
        availability_text = "\n".join([stderr, stdout, *report_errors])
        unavailable = _looks_like_browser_converter_unavailable(availability_text)
        if completed.returncode != 0:
            errors = report_errors
            if stderr:
                errors.append(stderr)
            if stdout:
                errors.append(stdout)
            return BrowserHtmlToPptxResult(
                ok=False,
                pptx_path=pptx_path,
                warnings=_extract_report_warnings(report),
                errors=errors or [f"Browser HTML-to-PPTX converter exited with code {completed.returncode}."],
                report=report,
                unavailable=unavailable,
            )

        if not pptx_path.exists():
            return BrowserHtmlToPptxResult(
                ok=False,
                pptx_path=pptx_path,
                warnings=_extract_report_warnings(report),
                errors=["Browser HTML-to-PPTX converter completed but did not create the PPTX file."],
                report=report,
            )

        return BrowserHtmlToPptxResult(
            ok=True,
            pptx_path=pptx_path,
            warnings=_extract_report_warnings(report),
            report=report,
        )


def _wrap_html_page(html_page: str, *, template: HtmlTemplatePackage) -> str:
    """Return a full HTML page with a fixed PPT viewport."""
    clean_page = str(html_page or "").strip()
    if "<html" in clean_page[:300].lower():
        return clean_page
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    html, body {{
      width: {template.viewport_width}px;
      height: {template.viewport_height}px;
      margin: 0;
      padding: 0;
      overflow: hidden;
      position: relative;
      background: #ffffff;
      font-family: Aptos, Arial, "Microsoft YaHei", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    img {{ display: block; }}
    section.slide, section.generated-slide {{
      width: {template.viewport_width}px;
      height: {template.viewport_height}px;
      position: relative;
      overflow: hidden;
      margin: 0;
    }}
  </style>
</head>
<body>
{clean_page}
</body>
</html>
"""


def _read_json_report(report_path: Path) -> dict[str, Any]:
    """Read the converter report if the Node process wrote one."""
    if not report_path.exists():
        return {}
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_report_warnings(report: dict[str, Any]) -> list[str]:
    """Extract warnings from the Node converter report."""
    warnings = [str(item) for item in report.get("warnings") or [] if str(item).strip()]
    for page in report.get("pages") or []:
        slide_number = page.get("slideNumber") or page.get("slide_number") or "?"
        for warning in page.get("warnings") or []:
            clean_warning = str(warning).strip()
            if clean_warning:
                warnings.append(f"slide {slide_number}: {clean_warning}")
    return list(dict.fromkeys(warnings))


def _extract_report_errors(report: dict[str, Any]) -> list[str]:
    """Extract errors from the Node converter report."""
    errors = [str(item) for item in report.get("errors") or [] if str(item).strip()]
    for page in report.get("pages") or []:
        slide_number = page.get("slideNumber") or page.get("slide_number") or "?"
        for error in page.get("errors") or []:
            clean_error = str(error).strip()
            if clean_error:
                errors.append(f"slide {slide_number}: {clean_error}")
    return list(dict.fromkeys(errors))


def _looks_like_browser_converter_unavailable(output: str) -> bool:
    """Return whether Node output indicates an unavailable local browser runtime."""
    lowered = str(output or "").lower()
    return (
        "cannot find module" in lowered
        or "please run the following command to download new browsers" in lowered
        or "browserType.launch".lower() in lowered
        and ("permission denied" in lowered or "machportrendezvous" in lowered)
    )


def _project_root() -> str:
    """Return the Creative Claw project root for Node module resolution."""
    return str(Path(__file__).resolve().parents[5])
