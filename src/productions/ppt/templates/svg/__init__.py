"""SVG template helpers for PPT product routes."""

from src.productions.ppt.templates.svg.loader import (
    SVG_LAYOUTS_DIR,
    load_svg_layout_template,
    load_svg_layout_templates_from_directory,
)
from src.productions.ppt.templates.svg.models import (
    SvgLayoutTemplate,
    SvgLayoutTemplateMatch,
)
from src.productions.ppt.templates.svg.registry import (
    list_svg_layout_templates,
    load_svg_layout_template_package,
)
from src.productions.ppt.templates.svg.selector import select_svg_layout_template_match

__all__ = [
    "SVG_LAYOUTS_DIR",
    "SvgLayoutTemplate",
    "SvgLayoutTemplateMatch",
    "list_svg_layout_templates",
    "load_svg_layout_template",
    "load_svg_layout_template_package",
    "load_svg_layout_templates_from_directory",
    "select_svg_layout_template_match",
]
