"""Data models for PPT SVG layout templates."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SvgLayoutTemplate:
    """One system SVG layout template package."""

    template_id: str
    label: str
    summary: str
    keywords: tuple[str, ...]
    source_dir: Path
    design_spec_path: Path
    page_svgs: dict[str, Path]
    asset_paths: tuple[Path, ...] = field(default_factory=tuple)
    palette: tuple[str, ...] = field(default_factory=tuple)
    font_family: str = ""

    def to_summary_dict(self) -> dict[str, object]:
        """Return JSON-safe template metadata for listing and logs."""
        return {
            "template_id": self.template_id,
            "label": self.label,
            "summary": self.summary,
            "keywords": list(self.keywords),
            "page_types": sorted(self.page_svgs),
            "palette": list(self.palette),
            "font_family": self.font_family,
        }

    def to_prompt_context(
        self,
        *,
        max_design_spec_chars: int = 5000,
        max_svg_chars: int = 1200,
    ) -> dict[str, object]:
        """Return bounded template context for SVG design/executor agents."""
        design_spec = self.design_spec_path.read_text(encoding="utf-8") if self.design_spec_path.exists() else ""
        page_svg_excerpts: dict[str, str] = {}
        for page_type, path in sorted(self.page_svgs.items()):
            if not path.exists():
                continue
            page_svg_excerpts[page_type] = path.read_text(encoding="utf-8")[:max_svg_chars]
        return {
            **self.to_summary_dict(),
            "design_spec_excerpt": design_spec[:max_design_spec_chars],
            "page_svg_files": {
                page_type: path.name for page_type, path in sorted(self.page_svgs.items())
            },
            "page_svg_excerpts": page_svg_excerpts,
        }


@dataclass(frozen=True)
class SvgLayoutTemplateMatch:
    """Template selection result for one SVG route run."""

    use_template: bool
    template_id: str = ""
    score: int = 0
    reasons: tuple[str, ...] = field(default_factory=tuple)
    explicit: bool = False
    template: SvgLayoutTemplate | None = None
    fallback_reason: str = ""

    def to_dict(self, *, include_prompt_context: bool = False) -> dict[str, object]:
        """Return JSON-safe selection metadata."""
        payload: dict[str, object] = {
            "use_template": self.use_template,
            "template_id": self.template_id,
            "score": self.score,
            "reasons": list(self.reasons),
            "explicit": self.explicit,
            "fallback_reason": self.fallback_reason,
        }
        if self.template is not None:
            payload["template"] = (
                self.template.to_prompt_context()
                if include_prompt_context
                else self.template.to_summary_dict()
            )
        return payload
