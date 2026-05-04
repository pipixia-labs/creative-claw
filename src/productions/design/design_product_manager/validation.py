"""Deterministic validation helpers for generated design artifacts."""

from __future__ import annotations

import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from src.runtime.workspace import resolve_workspace_path, workspace_relative_path

_BROWSER_PREVIEW_TIMEOUT_MS = 8_000


@dataclass(frozen=True, slots=True)
class BrowserViewport:
    """One browser viewport used for generated design preview checks."""

    name: str
    width: int
    height: int


DEFAULT_BROWSER_VIEWPORTS: tuple[BrowserViewport, ...] = (
    BrowserViewport(name="desktop", width=1280, height=800),
    BrowserViewport(name="mobile", width=390, height=844),
)

BrowserPreviewRunner = Callable[[Path, tuple[BrowserViewport, ...]], dict[str, Any]]


@dataclass(slots=True)
class DesignArtifactValidation:
    """Validation result for one generated design artifact."""

    status: str
    path: str
    errors: list[str]
    warnings: list[str]
    checks: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe validation payload."""
        return {
            "status": self.status,
            "path": self.path,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "checks": dict(self.checks),
        }


class _VisibleTextParser(HTMLParser):
    """Extract visible-ish text while ignoring script and style content."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()
        self.text_parts: list[str] = []
        self.style_parts: list[str] = []
        self.script_parts: list[str] = []
        self.external_asset_refs: list[str] = []
        self.has_inline_style = False
        self.has_viewport_meta = False
        self._script_depth = 0
        self._style_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        self.tags.add(normalized_tag)
        attrs_by_name = {name.lower(): value or "" for name, value in attrs}
        if normalized_tag == "meta" and attrs_by_name.get("name", "").lower() == "viewport":
            self.has_viewport_meta = True
        if attrs_by_name.get("style", "").strip():
            self.has_inline_style = True
        for name in ("src", "href"):
            value = attrs_by_name.get(name, "").strip()
            if value.startswith(("http://", "https://")):
                self.external_asset_refs.append(value)
        if normalized_tag == "script":
            self._script_depth += 1
        elif normalized_tag == "style":
            self._style_depth += 1

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag == "script" and self._script_depth > 0:
            self._script_depth -= 1
        elif normalized_tag == "style" and self._style_depth > 0:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._script_depth:
            if data.strip():
                self.script_parts.append(data)
            return
        if self._style_depth:
            if data.strip():
                self.style_parts.append(data)
            return
        text = data.strip()
        if text:
            self.text_parts.append(text)


def validate_design_artifact(
    path: str | Path,
    *,
    browser_preview: bool = False,
    browser_viewports: tuple[BrowserViewport, ...] = DEFAULT_BROWSER_VIEWPORTS,
    browser_preview_runner: BrowserPreviewRunner | None = None,
) -> DesignArtifactValidation:
    """Validate existence, readability, and basic format of one design artifact."""
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {}

    try:
        resolved = resolve_workspace_path(path)
    except Exception as exc:
        return DesignArtifactValidation(
            status="error",
            path=str(path),
            errors=[f"path is not inside workspace: {type(exc).__name__}: {exc}"],
            warnings=[],
            checks={"workspace_path": False},
        )

    relative_path = workspace_relative_path(resolved)
    checks["workspace_path"] = True
    checks["exists"] = resolved.exists() and resolved.is_file()
    if not checks["exists"]:
        return DesignArtifactValidation(
            status="error",
            path=relative_path,
            errors=["artifact file does not exist"],
            warnings=[],
            checks=checks,
        )

    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return DesignArtifactValidation(
            status="error",
            path=relative_path,
            errors=[f"artifact is not readable as UTF-8 text: {exc}"],
            warnings=[],
            checks=checks,
        )

    lower_content = content.lower()
    checks["non_empty"] = bool(content.strip())
    checks["html_extension"] = resolved.suffix.lower() in {".html", ".htm"}
    checks["has_html_tag"] = "<html" in lower_content
    checks["has_body_tag"] = "<body" in lower_content
    checks["no_local_absolute_paths"] = not any(
        marker in content
        for marker in (
            "/Users/",
            "creative_claw_opensource",
            "pytorch_research",
            "basic_networks",
            "0_auto_agent",
        )
    )

    parser = _VisibleTextParser()
    try:
        parser.feed(content)
        checks["parseable_html"] = True
    except Exception as exc:
        checks["parseable_html"] = False
        errors.append(f"artifact is not parseable as HTML: {type(exc).__name__}: {exc}")

    visible_text = " ".join(parser.text_parts)
    checks["has_visible_text"] = bool(re.sub(r"\s+", "", visible_text))
    preview_quality = _build_preview_quality(parser=parser, content=content, visible_text=visible_text)
    checks.update(preview_quality["checks"])
    warnings.extend(preview_quality["warnings"])
    if browser_preview:
        preview_runner = browser_preview_runner or _run_playwright_browser_preview
        browser_quality = preview_runner(resolved, browser_viewports)
        checks.update(dict(browser_quality.get("checks") or {}))
        errors.extend(str(message) for message in browser_quality.get("errors", []) or [])
        warnings.extend(str(message) for message in browser_quality.get("warnings", []) or [])

    required_checks = {
        "non_empty": "artifact is empty",
        "html_extension": "artifact does not use an HTML extension",
        "has_html_tag": "artifact is missing an <html> tag",
        "has_body_tag": "artifact is missing a <body> tag",
        "parseable_html": "artifact is not parseable as HTML",
        "has_visible_text": "artifact has no visible text",
        "no_local_absolute_paths": "artifact contains local absolute path markers",
    }
    for check_name, message in required_checks.items():
        if not checks.get(check_name, False):
            errors.append(message)

    if errors:
        status = "error"
    elif warnings:
        status = "warning"
    else:
        status = "pass"
    return DesignArtifactValidation(
        status=status,
        path=relative_path,
        errors=errors,
        warnings=warnings,
        checks=checks,
    )


