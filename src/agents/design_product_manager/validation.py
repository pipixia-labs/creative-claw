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
    """Extract visible-ish text while tracking common HTML structure."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()
        self.text_parts: list[str] = []
        self.external_refs: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        self.tags.add(normalized_tag)
        if normalized_tag in {"script", "style"}:
            self._ignored_depth += 1
        for name, value in attrs:
            if name.lower() in {"src", "href"} and value and _is_external_reference(value):
                self.external_refs.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self._ignored_depth > 0:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = data.strip()
        if text:
            self.text_parts.append(text)


def validate_design_artifact(path: str | Path) -> DesignArtifactValidation:
    """Validate one generated HTML design artifact without launching a browser."""
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
    checks["has_viewport_meta"] = "name=\"viewport\"" in lower_content or "name='viewport'" in lower_content
    checks["has_inline_style"] = "<style" in lower_content
    checks["has_layout_css"] = any(token in lower_content for token in ("display: grid", "display:grid", "display: flex", "display:flex"))
    checks["has_responsive_css"] = "@media" in lower_content or "clamp(" in lower_content or "minmax(" in lower_content
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
    except Exception as exc:
        warnings.append(f"HTML parser warning: {type(exc).__name__}: {exc}")

    visible_text = " ".join(parser.text_parts)
    checks["has_visible_text"] = len(re.sub(r"\s+", "", visible_text)) >= 30
    checks["has_main_or_section"] = bool({"main", "section", "article"} & parser.tags)
    checks["no_external_runtime_refs"] = not parser.external_refs

    required_checks = {
        "non_empty": "artifact is empty",
        "html_extension": "artifact does not use an HTML extension",
        "has_html_tag": "artifact is missing an <html> tag",
        "has_body_tag": "artifact is missing a <body> tag",
        "has_visible_text": "artifact has too little visible text",
        "no_local_absolute_paths": "artifact contains local absolute path markers",
    }
    for check_name, message in required_checks.items():
        if not checks.get(check_name, False):
            errors.append(message)

    recommended_checks = {
        "has_viewport_meta": "missing viewport meta tag",
        "has_inline_style": "missing inline <style> block",
        "has_layout_css": "no obvious grid/flex layout CSS",
        "has_responsive_css": "no obvious responsive CSS",
        "has_main_or_section": "missing semantic main/section/article structure",
        "no_external_runtime_refs": "external src/href references found",
    }
    for check_name, message in recommended_checks.items():
        if not checks.get(check_name, False):
            warnings.append(message)

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


def validate_design_artifacts(paths: list[str]) -> list[dict[str, Any]]:
    """Validate multiple generated design artifact paths."""
    return [validate_design_artifact(path).to_dict() for path in paths]


def _is_external_reference(value: str) -> bool:
    """Return whether an HTML src/href value points to an external runtime dependency."""
    lowered = value.strip().lower()
    return lowered.startswith(("http://", "https://", "//"))
