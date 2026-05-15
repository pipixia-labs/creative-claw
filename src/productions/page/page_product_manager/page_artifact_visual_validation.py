"""Visual validation helpers for PageProductManager HTML artifacts."""

from __future__ import annotations

import base64
import io
import json
import re
import shutil
import subprocess
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

_PAGE_BROWSER_TIMEOUT_SECONDS = 12


@dataclass(frozen=True, slots=True)
class PageVisualViewport:
    """One browser viewport used for page visual checks."""

    name: str
    width: int
    height: int


DEFAULT_PAGE_VISUAL_VIEWPORTS: tuple[PageVisualViewport, ...] = (
    PageVisualViewport(name="poster", width=1080, height=1920),
    PageVisualViewport(name="mobile", width=390, height=844),
)

PageVisualPreviewRunner = Callable[[Path, tuple[PageVisualViewport, ...]], dict[str, Any]]

_REVIEW_BOARD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"方案\s*[一二三四五六七八九十123456789]", re.IGNORECASE),
    re.compile(r"[A-D]\s*版", re.IGNORECASE),
    re.compile(r"设计方向|方向稿|多方案|方案对比|评审画布", re.IGNORECASE),
    re.compile(r"\b(?:option|variant|direction|review\s+board|style\s+comparison)\b", re.IGNORECASE),
)


def validate_page_visual_artifact(
    path: Path,
    *,
    content: str,
    browser_preview: bool = True,
    browser_preview_runner: PageVisualPreviewRunner | None = None,
    browser_viewports: tuple[PageVisualViewport, ...] = DEFAULT_PAGE_VISUAL_VIEWPORTS,
) -> dict[str, Any]:
    """Return deterministic visual-quality checks for one page HTML artifact."""
    checks: dict[str, bool] = {}
    errors: list[str] = []
    warnings: list[str] = []

    static_result = _run_static_visual_checks(content)
    checks.update(static_result["checks"])
    warnings.extend(static_result["warnings"])

    if browser_preview:
        runner = browser_preview_runner or _run_node_playwright_page_preview
        browser_result = runner(path, browser_viewports)
        checks.update(dict(browser_result.get("checks") or {}))
        errors.extend(str(message) for message in browser_result.get("errors", []) or [])
        warnings.extend(str(message) for message in browser_result.get("warnings", []) or [])
    else:
        checks["browser_preview_checked"] = False

    return {
        "status": "error" if errors else "warning" if warnings else "success",
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }


def _run_static_visual_checks(content: str) -> dict[str, Any]:
    """Return static visual signals that do not require a browser."""
    compact_content = re.sub(r"\s+", " ", content)
    review_board_matches: list[str] = []
    for pattern in _REVIEW_BOARD_PATTERNS:
        review_board_matches.extend(pattern.findall(compact_content))

    has_html_image = bool(re.search(r"<img\b|<picture\b|<svg\b|<canvas\b", content, re.IGNORECASE))
    has_css_image = bool(re.search(r"background(?:-image)?\s*:", content, re.IGNORECASE))
    has_hero_signal = bool(re.search(r"hero|poster|cover|headline|主视觉|海报", content, re.IGNORECASE))
    has_visual_signal = has_html_image or has_css_image or has_hero_signal
    has_headline_signal = bool(
        re.search(r"<h1\b|<h2\b|class\s*=\s*[\"'][^\"']*(?:headline|title|hero)", content, re.IGNORECASE)
    )
    no_review_board_signals = len(review_board_matches) < 2

    warnings: list[str] = []
    if not no_review_board_signals:
        warnings.append(
            "visual validation: output has multiple review-board or option-comparison signals"
        )

    return {
        "checks": {
            "visual_has_visual_signal": has_visual_signal,
            "visual_has_headline_signal": has_headline_signal,
            "visual_no_review_board_signals": no_review_board_signals,
        },
        "warnings": warnings,
    }


