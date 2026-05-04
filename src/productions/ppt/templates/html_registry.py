"""System HTML template registry for the PPT HTML route."""

from __future__ import annotations

from src.productions.ppt.schemas import HtmlTemplatePackage

_DEFAULT_PAGE_TYPES = {
    "cover": "cover",
    "toc": "toc",
    "chapter_start": "chapter-start",
    "chapter_content": "chapter-content",
    "ending": "ending",
}

_HTML_TEMPLATE_REGISTRY = {
    "clean_business": {
        "template_id": "clean_business",
        "label": "Clean Business",
        "version": "0.1.0",
        "viewport_width": 1280,
        "viewport_height": 720,
        "page_types": _DEFAULT_PAGE_TYPES,
        "pptx_strategy": "native_editable",
        "editability_notes": (
            "HTML route exports an editable PPTX with native text boxes and simple vector shapes. "
            "PNG previews are still generated for visual review."
        ),
    }
}


def list_html_templates() -> list[dict[str, object]]:
    """Return registered system HTML templates."""
    return [dict(template) for template in _HTML_TEMPLATE_REGISTRY.values()]


def load_html_template_package(
    template_id: str = "clean_business",
    *,
    aspect_ratio: str = "16:9",
) -> HtmlTemplatePackage:
    """Load one system HTML template package or raise a clear error."""
    clean_template_id = (template_id or "clean_business").strip()
    raw_template = _HTML_TEMPLATE_REGISTRY.get(clean_template_id)
    if raw_template is None:
        available = ", ".join(sorted(_HTML_TEMPLATE_REGISTRY))
        raise ValueError(f"Unknown HTML template `{clean_template_id}`. Available templates: {available}")
    if aspect_ratio not in {"16:9", "4:3"}:
        raise ValueError("HTML template aspect_ratio must be `16:9` or `4:3`.")
    payload = dict(raw_template)
    payload["aspect_ratio"] = aspect_ratio
    if aspect_ratio == "4:3":
        payload["viewport_width"] = 1024
        payload["viewport_height"] = 768
    return HtmlTemplatePackage(**payload)
