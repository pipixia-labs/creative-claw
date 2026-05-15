"""Typed records for Page product built-in templates."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PageTemplate:
    """A built-in Page product template that can be injected into code generation."""

    id: str
    name: str
    description: str
    usage: str
    tags: tuple[str, ...]
    trigger_terms: tuple[str, ...]
    best_for: tuple[str, ...]
    avoid_for: tuple[str, ...]
    layout_rules: tuple[str, ...]
    style_rules: tuple[str, ...]
    content_rules: tuple[str, ...]
    quality_checks: tuple[str, ...]
    template_html: str
    source_dir: str

    def __post_init__(self) -> None:
        """Validate required template fields at import time."""
        if not self.id.strip():
            raise ValueError("PageTemplate.id must be non-empty.")
        if not self.name.strip():
            raise ValueError(f"PageTemplate {self.id!r} must have a name.")
        if not self.usage.strip():
            raise ValueError(f"PageTemplate {self.id!r} must have usage guidance.")
        if "<!DOCTYPE html>" not in self.template_html:
            raise ValueError(f"PageTemplate {self.id!r} must include a complete HTML template.")
        required_sequences = {
            "tags": self.tags,
            "trigger_terms": self.trigger_terms,
            "best_for": self.best_for,
            "layout_rules": self.layout_rules,
            "style_rules": self.style_rules,
            "content_rules": self.content_rules,
            "quality_checks": self.quality_checks,
        }
        for field_name, values in required_sequences.items():
            if not values or not all(str(value).strip() for value in values):
                raise ValueError(f"PageTemplate {self.id!r} has empty {field_name}.")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly template summary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "usage": self.usage,
            "tags": list(self.tags),
            "best_for": list(self.best_for),
            "avoid_for": list(self.avoid_for),
            "layout_rules": list(self.layout_rules),
            "style_rules": list(self.style_rules),
            "content_rules": list(self.content_rules),
            "quality_checks": list(self.quality_checks),
            "source_dir": self.source_dir,
            "template_html_available": bool(self.template_html.strip()),
        }

    def to_prompt_block(self) -> str:
        """Render this template as a compact prompt section."""
        sections = [
            ("Best for", self.best_for),
            ("Avoid for", self.avoid_for),
            ("Layout rules", self.layout_rules),
            ("Style rules", self.style_rules),
            ("Content rules", self.content_rules),
            ("Quality checks", self.quality_checks),
        ]
        lines = [
            f"Template ID: {self.id}",
            f"Template name: {self.name}",
            f"Template tags: {', '.join(self.tags)}",
            f"Template intent: {self.description}",
            f"Template usage: {self.usage}",
        ]
        for title, values in sections:
            if not values:
                continue
            lines.append(f"{title}:")
            lines.extend(f"- {value}" for value in values)
        lines.extend(
            [
                "HTML reference (adapt structure and style; do not copy demo placeholders verbatim):",
                "<template_html>",
                self.template_html.strip(),
                "</template_html>",
            ]
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class PageTemplateMatch:
    """Selection result for one Page template."""

    template: PageTemplate
    score: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly match summary."""
        return {
            "template": self.template.to_dict(),
            "score": self.score,
            "reasons": list(self.reasons),
        }
