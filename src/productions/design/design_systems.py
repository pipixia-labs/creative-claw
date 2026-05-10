"""Design system registry and preview helpers for the design product line."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from conf.path import PROJECT_PATH

DESIGN_SYSTEMS_DIR = Path(PROJECT_PATH) / "src" / "productions" / "design" / "design-systems"


@dataclass(frozen=True, slots=True)
class DesignSystemSummary:
    """Lightweight metadata for one local design system."""

    id: str
    title: str
    summary: str
    swatches: list[str]
    preview_url: str
    dark_preview_url: str
    showcase_url: str

    def to_dict(self) -> dict[str, Any]:
        """Return a Web API friendly representation."""
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "swatches": self.swatches,
            "previewUrl": self.preview_url,
            "darkPreviewUrl": self.dark_preview_url,
            "showcaseUrl": self.showcase_url,
        }


def list_design_systems(root: Path | None = None) -> list[DesignSystemSummary]:
    """Return lightweight summaries for all local design systems."""
    base = root or DESIGN_SYSTEMS_DIR
    if not base.exists():
        return []

    summaries: list[DesignSystemSummary] = []
    for child in sorted(path for path in base.iterdir() if path.is_dir()):
        design_path = child / "DESIGN.md"
        if not design_path.is_file():
            continue
        raw = design_path.read_text(encoding="utf-8")
        system_id = child.name
        encoded_id = quote(system_id, safe="")
        summaries.append(
            DesignSystemSummary(
                id=system_id,
                title=_extract_scalar(raw, "name") or _title_from_slug(system_id),
                summary=_extract_scalar(raw, "description")[:260],
                swatches=_extract_swatches(raw),
                preview_url=f"/api/design-systems/{encoded_id}/preview",
                dark_preview_url=f"/api/design-systems/{encoded_id}/preview-dark",
                showcase_url=f"/api/design-systems/{encoded_id}/showcase",
            )
        )
    return summaries


def read_design_system(system_id: str, root: Path | None = None) -> str | None:
    """Read the full DESIGN.md body for one design system id."""
    design_path = _resolve_design_system_file(system_id, "DESIGN.md", root=root)
    if design_path is None:
        return None
    return design_path.read_text(encoding="utf-8")


def resolve_design_system_preview(
    system_id: str,
    *,
    dark: bool = False,
    root: Path | None = None,
) -> Path | None:
    """Resolve the local HTML preview file for one design system id."""
    filename = "preview-dark.html" if dark else "preview.html"
    return _resolve_design_system_file(system_id, filename, root=root)


def _resolve_design_system_file(system_id: str, filename: str, *, root: Path | None = None) -> Path | None:
    clean_id = str(system_id or "").strip()
    if not clean_id or "/" in clean_id or "\\" in clean_id or clean_id in {".", ".."}:
        return None
    base = root or DESIGN_SYSTEMS_DIR
    resolved = (base / clean_id / filename).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        return None
    if not resolved.is_file():
        return None
    return resolved


def _extract_scalar(raw: str, key: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.+?)\s*$", re.MULTILINE)
    match = pattern.search(raw)
    if not match:
        return ""
    return _clean_scalar(match.group(1))


def _clean_scalar(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    return text.strip()


def _extract_swatches(raw: str) -> list[str]:
    colors: list[tuple[str, str]] = []
    in_colors = False
    for line in raw.splitlines():
        if line.strip() == "colors:":
            in_colors = True
            continue
        if in_colors and line and not line.startswith((" ", "\t")):
            break
        if not in_colors:
            continue
        match = re.match(r"\s+([A-Za-z0-9_-]+):\s*['\"]?(#[0-9a-fA-F]{3,8})", line)
        if match:
            colors.append((match.group(1).lower(), _normalize_hex(match.group(2))))
    if not colors:
        return []

    def pick(*hints: str) -> str | None:
        for hint in hints:
            for name, color in colors:
                if hint in name:
                    return color
        return None

    picked = [
        pick("canvas", "background", "surface") or colors[0][1],
        pick("hairline", "border", "muted") or colors[min(1, len(colors) - 1)][1],
        pick("ink", "text", "body") or colors[min(2, len(colors) - 1)][1],
        pick("primary", "accent", "brand") or colors[min(3, len(colors) - 1)][1],
    ]
    out: list[str] = []
    for color in picked:
        if color not in out:
            out.append(color)
    return out[:4]


def _normalize_hex(value: str) -> str:
    raw = str(value or "").strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", raw):
        return "#" + "".join(char * 2 for char in raw[1:])
    return raw


def _title_from_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-_.]+", slug) if part) or slug
