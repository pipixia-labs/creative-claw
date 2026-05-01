"""Deterministic validation helpers for generated design artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


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


def validate_design_artifact(path: str | Path) -> DesignArtifactValidation:
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


def validate_design_artifacts(paths: list[str]) -> list[dict[str, Any]]:
    """Validate multiple generated design artifact paths."""
    return [validate_design_artifact(path).to_dict() for path in paths]