def _run_node_playwright_page_preview(
    path: Path,
    viewports: tuple[PageVisualViewport, ...],
) -> dict[str, Any]:
    """Run optional browser-rendered checks for one local HTML artifact."""
    node_bin = shutil.which("node")
    if not node_bin:
        return _browser_unavailable("Node.js is not available; browser visual checks skipped")

    project_root = Path(__file__).resolve().parents[4]
    if not (project_root / "node_modules" / "playwright").exists():
        return _browser_unavailable("Node Playwright dependency is not installed; browser visual checks skipped")

    script = _node_playwright_script()
    try:
        completed = subprocess.run(
            [
                node_bin,
                "-e",
                script,
                str(path),
                json.dumps([asdict(viewport) for viewport in viewports]),
            ],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=_PAGE_BROWSER_TIMEOUT_SECONDS + 3,
        )
    except subprocess.TimeoutExpired:
        return _browser_unavailable("browser visual checks timed out and were skipped")

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        return _browser_unavailable(
            f"browser visual checks failed to start: {message[:300] or 'unknown error'}"
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return _browser_unavailable("browser visual checks returned unreadable diagnostics")

    if not payload.get("ok", False):
        return _browser_unavailable(str(payload.get("message") or "browser visual checks failed"))

    return _normalize_browser_payload(payload)


def _browser_unavailable(message: str) -> dict[str, Any]:
    """Return a non-blocking warning when browser diagnostics cannot run."""
    return {
        "checks": {
            "browser_preview_available": False,
            "browser_preview_checked": False,
        },
        "errors": [],
        "warnings": [f"visual validation: {message}"],
    }


def _normalize_browser_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert browser diagnostics from Node into page validation checks."""
    checks: dict[str, bool] = {
        "browser_preview_available": True,
        "browser_preview_checked": True,
    }
    errors: list[str] = []
    warnings: list[str] = []

    for result in payload.get("results", []) or []:
        name = str(result.get("name") or "viewport")
        prefix = f"browser_{name}"
        metrics = dict(result.get("metrics") or {})
        broken_images = list(metrics.get("brokenImages") or [])
        overflow_text = list(metrics.get("overflowText") or [])
        client_width = int(metrics.get("clientWidth") or 0)
        scroll_width = int(metrics.get("scrollWidth") or 0)
        max_right = float(metrics.get("maxElementRight") or 0)
        heading_count = int(metrics.get("headingCount") or 0)
        first_screen_text_length = int(metrics.get("firstScreenTextLength") or 0)

        has_horizontal_overflow = scroll_width > client_width + 8 or max_right > client_width + 16
        has_text_overflow = bool(overflow_text)
        screenshot = _decode_screenshot(result.get("screenshotBase64"))
        has_non_blank_render = _screenshot_has_visual_content(screenshot)

        checks[f"{prefix}_opens"] = bool(result.get("opened", False))
        checks[f"{prefix}_no_broken_images"] = not broken_images
        checks[f"{prefix}_no_horizontal_overflow"] = not has_horizontal_overflow
        checks[f"{prefix}_no_text_overflow"] = not has_text_overflow
        checks[f"{prefix}_first_screen_focus_signal"] = heading_count > 0 and first_screen_text_length > 12
        checks[f"{prefix}_non_blank_screenshot"] = has_non_blank_render

        if not checks[f"{prefix}_opens"]:
            warnings.append(f"visual validation: {name} viewport did not open successfully")
        if broken_images:
            errors.append(f"visual validation: {name} viewport has broken images")
        if has_horizontal_overflow:
            warnings.append(
                "visual validation: "
                f"{name} viewport has horizontal overflow "
                f"(document {scroll_width}px, viewport {client_width}px)"
            )
        if has_text_overflow:
            warnings.append(f"visual validation: {name} viewport has text overflow")
        if not checks[f"{prefix}_first_screen_focus_signal"]:
            warnings.append(f"visual validation: {name} first screen has weak headline/focus signal")
        if not has_non_blank_render:
            errors.append(f"visual validation: {name} screenshot appears blank")

    for warning in payload.get("warnings", []) or []:
        warnings.append(f"visual validation: {warning}")
    for error in payload.get("errors", []) or []:
        errors.append(f"visual validation: {error}")

    return {
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }


def _decode_screenshot(value: Any) -> bytes:
    """Decode a base64 screenshot payload from Node."""
    if not isinstance(value, str) or not value:
        return b""
    try:
        return base64.b64decode(value)
    except Exception:
        return b""


def _screenshot_has_visual_content(screenshot: bytes) -> bool:
    """Return whether a screenshot has enough pixel variance to be non-blank."""
    if not screenshot:
        return False
    try:
        from PIL import Image, ImageStat

        image = Image.open(io.BytesIO(screenshot)).convert("RGB").resize((32, 32))
        extrema = ImageStat.Stat(image).extrema
        return sum(high - low for low, high in extrema) > 12
    except Exception:
        return True


def _node_playwright_script() -> str:
    """Return the Node Playwright script used for page diagnostics."""
    return textwrap.dedent(
        r"""
        const { chromium } = require("playwright");
        const { pathToFileURL } = require("url");

        const htmlPath = process.argv[1];
        const viewports = JSON.parse(process.argv[2] || "[]");

        function unique(values) {
          return Array.from(new Set(values.filter(Boolean)));
        }

        (async () => {
          const browser = await chromium.launch({ headless: true });
          const results = [];
          const errors = [];
          const warnings = [];
          try {
            for (const viewport of viewports) {
              const page = await browser.newPage({
                viewport: { width: viewport.width, height: viewport.height },
              });
              const consoleErrors = [];
              const pageErrors = [];
              page.on("console", (message) => {
                if (message.type() === "error") consoleErrors.push(message.text());
              });
              page.on("pageerror", (error) => pageErrors.push(String(error)));
              let opened = false;
              try {
                const response = await page.goto(pathToFileURL(htmlPath).href, {
                  waitUntil: "networkidle",
                  timeout: 10000,
                });
                opened = !response || response.ok();
              } catch (error) {
                errors.push(`${viewport.name}: ${error.message || String(error)}`);
              }

              const metrics = await page.evaluate(() => {
                const root = document.documentElement;
                const body = document.body;
                const isVisible = (element) => {
                  const style = window.getComputedStyle(element);
                  const rect = element.getBoundingClientRect();
                  return (
                    style.display !== "none" &&
                    style.visibility !== "hidden" &&
                    rect.width > 0 &&
                    rect.height > 0
                  );
                };
                const elements = Array.from(body ? body.querySelectorAll("*") : []).filter(isVisible);
                const textElements = elements.filter((element) => {
                  const tag = element.tagName.toLowerCase();
                  return tag !== "script" && tag !== "style" && (element.innerText || "").trim().length > 0;
                });
                const overflowText = textElements
                  .filter((element) => element.scrollWidth > element.clientWidth + 2 || element.scrollHeight > element.clientHeight + 2)
                  .slice(0, 5)
                  .map((element) => ({
                    tag: element.tagName.toLowerCase(),
                    text: (element.innerText || "").trim().slice(0, 80),
                  }));
                const images = Array.from(document.images || []);
                const brokenImages = images
                  .filter((image) => !image.complete || image.naturalWidth === 0 || image.naturalHeight === 0)
                  .map((image) => image.getAttribute("src") || "")
                  .slice(0, 5);
                const maxElementRight = elements.reduce((value, element) => {
                  const rect = element.getBoundingClientRect();
                  return Math.max(value, rect.right);
                }, 0);
                const headings = Array.from(
                  document.querySelectorAll("h1,h2,.headline,.title,[class*='title'],[class*='headline'],[class*='hero']")
                ).filter(isVisible);
                const firstScreenElements = elements.filter((element) => {
                  const rect = element.getBoundingClientRect();
                  return rect.top < window.innerHeight && rect.bottom > 0;
                });
                const firstScreenTextLength = firstScreenElements
                  .map((element) => (element.innerText || "").trim())
                  .join(" ")
                  .replace(/\s+/g, "").length;
                return {
                  clientWidth: root ? root.clientWidth : 0,
                  scrollWidth: root ? root.scrollWidth : 0,
                  maxElementRight,
                  bodyTextLength: body && body.innerText ? body.innerText.trim().length : 0,
                  brokenImages,
                  overflowText,
                  headingCount: headings.length,
                  firstScreenTextLength,
                };
              });
              metrics.consoleErrors = unique(consoleErrors).slice(0, 3);
              metrics.pageErrors = unique(pageErrors).slice(0, 3);
              if (metrics.consoleErrors.length) warnings.push(`${viewport.name} console errors: ${metrics.consoleErrors.join(" | ")}`);
              if (metrics.pageErrors.length) warnings.push(`${viewport.name} page errors: ${metrics.pageErrors.join(" | ")}`);
              const screenshot = await page.screenshot({ fullPage: false });
              results.push({
                name: viewport.name,
                opened,
                metrics,
                screenshotBase64: screenshot.toString("base64"),
              });
              await page.close();
            }
          } finally {
            await browser.close();
          }
          process.stdout.write(JSON.stringify({ ok: true, results, errors, warnings }));
        })().catch((error) => {
          process.stdout.write(JSON.stringify({ ok: false, message: error.message || String(error) }));
        });
        """
    )


__all__ = [
    "DEFAULT_PAGE_VISUAL_VIEWPORTS",
    "PageVisualPreviewRunner",
    "PageVisualViewport",
    "validate_page_visual_artifact",
]
