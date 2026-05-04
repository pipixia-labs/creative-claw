"""Deterministic HTML route MVP for PPT generation."""

from __future__ import annotations

import html
import json
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_AUTO_SIZE, MSO_VERTICAL_ANCHOR
from pptx.util import Inches, Pt

from src.productions.ppt.schemas import (
    DeckContentPlan,
    DeckPagePlan,
    HtmlRouteBuildPackage,
    HtmlTemplatePackage,
)
from src.productions.ppt.templates.html_registry import load_html_template_package
from src.runtime.workspace import workspace_relative_path

_COLOR_BG = RGBColor(247, 248, 251)
_COLOR_INK = RGBColor(23, 32, 51)
_COLOR_MUTED = RGBColor(102, 112, 133)
_COLOR_ACCENT = RGBColor(36, 87, 214)
_COLOR_ACCENT_2 = RGBColor(67, 166, 255)
_COLOR_LINE = RGBColor(219, 226, 239)
_COLOR_PANEL = RGBColor(255, 255, 255)
_COLOR_WHITE = RGBColor(255, 255, 255)
_FONT_FAMILY = "Aptos"


def build_html_route(
    *,
    content_plan: DeckContentPlan,
    output_dir: Path,
    aspect_ratio: str = "16:9",
    template_id: str = "clean_business",
) -> HtmlRouteBuildPackage:
    """Generate static HTML, PNG previews, and editable PPTX."""
    template = load_html_template_package(template_id, aspect_ratio=aspect_ratio)
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = output_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)

    html_path = output_dir / "deck.html"
    pptx_path = output_dir / "deck.pptx"
    quality_report_path = output_dir / "quality_report.json"
    build_log_path = output_dir / "build_log.json"

    html_path.write_text(_render_html_deck(content_plan, template), encoding="utf-8")
    preview_paths = _render_previews(content_plan, template, preview_dir)
    if template.pptx_strategy == "native_editable":
        _export_native_pptx(content_plan, pptx_path, template)
    else:
        _export_previews_to_pptx(preview_paths, pptx_path, aspect_ratio=template.aspect_ratio)
    quality_report = _validate_html_route_output(
        content_plan=content_plan,
        html_path=html_path,
        preview_paths=preview_paths,
        pptx_path=pptx_path,
    )
    quality_report_path.write_text(
        json.dumps(quality_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    build_log = {
        "route": "html",
        "template_id": template.template_id,
        "pptx_strategy": template.pptx_strategy,
        "slide_count": len(content_plan.pages),
        "html_deck_path": workspace_relative_path(html_path),
        "pptx_path": workspace_relative_path(pptx_path),
        "preview_count": len(preview_paths),
    }
    build_log_path.write_text(json.dumps(build_log, ensure_ascii=False, indent=2), encoding="utf-8")

    warnings = []
    if template.pptx_strategy == "screenshot":
        warnings.append(template.editability_notes)

    return HtmlRouteBuildPackage(
        template=template,
        html_deck_path=workspace_relative_path(html_path),
        preview_paths=[workspace_relative_path(path) for path in preview_paths],
        pptx_path=workspace_relative_path(pptx_path),
        quality_report_path=workspace_relative_path(quality_report_path),
        build_log_path=workspace_relative_path(build_log_path),
        warnings=warnings,
    )


def _render_html_deck(content_plan: DeckContentPlan, template: HtmlTemplatePackage) -> str:
    """Render one complete static HTML deck."""
    slides_html = "\n".join(_render_html_slide(page, template) for page in content_plan.pages)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(content_plan.title)}</title>
  <style>
    :root {{
      --bg: #f7f8fb;
      --ink: #172033;
      --muted: #667085;
      --panel: #ffffff;
      --accent: #2457d6;
      --accent-soft: #e8eefc;
      --line: #dbe2ef;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #d7dce8;
      color: var(--ink);
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .deck {{
      display: grid;
      gap: 32px;
      padding: 32px;
    }}
    .slide {{
      width: {template.viewport_width}px;
      height: {template.viewport_height}px;
      background: var(--bg);
      border: 1px solid var(--line);
      overflow: hidden;
      position: relative;
      padding: 58px 72px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }}
    .slide::after {{
      content: attr(data-slide-number);
      position: absolute;
      right: 34px;
      bottom: 24px;
      color: #98a2b3;
      font-size: 18px;
    }}
    .eyebrow {{
      color: var(--accent);
      font-size: 17px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
      margin-bottom: 18px;
    }}
    h1 {{
      font-size: 56px;
      line-height: 1.06;
      margin: 0;
      max-width: 900px;
    }}
    h2 {{
      font-size: 40px;
      line-height: 1.12;
      margin: 0 0 24px;
      max-width: 960px;
    }}
    .takeaway {{
      color: var(--muted);
      font-size: 24px;
      line-height: 1.35;
      max-width: 900px;
      margin-top: 20px;
    }}
    .content-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 22px;
      margin-top: 24px;
    }}
    .block {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px 24px;
      min-height: 130px;
    }}
    .block-title {{
      font-size: 21px;
      font-weight: 760;
      margin-bottom: 10px;
    }}
    .block-body {{
      font-size: 18px;
      line-height: 1.38;
      color: var(--muted);
    }}
    .toc-list {{
      display: grid;
      gap: 16px;
      margin-top: 30px;
      max-width: 820px;
    }}
    .toc-item {{
      display: grid;
      grid-template-columns: 54px 1fr;
      gap: 16px;
      align-items: start;
      padding: 18px 0;
      border-top: 1px solid var(--line);
    }}
    .toc-index {{
      color: var(--accent);
      font-size: 24px;
      font-weight: 780;
    }}
    .visual-band {{
      height: 92px;
      border-radius: 8px;
      background: linear-gradient(90deg, var(--accent), #43a6ff);
      margin-top: 34px;
    }}
  </style>
</head>
<body data-deck-template="{html.escape(template.template_id)}">
  <main class="deck">
{slides_html}
  </main>
</body>
</html>
"""


def _render_html_slide(page: DeckPagePlan, template: HtmlTemplatePackage) -> str:
    """Render one HTML slide from a page plan."""
    layout = template.page_types.get(page.page_type, "chapter-content")
    blocks = page.content_blocks or [{"title": "Core point", "body": page.key_takeaway}]
    block_html = "\n".join(_render_content_block(block) for block in blocks[:4])
    toc_html = ""
    if page.page_type == "toc":
        toc_html = _render_toc_list(blocks)
    content = toc_html or f'<div class="content-grid">{block_html}</div>'
    visual = '<div class="visual-band"></div>' if page.asset_intent else ""
    return f"""    <section class="slide slide-{html.escape(page.page_type)}" data-layout="{html.escape(layout)}" data-slide-number="{page.slide_number:02d}">
      <div>
        <div class="eyebrow">{html.escape(page.page_type.replace("_", " "))}</div>
        <h2>{html.escape(page.title)}</h2>
        <div class="takeaway">{html.escape(page.key_takeaway)}</div>
        {content}
      </div>
      {visual}
    </section>"""


def _render_content_block(block: dict[str, Any]) -> str:
    """Render one content block for a slide."""
    title = str(block.get("title") or block.get("heading") or "Point").strip()
    body = str(block.get("body") or block.get("text") or block.get("description") or "").strip()
    return f"""<div class="block">
  <div class="block-title">{html.escape(title)}</div>
  <div class="block-body">{html.escape(body)}</div>
</div>"""


def _render_toc_list(blocks: list[dict[str, Any]]) -> str:
    """Render a table of contents from planned chapter blocks."""
    items = []
    for index, block in enumerate(blocks[:6], start=1):
        title = str(block.get("title") or block.get("heading") or f"Chapter {index}").strip()
        body = str(block.get("body") or block.get("text") or block.get("description") or "").strip()
        items.append(
            f"""        <div class="toc-item"><div class="toc-index">{index:02d}</div><div><div class="block-title">{html.escape(title)}</div><div class="block-body">{html.escape(body)}</div></div></div>"""
        )
    return "      <div class=\"toc-list\">\n" + "\n".join(items) + "\n      </div>"


def _render_previews(
    content_plan: DeckContentPlan,
    template: HtmlTemplatePackage,
    preview_dir: Path,
) -> list[Path]:
    """Render simple slide preview PNGs for screenshot-style PPTX export."""
    preview_paths: list[Path] = []
    for page in content_plan.pages:
        image = Image.new("RGB", (template.viewport_width, template.viewport_height), "#F7F8FB")
        draw = ImageDraw.Draw(image)
        _draw_slide_preview(draw, page, template)
        preview_path = preview_dir / f"slide_{page.slide_number:03d}.png"
        image.save(preview_path)
        preview_paths.append(preview_path)
    return preview_paths


def _draw_slide_preview(
    draw: ImageDraw.ImageDraw,
    page: DeckPagePlan,
    template: HtmlTemplatePackage,
) -> None:
    """Draw one preview slide with deterministic typography and layout."""
    width = template.viewport_width
    height = template.viewport_height
    margin_x = 72 if template.aspect_ratio == "16:9" else 62
    top = 54
    accent = "#2457D6"
    ink = "#172033"
    muted = "#667085"
    line = "#DBE2EF"
    panel = "#FFFFFF"

    title_font = _load_font(42 if template.aspect_ratio == "16:9" else 36, bold=True)
    body_font = _load_font(21 if template.aspect_ratio == "16:9" else 18)
    label_font = _load_font(16, bold=True)
    small_font = _load_font(15)

    draw.text((margin_x, top), page.page_type.replace("_", " ").upper(), fill=accent, font=label_font)
    title_y = top + 38
    for line_text in _wrap_text(page.title, width=26 if template.aspect_ratio == "16:9" else 22)[:2]:
        draw.text((margin_x, title_y), line_text, fill=ink, font=title_font)
        title_y += 50

    takeaway_y = title_y + 14
    for line_text in _wrap_text(page.key_takeaway, width=42 if template.aspect_ratio == "16:9" else 34)[:3]:
        draw.text((margin_x, takeaway_y), line_text, fill=muted, font=body_font)
        takeaway_y += 30

    blocks = page.content_blocks or [{"title": "Core point", "body": page.key_takeaway}]
    grid_top = min(takeaway_y + 36, height - 300)
    card_gap = 22
    card_width = int((width - margin_x * 2 - card_gap) / 2)
    card_height = 128 if template.aspect_ratio == "16:9" else 112
    for index, block in enumerate(blocks[:4]):
        col = index % 2
        row = index // 2
        x = margin_x + col * (card_width + card_gap)
        y = grid_top + row * (card_height + card_gap)
        draw.rounded_rectangle((x, y, x + card_width, y + card_height), radius=8, fill=panel, outline=line)
        block_title = str(block.get("title") or block.get("heading") or "Point")
        block_body = str(block.get("body") or block.get("text") or block.get("description") or "")
        draw.text((x + 22, y + 18), _truncate(block_title, 30), fill=ink, font=label_font)
        body_y = y + 50
        for line_text in _wrap_text(block_body, width=34)[:2]:
            draw.text((x + 22, body_y), line_text, fill=muted, font=small_font)
            body_y += 23

    band_y = height - 104
    draw.rounded_rectangle((margin_x, band_y, width - margin_x, band_y + 58), radius=8, fill=accent)
    draw.text((margin_x + 20, band_y + 17), page.asset_intent or "HTML route preview", fill="#FFFFFF", font=small_font)
    draw.text((width - margin_x - 38, height - 40), f"{page.slide_number:02d}", fill="#98A2B3", font=small_font)


def _export_previews_to_pptx(preview_paths: list[Path], pptx_path: Path, *, aspect_ratio: str) -> None:
    """Create a screenshot-style PPTX from preview images."""
    prs = Presentation()
    if aspect_ratio == "4:3":
        prs.slide_width = Inches(10)
        prs.slide_height = Inches(7.5)
    else:
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

    blank_layout = prs.slide_layouts[6]
    for preview_path in preview_paths:
        slide = prs.slides.add_slide(blank_layout)
        slide.shapes.add_picture(
            str(preview_path),
            0,
            0,
            width=prs.slide_width,
            height=prs.slide_height,
        )
    prs.save(pptx_path)


def _export_native_pptx(
    content_plan: DeckContentPlan,
    pptx_path: Path,
    template: HtmlTemplatePackage,
) -> None:
    """Create an editable PPTX from the HTML route content plan."""
    prs = Presentation()
    slide_width, slide_height = _configure_slide_size(prs, template.aspect_ratio)
    blank_layout = prs.slide_layouts[6]
    for page in content_plan.pages:
        slide = prs.slides.add_slide(blank_layout)
        _draw_native_slide(slide, page, slide_width=slide_width, slide_height=slide_height)
    prs.save(pptx_path)


def _configure_slide_size(prs: Presentation, aspect_ratio: str) -> tuple[float, float]:
    """Configure slide size and return dimensions in inches."""
    if aspect_ratio == "4:3":
        width, height = 10.0, 7.5
    else:
        width, height = 13.333, 7.5
    prs.slide_width = Inches(width)
    prs.slide_height = Inches(height)
    return width, height


def _draw_native_slide(
    slide: Any,
    page: DeckPagePlan,
    *,
    slide_width: float,
    slide_height: float,
) -> None:
    """Draw one editable PPTX slide from a page plan."""
    _set_slide_background(slide)
    margin_x = 0.72 if slide_width > 11 else 0.58
    top = 0.48
    _add_text_box(
        slide,
        page.page_type.replace("_", " ").upper(),
        margin_x,
        top,
        3.2,
        0.26,
        font_size=10,
        bold=True,
        color=_COLOR_ACCENT,
    )

    if page.page_type == "cover":
        _draw_cover_slide(slide, page, slide_width, slide_height, margin_x)
    elif page.page_type == "toc":
        _draw_toc_slide(slide, page, slide_width, margin_x)
    elif page.page_type == "chapter_start":
        _draw_chapter_start_slide(slide, page, slide_width, slide_height, margin_x)
    else:
        _draw_content_slide(slide, page, slide_width, slide_height, margin_x)

    _draw_footer(slide, page, slide_width, slide_height, margin_x)


def _draw_cover_slide(
    slide: Any,
    page: DeckPagePlan,
    slide_width: float,
    slide_height: float,
    margin_x: float,
) -> None:
    """Draw an editable cover slide."""
    title_width = min(9.3, slide_width - margin_x * 2)
    _add_text_box(slide, page.title, margin_x, 1.35, title_width, 1.35, font_size=34, bold=True)
    _add_text_box(slide, page.key_takeaway, margin_x, 3.0, title_width, 0.9, font_size=18, color=_COLOR_MUTED)
    _draw_visual_band(slide, margin_x, slide_height - 1.22, slide_width - margin_x * 2, 0.5)
    _draw_content_cards(slide, page.content_blocks[:2], margin_x, 4.18, slide_width - margin_x * 2, 1.1)


def _draw_toc_slide(slide: Any, page: DeckPagePlan, slide_width: float, margin_x: float) -> None:
    """Draw an editable table-of-contents slide."""
    _add_text_box(slide, page.title, margin_x, 1.04, slide_width - margin_x * 2, 0.55, font_size=28, bold=True)
    _add_text_box(slide, page.key_takeaway, margin_x, 1.7, slide_width - margin_x * 2, 0.55, font_size=15, color=_COLOR_MUTED)
    y = 2.55
    for index, block in enumerate((page.content_blocks or [])[:5], start=1):
        title = _block_title(block, fallback=f"Chapter {index}")
        body = _block_body(block)
        _add_text_box(slide, f"{index:02d}", margin_x, y + 0.06, 0.45, 0.3, font_size=15, bold=True, color=_COLOR_ACCENT)
        _add_line(slide, margin_x + 0.72, y, slide_width - margin_x, y)
        _add_text_box(slide, title, margin_x + 0.72, y + 0.12, 4.5, 0.32, font_size=16, bold=True)
        _add_text_box(slide, body, margin_x + 5.2, y + 0.12, slide_width - margin_x - 5.2, 0.44, font_size=11, color=_COLOR_MUTED)
        y += 0.72


def _draw_chapter_start_slide(
    slide: Any,
    page: DeckPagePlan,
    slide_width: float,
    slide_height: float,
    margin_x: float,
) -> None:
    """Draw an editable chapter divider slide."""
    accent = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(margin_x),
        Inches(1.25),
        Inches(0.18),
        Inches(4.8),
    )
    accent.fill.solid()
    accent.fill.fore_color.rgb = _COLOR_ACCENT
    accent.line.fill.background()
    _add_text_box(slide, page.title, margin_x + 0.45, 1.55, slide_width - margin_x * 2 - 0.45, 0.95, font_size=32, bold=True)
    _add_text_box(slide, page.key_takeaway, margin_x + 0.45, 2.78, slide_width - margin_x * 2 - 0.45, 0.82, font_size=18, color=_COLOR_MUTED)
    _draw_content_cards(slide, page.content_blocks[:2], margin_x + 0.45, 4.1, slide_width - margin_x * 2 - 0.45, 1.25)
    _draw_visual_band(slide, margin_x, slide_height - 1.12, slide_width - margin_x * 2, 0.38)


def _draw_content_slide(
    slide: Any,
    page: DeckPagePlan,
    slide_width: float,
    slide_height: float,
    margin_x: float,
) -> None:
    """Draw a standard editable content slide."""
    _add_text_box(slide, page.title, margin_x, 0.95, slide_width - margin_x * 2, 0.65, font_size=25, bold=True)
    _add_text_box(slide, page.key_takeaway, margin_x, 1.72, slide_width - margin_x * 2, 0.68, font_size=14, color=_COLOR_MUTED)
    _draw_content_cards(slide, page.content_blocks[:4], margin_x, 2.65, slide_width - margin_x * 2, 2.55)
    _draw_visual_band(slide, margin_x, slide_height - 1.14, slide_width - margin_x * 2, 0.42)
    _add_text_box(slide, page.asset_intent, margin_x + 0.18, slide_height - 1.05, slide_width - margin_x * 2 - 0.36, 0.25, font_size=9, color=_COLOR_WHITE)


def _draw_content_cards(
    slide: Any,
    blocks: list[dict[str, Any]],
    x: float,
    y: float,
    width: float,
    height: float,
) -> None:
    """Draw editable content cards for slide blocks."""
    card_blocks = blocks or [{"title": "Core message", "body": ""}]
    gap = 0.22
    rows = 2 if len(card_blocks) > 2 else 1
    cols = 2 if len(card_blocks) > 1 else 1
    card_width = (width - gap * (cols - 1)) / cols
    card_height = (height - gap * (rows - 1)) / rows
    for index, block in enumerate(card_blocks[:4]):
        col = index % cols
        row = index // cols
        card_x = x + col * (card_width + gap)
        card_y = y + row * (card_height + gap)
        card = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(card_x),
            Inches(card_y),
            Inches(card_width),
            Inches(card_height),
        )
        card.fill.solid()
        card.fill.fore_color.rgb = _COLOR_PANEL
        card.line.color.rgb = _COLOR_LINE
        _add_text_box(slide, _block_title(block), card_x + 0.18, card_y + 0.15, card_width - 0.36, 0.24, font_size=12, bold=True)
        _add_text_box(slide, _block_body(block), card_x + 0.18, card_y + 0.48, card_width - 0.36, card_height - 0.58, font_size=10, color=_COLOR_MUTED)


def _draw_visual_band(slide: Any, x: float, y: float, width: float, height: float) -> None:
    """Draw a simple editable accent band."""
    band = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(x),
        Inches(y),
        Inches(width),
        Inches(height),
    )
    band.fill.solid()
    band.fill.fore_color.rgb = _COLOR_ACCENT
    band.line.fill.background()
    cap = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(x + width * 0.72),
        Inches(y),
        Inches(width * 0.28),
        Inches(height),
    )
    cap.fill.solid()
    cap.fill.fore_color.rgb = _COLOR_ACCENT_2
    cap.line.fill.background()


def _draw_footer(
    slide: Any,
    page: DeckPagePlan,
    slide_width: float,
    slide_height: float,
    margin_x: float,
) -> None:
    """Draw a compact editable footer."""
    _add_line(slide, margin_x, slide_height - 0.58, slide_width - margin_x, slide_height - 0.58)
    _add_text_box(slide, page.chapter or page.page_type.replace("_", " "), margin_x, slide_height - 0.45, 4.0, 0.22, font_size=8, color=_COLOR_MUTED)
    _add_text_box(slide, f"{page.slide_number:02d}", slide_width - margin_x - 0.42, slide_height - 0.45, 0.42, 0.22, font_size=8, color=_COLOR_MUTED)


def _set_slide_background(slide: Any) -> None:
    """Apply the route background to one PPTX slide."""
    background = slide.background
    fill = background.fill
    fill.solid()
    fill.fore_color.rgb = _COLOR_BG


def _add_text_box(
    slide: Any,
    text: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    font_size: int,
    bold: bool = False,
    color: RGBColor = _COLOR_INK,
) -> Any:
    """Add an editable text box and return the shape."""
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(width), Inches(height))
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    frame.vertical_anchor = MSO_VERTICAL_ANCHOR.TOP
    frame.margin_left = Inches(0.02)
    frame.margin_right = Inches(0.02)
    frame.margin_top = Inches(0.01)
    frame.margin_bottom = Inches(0.01)
    paragraph = frame.paragraphs[0]
    paragraph.space_after = Pt(0)
    paragraph.line_spacing = 1.05
    run = paragraph.add_run()
    run.text = str(text or "")
    run.font.name = _FONT_FAMILY
    run.font.size = Pt(font_size)
    run.font.bold = bold
    run.font.color.rgb = color
    return shape


def _add_line(slide: Any, x1: float, y1: float, x2: float, y2: float) -> Any:
    """Add a thin editable divider line."""
    line = slide.shapes.add_connector(1, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    line.line.color.rgb = _COLOR_LINE
    line.line.width = Pt(0.75)
    return line


def _block_title(block: dict[str, Any], *, fallback: str = "Point") -> str:
    """Return the display title for one content block."""
    return str(block.get("title") or block.get("heading") or fallback).strip()


def _block_body(block: dict[str, Any]) -> str:
    """Return the display body for one content block."""
    return str(block.get("body") or block.get("text") or block.get("description") or "").strip()


def _validate_html_route_output(
    *,
    content_plan: DeckContentPlan,
    html_path: Path,
    preview_paths: list[Path],
    pptx_path: Path,
) -> dict[str, Any]:
    """Validate the core HTML route artifacts."""
    checks = {
        "html_exists": html_path.exists() and html_path.stat().st_size > 0,
        "preview_count_matches": len(preview_paths) == len(content_plan.pages),
        "all_previews_exist": all(path.exists() and path.stat().st_size > 0 for path in preview_paths),
        "pptx_exists": pptx_path.exists() and pptx_path.stat().st_size > 0,
        "pptx_slide_count_matches": False,
        "pptx_contains_editable_text": False,
        "pptx_titles_present": False,
    }
    editable_text_shape_count = 0
    pptx_text = ""
    if checks["pptx_exists"]:
        prs = Presentation(str(pptx_path))
        checks["pptx_slide_count_matches"] = len(prs.slides) == len(content_plan.pages)
        editable_text_shape_count = sum(
            1
            for slide in prs.slides
            for shape in slide.shapes
            if getattr(shape, "has_text_frame", False) and str(getattr(shape, "text", "") or "").strip()
        )
        pptx_text = "\n".join(
            str(getattr(shape, "text", "") or "")
            for slide in prs.slides
            for shape in slide.shapes
            if getattr(shape, "has_text_frame", False)
        )
        checks["pptx_contains_editable_text"] = editable_text_shape_count > 0
        checks["pptx_titles_present"] = all(page.title in pptx_text for page in content_plan.pages)
    status = "pass" if all(checks.values()) else "failed"
    return {
        "status": status,
        "checks": checks,
        "route": "html",
        "slide_count": len(content_plan.pages),
        "pptx_editable_text_shape_count": editable_text_shape_count,
    }


def _load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    """Load a font that can render Chinese and Latin text on common hosts."""
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for font_path in candidates:
        try:
            return ImageFont.truetype(font_path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(text: str, *, width: int) -> list[str]:
    """Wrap Latin text by words and CJK text by character count."""
    clean_text = " ".join(str(text or "").split())
    if not clean_text:
        return [""]
    if " " in clean_text:
        return textwrap.wrap(clean_text, width=width) or [clean_text]
    return [clean_text[index : index + width] for index in range(0, len(clean_text), width)]


def _truncate(text: str, max_chars: int) -> str:
    """Truncate one display string to a compact length."""
    clean_text = str(text or "").strip()
    if len(clean_text) <= max_chars:
        return clean_text
    return f"{clean_text[: max_chars - 1]}..."
