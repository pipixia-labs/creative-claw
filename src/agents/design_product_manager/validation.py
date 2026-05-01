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
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        self.tags.add(normalized_tag)
        if normalized_tag in {"script", "style"}:
            self._ignored_depth += 1

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


def validate_design_artifacts(paths: list[str]) -> list[dict[str, Any]]:
    """Validate multiple generated design artifact paths."""
    return [validate_design_artifact(path).to_dict() for path in paths]
