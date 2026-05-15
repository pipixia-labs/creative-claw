"""Filesystem loader for Page product HTML templates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.productions.page.page_product_manager.templates.models import PageTemplate

TEMPLATES_HTML_DIR = Path(__file__).resolve().parent.parent / "templates-html"


def load_page_templates_from_directory(
    templates_dir: str | Path = TEMPLATES_HTML_DIR,
) -> tuple[PageTemplate, ...]:
    """Load Page templates from `templates-html/<template_id>/` directories."""
    root = Path(templates_dir)
    if not root.exists():
        return ()

    templates: list[PageTemplate] = []
    for template_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        metadata_path = template_dir / "metadata.json"
        html_path = template_dir / "template.html"
        if not metadata_path.exists() or not html_path.exists():
            continue
        templates.append(_load_one_template(template_dir, metadata_path, html_path))
    return tuple(templates)


def _load_one_template(
    template_dir: Path,
    metadata_path: Path,
    html_path: Path,
) -> PageTemplate:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError(f"{metadata_path} must contain a JSON object.")

    template_id = _required_string(metadata, "id", metadata_path)
    if template_id != template_dir.name:
        raise ValueError(
            f"{metadata_path} id must match directory name {template_dir.name!r}."
        )

    return PageTemplate(
        id=template_id,
        name=_required_string(metadata, "name", metadata_path),
        description=_required_string(metadata, "description", metadata_path),
        usage=_required_string(metadata, "usage", metadata_path),
        tags=_required_string_tuple(metadata, "tags", metadata_path),
        trigger_terms=_required_string_tuple(metadata, "trigger_terms", metadata_path),
        best_for=_required_string_tuple(metadata, "best_for", metadata_path),
        avoid_for=_required_string_tuple(metadata, "avoid_for", metadata_path),
        layout_rules=_required_string_tuple(metadata, "layout_rules", metadata_path),
        style_rules=_required_string_tuple(metadata, "style_rules", metadata_path),
        content_rules=_required_string_tuple(metadata, "content_rules", metadata_path),
        quality_checks=_required_string_tuple(metadata, "quality_checks", metadata_path),
        template_html=html_path.read_text(encoding="utf-8").strip(),
        source_dir=str(template_dir),
    )


def _required_string(metadata: dict[str, Any], key: str, metadata_path: Path) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{metadata_path} field {key!r} must be a non-empty string.")
    return value.strip()


def _required_string_tuple(
    metadata: dict[str, Any],
    key: str,
    metadata_path: Path,
) -> tuple[str, ...]:
    value = metadata.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{metadata_path} field {key!r} must be a non-empty string list.")
    items = tuple(str(item).strip() for item in value if str(item).strip())
    if len(items) != len(value):
        raise ValueError(f"{metadata_path} field {key!r} must not contain empty items.")
    return items
