"""Registry facade for PPT SVG layout templates."""

from __future__ import annotations

from src.productions.ppt.templates.svg.loader import (
    load_svg_layout_template,
    load_svg_layout_templates_from_directory,
)
from src.productions.ppt.templates.svg.models import SvgLayoutTemplate


def list_svg_layout_templates() -> list[dict[str, object]]:
    """Return registered system SVG layout templates."""
    return [
        template.to_summary_dict()
        for template in load_svg_layout_templates_from_directory()
    ]


def load_svg_layout_template_package(template_id: str) -> SvgLayoutTemplate:
    """Load one registered system SVG layout template package."""
    return load_svg_layout_template(template_id)