def _build_preview_quality(
    *,
    parser: _VisibleTextParser,
    content: str,
    visible_text: str,
) -> dict[str, Any]:
    """Return deterministic preview-quality signals for one HTML artifact."""
    lower_content = content.lower()
    style_text = "\n".join(parser.style_parts).lower()
    script_text = "\n".join(parser.script_parts)
    visible_character_count = len(re.sub(r"\s+", "", visible_text))
    responsive_markers = (
        "@media",
        "clamp(",
        "minmax(",
        "max-width",
        "min-width",
        "flex-wrap",
        "grid-template",
        "aspect-ratio",
        "vw",
        "vh",
    )
    checks = {
        "has_viewport_meta": parser.has_viewport_meta,
        "has_layout_css": bool(style_text.strip() or parser.has_inline_style),
        "has_responsive_signal": any(marker in lower_content for marker in responsive_markers),
        "has_semantic_structure": bool(
            parser.tags.intersection({"main", "section", "article", "nav", "header", "footer"})
        ),
        "has_meaningful_content": visible_character_count >= 60,
        "no_external_runtime_assets": not parser.external_asset_refs,
        "no_obvious_console_errors": not re.search(
            r"(throw\s+new\s+error|console\.error|referenceerror|typeerror)",
            script_text,
            flags=re.IGNORECASE,
        ),
    }
    warning_messages = {
        "has_viewport_meta": "preview quality: missing viewport meta tag",
        "has_layout_css": "preview quality: missing CSS/layout styling signal",
        "has_responsive_signal": "preview quality: no responsive layout signal found",
        "has_semantic_structure": "preview quality: missing semantic layout structure",
        "has_meaningful_content": "preview quality: visible content is too thin for preview validation",
        "no_external_runtime_assets": "preview quality: external runtime assets may be unavailable in local preview",
        "no_obvious_console_errors": "preview quality: script contains obvious console/error marker",
    }
    warnings = [message for check, message in warning_messages.items() if not checks.get(check, False)]
    return {
        "status": "warning" if warnings else "pass",
        "checks": checks,
        "warnings": warnings,
    }


