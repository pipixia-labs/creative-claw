"""Filesystem loader for PPT SVG layout templates."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.productions.ppt.templates.svg.models import SvgLayoutTemplate

SVG_LAYOUTS_DIR = Path(__file__).resolve().parent / "layouts"

_PAGE_TYPE_FILES = {
    "cover": "01_cover.svg",
    "toc": "02_toc.svg",
    "chapter_start": "02_chapter.svg",
    "chapter": "02_chapter.svg",
    "content": "03_content.svg",
    "ending": "04_ending.svg",
}
_REQUIRED_PAGE_TYPES = ("cover", "chapter", "content", "ending")
_ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
_FONT_STACK_RE = re.compile(r"\*\*Font Stack\*\*:\s*`?([^`\n]+)`?")


def load_svg_layout_templates_from_directory(
    layouts_dir: str | Path = SVG_LAYOUTS_DIR,
) -> tuple[SvgLayoutTemplate, ...]:
    """Load all SVG layout templates from a ppt-master-style layouts directory."""
    root = Path(layouts_dir)
    if not root.exists() or not root.is_dir():
        return ()

    index = _load_index(root / "layouts_index.json")
    templates: list[SvgLayoutTemplate] = []
    for template_id, metadata in sorted(index.items(), key=lambda item: item[0].lower()):
        template_dir = root / template_id
        if not template_dir.is_dir():
            continue
        templates.append(_load_one_template(template_id, template_dir, metadata))
    return tuple(templates)


def load_svg_layout_template(
    template_id: str,
    layouts_dir: str | Path = SVG_LAYOUTS_DIR,
) -> SvgLayoutTemplate:
    """Load one SVG layout template by exact id."""
    clean_template_id = str(template_id or "").strip()
    if not clean_template_id:
        raise ValueError("SVG layout template_id cannot be empty.")

    for template in load_svg_layout_templates_from_directory(layouts_dir):
        if template.template_id == clean_template_id:
            return template
    available = ", ".join(template.template_id for template in load_svg_layout_templates_from_directory(layouts_dir))
    raise ValueError(f"Unknown SVG layout template `{clean_template_id}`. Available templates: {available}")


def _load_index(index_path: Path) -> dict[str, dict[str, Any]]:
    if not index_path.is_file():
        return {}
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{index_path} must contain a JSON object.")
    return {
        str(template_id): dict(metadata)
        for template_id, metadata in payload.items()
        if isinstance(metadata, dict)
    }


def _load_one_template(
    template_id: str,
    template_dir: Path,
    metadata: dict[str, Any],
) -> SvgLayoutTemplate:
    design_spec_path = template_dir / "design_spec.md"
    if not design_spec_path.is_file():
        raise ValueError(f"SVG layout template `{template_id}` is missing design_spec.md.")

    page_svgs = {
        page_type: template_dir / file_name
        for page_type, file_name in _PAGE_TYPE_FILES.items()
        if (template_dir / file_name).is_file()
    }
    missing = [page_type for page_type in _REQUIRED_PAGE_TYPES if page_type not in page_svgs]
    if missing:
        raise ValueError(f"SVG layout template `{template_id}` is missing page types: {', '.join(missing)}.")

    design_spec = design_spec_path.read_text(encoding="utf-8")
    return SvgLayoutTemplate(
        template_id=template_id,
        label=_metadata_string(metadata, "label") or template_id,
        summary=_metadata_string(metadata, "summary"),
        keywords=tuple(_metadata_list(metadata, "keywords")),
        source_dir=template_dir,
        design_spec_path=design_spec_path,
        page_svgs=page_svgs,
        asset_paths=tuple(
            sorted(
                path for path in template_dir.iterdir()
                if path.is_file() and path.suffix.lower() in _ASSET_EXTENSIONS
            )
        ),
        palette=tuple(_dedupe(_HEX_COLOR_RE.findall(design_spec))[:8]),
        font_family=_extract_font_family(design_spec),
    )


def _metadata_string(metadata: dict[str, Any], key: str) -> str:
    return str(metadata.get(key) or "").strip()


def _metadata_list(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _extract_font_family(design_spec: str) -> str:
    match = _FONT_STACK_RE.search(design_spec)
    if not match:
        return ""
    return match.group(1).strip()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.upper()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(item)
    return result