def _run_playwright_browser_preview(
    path: Path,
    viewports: tuple[BrowserViewport, ...],
) -> dict[str, Any]:
    """Run optional Playwright checks for one local HTML artifact."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "checks": {
                "browser_preview_available": False,
                "browser_preview_checked": False,
            },
            "errors": [],
            "warnings": ["browser preview: Playwright is not installed; browser checks skipped"],
        }

    checks: dict[str, bool] = {
        "browser_preview_available": True,
        "browser_preview_checked": False,
    }
    errors: list[str] = []
    warnings: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for viewport in viewports:
                    viewport_result = _check_browser_viewport(
                        browser=browser,
                        path=path,
                        viewport=viewport,
                    )
                    checks.update(viewport_result["checks"])
                    errors.extend(viewport_result["errors"])
                    warnings.extend(viewport_result["warnings"])
            finally:
                browser.close()
        checks["browser_preview_checked"] = True
    except PlaywrightTimeoutError as exc:
        checks["browser_preview_checked"] = False
        errors.append(f"browser preview: timed out while opening artifact: {exc}")
    except PlaywrightError as exc:
        checks["browser_preview_checked"] = False
        errors.append(f"browser preview: Playwright failed: {exc}")
    return {
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }


def _check_browser_viewport(
    *,
    browser: Any,
    path: Path,
    viewport: BrowserViewport,
) -> dict[str, Any]:
    """Open one artifact viewport and return browser-rendered diagnostics."""
    checks: dict[str, bool] = {}
    errors: list[str] = []
    warnings: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    prefix = f"browser_{viewport.name}"
    page = browser.new_page(viewport={"width": viewport.width, "height": viewport.height})
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        response = page.goto(path.as_uri(), wait_until="networkidle", timeout=_BROWSER_PREVIEW_TIMEOUT_MS)
        checks[f"{prefix}_opens"] = response is None or bool(getattr(response, "ok", False))
        metrics = page.evaluate(
            """() => {
              const root = document.documentElement;
              const body = document.body;
              const elements = Array.from(body ? body.querySelectorAll("*") : []);
              const maxRight = elements.reduce((value, element) => {
                const rect = element.getBoundingClientRect();
                return Math.max(value, rect.right);
              }, 0);
              return {
                clientWidth: root ? root.clientWidth : 0,
                scrollWidth: root ? root.scrollWidth : 0,
                maxElementRight: maxRight,
                textLength: body && body.innerText ? body.innerText.trim().length : 0
              };
            }"""
        )
        screenshot = page.screenshot(full_page=False)
    finally:
        page.close()

    client_width = int(metrics.get("clientWidth", 0) or 0)
    scroll_width = int(metrics.get("scrollWidth", 0) or 0)
    max_element_right = float(metrics.get("maxElementRight", 0) or 0)
    text_length = int(metrics.get("textLength", 0) or 0)
    has_horizontal_overflow = scroll_width > client_width + 8 or max_element_right > client_width + 16
    has_non_blank_render = text_length > 0 and _screenshot_has_visual_content(screenshot)

    checks[f"{prefix}_no_console_errors"] = not console_errors and not page_errors
    checks[f"{prefix}_no_horizontal_overflow"] = not has_horizontal_overflow
    checks[f"{prefix}_non_blank_screenshot"] = has_non_blank_render
    if not checks[f"{prefix}_opens"]:
        errors.append(f"browser preview: {viewport.name} viewport did not open successfully")
    if console_errors:
        errors.append(f"browser preview: {viewport.name} console errors: {' | '.join(console_errors[:3])}")
    if page_errors:
        errors.append(f"browser preview: {viewport.name} page errors: {' | '.join(page_errors[:3])}")
    if has_horizontal_overflow:
        errors.append(
            "browser preview: "
            f"{viewport.name} has horizontal overflow "
            f"(document {scroll_width}px, viewport {client_width}px)"
        )
    if not has_non_blank_render:
        errors.append(f"browser preview: {viewport.name} screenshot appears blank")
    return {
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }


def _screenshot_has_visual_content(screenshot: bytes) -> bool:
    """Return whether a screenshot has enough pixel variance to be non-blank."""
    try:
        from PIL import Image, ImageStat

        image = Image.open(io.BytesIO(screenshot)).convert("RGB").resize((32, 32))
        extrema = ImageStat.Stat(image).extrema
        return sum(high - low for low, high in extrema) > 12
    except Exception:
        return True


def validate_design_artifacts(
    paths: list[str],
    *,
    browser_preview: bool = False,
    browser_preview_runner: BrowserPreviewRunner | None = None,
) -> list[dict[str, Any]]:
    """Validate multiple generated design artifact paths."""
    return [
        validate_design_artifact(
            path,
            browser_preview=browser_preview,
            browser_preview_runner=browser_preview_runner,
        ).to_dict()
        for path in paths
    ]
