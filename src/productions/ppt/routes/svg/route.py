"""SVG route MVP for PPT generation."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import html
import json
import re
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.apps import App
from google.adk.artifacts import BaseArtifactService, InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai.types import Content, Part

from conf.llm import build_llm
from src.productions.ppt.routes.svg.native_converter import (
    CONVERTIBLE_VISUAL_TAGS,
    FORBIDDEN_SVG_ATTRS,
    FORBIDDEN_SVG_TAGS,
    SUPPORTED_SVG_TAGS,
    PptSvgNativeConversionError,
    export_svg_pages_to_native_pptx,
    validate_svg_content,
    validate_svg_file,
)
from src.productions.ppt.schemas import (
    ConfirmedRequirement,
    DeckContentPlan,
    DeckPagePlan,
    PptDesignConfirmation,
    PptDesignStrategy,
    PptSvgExecutionPlan,
    PptSvgPageResult,
    PptSvgQualityReport,
    PptSvgRouteBuildPackage,
)
from src.runtime.tool_context_artifact_service import ToolContextArtifactService
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path

PPT_DESIGN_STRATEGY_EXPERT_NAME = "PptDesignStrategyExpert"
PPT_SVG_DECK_EXECUTOR_EXPERT_NAME = "PptSvgDeckExecutorExpert"
PPT_DESIGN_CONFIRMATION_STATE_KEY = "ppt_design_confirmation"
PPT_DESIGN_STRATEGY_STATE_KEY = "ppt_design_strategy"
PPT_SVG_EXECUTION_PLAN_STATE_KEY = "ppt_svg_execution_plan"
PPT_SVG_ROUTE_CONTENT_PLAN_KEY = "ppt_svg_route_content_plan"
PPT_SVG_ROUTE_OUTPUT_DIR_KEY = "ppt_svg_route_output_dir"
PPT_SVG_ROUTE_GENERATED_PAGES_KEY = "ppt_svg_route_generated_pages"
PPT_SVG_DESIGN_AGENT_MESSAGE_KEY = "ppt_design_strategy_agent_message"
PPT_SVG_EXECUTOR_AGENT_MESSAGE_KEY = "ppt_svg_deck_executor_agent_message"
PPT_SVG_ROUTE_WARNINGS_KEY = "ppt_svg_route_warnings"
SVG_ROUTE_STAGE_SEQUENCE = (
    "design_strategy",
    "svg_page_generation",
    "svg_quality_check",
    "pptx_output",
)
SVG_DELIVERY_STAGE = "quality_delivery"
_SVG_NS = "http://www.w3.org/2000/svg"
_SUPPORTED_ASPECT_RATIOS = {"16:9", "4:3"}
_UNSUPPORTED_SVG_TAGS = set(FORBIDDEN_SVG_TAGS)


@dataclass(frozen=True)
class SvgRoutePaths:
    """Filesystem paths used by one SVG route build."""

    output_dir: Path
    svg_dir: Path
    pptx_path: Path
    quality_report_path: Path
    build_log_path: Path


@dataclass(frozen=True)
class SvgDesignStrategyResult:
    """Resolved design strategy for an SVG route run."""

    confirmation: PptDesignConfirmation
    strategy: PptDesignStrategy
    execution_plan: PptSvgExecutionPlan
    generation_mode: str = "deterministic_strategy"
    warnings: list[str] = field(default_factory=list)
    stage: str = "design_strategy"


@dataclass(frozen=True)
class SvgPageGenerationResult:
    """Generated SVG pages for an SVG route run."""

    svg_pages: list[PptSvgPageResult]
    generation_mode: str = "deterministic_svg_renderer"
    warnings: list[str] = field(default_factory=list)
    stage: str = "svg_page_generation"


@dataclass(frozen=True)
class SvgPptxOutputResult:
    """Result of exporting SVG pages into editable PPTX."""

    pptx_path: Path
    conversion_report: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    stage: str = "pptx_output"


@dataclass(frozen=True)
class SvgQualityDeliveryResult:
    """Result of SVG route quality reporting."""

    quality_report_path: Path
    build_log_path: Path
    quality_report: PptSvgQualityReport
    warnings: list[str]
    stage: str = SVG_DELIVERY_STAGE


def build_svg_route(
    *,
    content_plan: DeckContentPlan,
    output_dir: Path,
    aspect_ratio: str = "16:9",
    template_id: str = "",
) -> PptSvgRouteBuildPackage:
    """Generate SVG pages and an editable PPTX with deterministic defaults."""
    requirement = ConfirmedRequirement(
        route="svg",
        request_brief=content_plan.core_narrative,
        topic=content_plan.title,
        aspect_ratio=_normalize_aspect_ratio(aspect_ratio),
        template_requirement={"template_id": template_id, "template_source": "system" if template_id else "none"},
    )
    paths = prepare_svg_route_paths(output_dir)
    design_stage = build_default_svg_design_strategy(
        requirement=requirement,
        content_plan=content_plan,
    )
    page_stage = generate_svg_pages(
        content_plan=content_plan,
        design_stage=design_stage,
        paths=paths,
    )
    quality_report = check_svg_pages_quality(
        svg_page_paths=[page.svg_path for page in page_stage.svg_pages],
        expected_page_count=len(content_plan.pages),
        execution_plan=design_stage.execution_plan,
    )
    pptx_stage = export_svg_pages_to_pptx(
        svg_page_paths=[page.svg_path for page in page_stage.svg_pages],
        pptx_path=paths.pptx_path,
        execution_plan=design_stage.execution_plan,
    )
    quality_stage = deliver_svg_route_quality(
        content_plan=content_plan,
        design_stage=design_stage,
        page_generation=page_stage,
        quality_report=quality_report,
        pptx_output=pptx_stage,
        paths=paths,
    )
    return _build_svg_route_package(
        design_stage=design_stage,
        page_stage=page_stage,
        pptx_stage=pptx_stage,
        quality_stage=quality_stage,
    )


async def build_svg_route_with_agent(
    *,
    requirement: ConfirmedRequirement,
    content_plan: DeckContentPlan,
    output_dir: Path,
    tool_context: ToolContext | None = None,
    app_name: str = "creative_claw",
    artifact_service: BaseArtifactService | None = None,
    design_strategy_agent: BaseAgent | None = None,
    svg_executor_agent: BaseAgent | None = None,
) -> PptSvgRouteBuildPackage:
    """Generate SVG route artifacts, using PM-managed experts when available."""
    paths = prepare_svg_route_paths(output_dir)
    design_stage = await build_svg_design_strategy_with_agent(
        requirement=requirement,
        content_plan=content_plan,
        paths=paths,
        tool_context=tool_context,
        app_name=app_name,
        artifact_service=artifact_service,
        design_strategy_agent=design_strategy_agent,
    )
    page_stage = await generate_svg_pages_with_agent(
        requirement=requirement,
        content_plan=content_plan,
        design_stage=design_stage,
        paths=paths,
        tool_context=tool_context,
        app_name=app_name,
        artifact_service=artifact_service,
        svg_executor_agent=svg_executor_agent,
    )
    quality_report = check_svg_pages_quality(
        svg_page_paths=[page.svg_path for page in page_stage.svg_pages],
        expected_page_count=len(content_plan.pages),
        execution_plan=design_stage.execution_plan,
    )
    pptx_stage = export_svg_pages_to_pptx(
        svg_page_paths=[page.svg_path for page in page_stage.svg_pages],
        pptx_path=paths.pptx_path,
        execution_plan=design_stage.execution_plan,
    )
    quality_stage = deliver_svg_route_quality(
        content_plan=content_plan,
        design_stage=design_stage,
        page_generation=page_stage,
        quality_report=quality_report,
        pptx_output=pptx_stage,
        paths=paths,
    )
    return _build_svg_route_package(
        design_stage=design_stage,
        page_stage=page_stage,
        pptx_stage=pptx_stage,
        quality_stage=quality_stage,
    )


def prepare_svg_route_paths(output_dir: Path) -> SvgRoutePaths:
    """Prepare output paths for all SVG route stages."""
    output_dir.mkdir(parents=True, exist_ok=True)
    svg_dir = output_dir / "svg_pages"
    svg_dir.mkdir(parents=True, exist_ok=True)
    return SvgRoutePaths(
        output_dir=output_dir,
        svg_dir=svg_dir,
        pptx_path=output_dir / "deck.pptx",
        quality_report_path=output_dir / "quality_report.json",
        build_log_path=output_dir / "build_log.json",
    )


def build_default_svg_design_strategy(
    *,
    requirement: ConfirmedRequirement,
    content_plan: DeckContentPlan,
) -> SvgDesignStrategyResult:
    """Build a deterministic SVG design strategy from current PPT facts."""
    aspect_ratio = _normalize_aspect_ratio(requirement.aspect_ratio)
    canvas_width, canvas_height = _canvas_size(aspect_ratio)
    style_keywords = set(requirement.style_requirement.style_keywords)
    if {"playful", "kid_friendly", "illustrated"} & style_keywords:
        palette = ["#FFF7E8", "#182230", "#F26D3D", "#2EA7A0", "#F4C542"]
        style_name = "warm_playful_cards"
        direction = "Warm, illustrated flashcard-like slides with large readable text."
        icon_style = "rounded_line"
    elif "academic" in style_keywords:
        palette = ["#F6F7F9", "#172033", "#244C8F", "#8E6F3E", "#D7DCE5"]
        style_name = "academic_clarity"
        direction = "Quiet academic slides with strong hierarchy and restrained accents."
        icon_style = "thin_line"
    else:
        palette = ["#F7F8FB", "#172033", "#2457D6", "#43A6FF", "#DCE7FF"]
        style_name = "clean_editorial"
        direction = "Clean editorial slides with a strong title lane and editable vector structure."
        icon_style = "line"

    strategy = PptDesignStrategy(
        style_name=style_name,
        design_direction=direction,
        palette=palette,
        font_family="Aptos",
        title_font_family="Aptos Display",
        icon_style=icon_style,
        image_strategy="Use ready content-plan assets when available; otherwise render simple editable placeholders.",
        layout_principles=[
            "Use one clear message per slide.",
            "Keep title, takeaway, and support points as editable text.",
            "Use only simple SVG shapes that can be converted into PowerPoint objects.",
        ],
    )
    confirmation = PptDesignConfirmation(
        summary=f"Use `{style_name}` for {content_plan.title}.",
        decisions=[
            f"Aspect ratio: {aspect_ratio}",
            f"Palette: {', '.join(palette[:4])}",
            "Export target: editable PPTX from SVG pages.",
        ],
        requires_user_confirmation=False,
        confirmation_prompt="Auto-confirmed for the SVG route MVP.",
    )
    execution_plan = PptSvgExecutionPlan(
        aspect_ratio=aspect_ratio,
        canvas_format="ppt43" if aspect_ratio == "4:3" else "ppt169",
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        background_color=palette[0],
        primary_text_color=palette[1],
        muted_text_color="#667085",
        accent_color=palette[2],
        secondary_accent_color=palette[3],
        font_family=strategy.font_family,
        font_stack=[strategy.font_family, "Microsoft YaHei", "Arial"],
        latin_font=strategy.font_family,
        east_asian_font="Microsoft YaHei",
        safe_margin=72 if aspect_ratio == "16:9" else 64,
        page_layouts={
            "cover": "large_title_with_accent_panel",
            "toc": "numbered_agenda",
            "chapter_start": "section_anchor",
            "chapter_content": "title_takeaway_blocks",
            "content": "title_takeaway_blocks",
            "ending": "closing_summary",
        },
        quality_constraints=[
            "Use only the native DrawingML converter SVG subset.",
            "No style/class/foreignObject/mask/script/symbol/use/textPath/animate features.",
            "Use viewBox matching canvas size.",
            "Keep visible text in SVG text elements.",
            "Prefer rect, line, circle, ellipse, polygon, polyline, path, text/tspan, and local or data images.",
            "Use HEX colors, opacity attributes, and optional defs-based linear/radial gradients.",
            "Use markers only on line/path, clipPath only on image, and drop-shadow/glow filters only through defs.",
        ],
        supported_svg_tags=sorted(SUPPORTED_SVG_TAGS),
        convertible_svg_tags=sorted(CONVERTIBLE_VISUAL_TAGS),
        forbidden_svg_tags=sorted(FORBIDDEN_SVG_TAGS),
        forbidden_svg_attributes=sorted(FORBIDDEN_SVG_ATTRS),
        converter_profile="native_drawingml_ppt_master_baseline_v1",
        pptx_editability_level="high",
    )
    return SvgDesignStrategyResult(
        confirmation=confirmation,
        strategy=strategy,
        execution_plan=execution_plan,
    )


async def build_svg_design_strategy_with_agent(
    *,
    requirement: ConfirmedRequirement,
    content_plan: DeckContentPlan,
    paths: SvgRoutePaths,
    tool_context: ToolContext | None,
    app_name: str,
    artifact_service: BaseArtifactService | None,
    design_strategy_agent: BaseAgent | None,
) -> SvgDesignStrategyResult:
    """Resolve design strategy through an ADK expert, falling back deterministically."""
    fallback = build_default_svg_design_strategy(requirement=requirement, content_plan=content_plan)
    if tool_context is None or not hasattr(tool_context, "_invocation_context") or design_strategy_agent is None:
        _persist_svg_design_to_state(tool_context, fallback)
        return fallback

    try:
        result = await _run_svg_design_strategy_agent(
            requirement=requirement,
            content_plan=content_plan,
            fallback=fallback,
            paths=paths,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            design_strategy_agent=design_strategy_agent,
        )
        _persist_svg_design_to_state(tool_context, result)
        return result
    except Exception as exc:
        agent_name = getattr(design_strategy_agent, "name", PPT_DESIGN_STRATEGY_EXPERT_NAME)
        warning = f"{agent_name} fallback: {type(exc).__name__}: {exc}"
        _append_svg_route_warning(tool_context.state, warning)
        fallback_with_warning = SvgDesignStrategyResult(
            confirmation=fallback.confirmation,
            strategy=fallback.strategy,
            execution_plan=fallback.execution_plan,
            generation_mode=fallback.generation_mode,
            warnings=[warning, *fallback.warnings],
        )
        _persist_svg_design_to_state(tool_context, fallback_with_warning)
        return fallback_with_warning


def generate_svg_pages(
    *,
    content_plan: DeckContentPlan,
    design_stage: SvgDesignStrategyResult,
    paths: SvgRoutePaths,
) -> SvgPageGenerationResult:
    """Generate deterministic SVG files for every page."""
    svg_pages: list[PptSvgPageResult] = []
    for page in content_plan.pages:
        svg_content = render_svg_slide(
            page=page,
            content_plan=content_plan,
            design_stage=design_stage,
        )
        svg_path = paths.svg_dir / f"slide_{page.slide_number:03d}.svg"
        svg_path.write_text(svg_content, encoding="utf-8")
        svg_pages.append(
            PptSvgPageResult(
                slide_number=page.slide_number,
                title=page.title,
                svg_path=workspace_relative_path(svg_path),
                page_type=page.page_type,
                page_rhythm=_page_rhythm(page),
            )
        )
    return SvgPageGenerationResult(svg_pages=svg_pages)


async def generate_svg_pages_with_agent(
    *,
    requirement: ConfirmedRequirement,
    content_plan: DeckContentPlan,
    design_stage: SvgDesignStrategyResult,
    paths: SvgRoutePaths,
    tool_context: ToolContext | None,
    app_name: str,
    artifact_service: BaseArtifactService | None,
    svg_executor_agent: BaseAgent | None,
) -> SvgPageGenerationResult:
    """Generate SVG pages through an ADK expert, falling back deterministically."""
    if tool_context is None or not hasattr(tool_context, "_invocation_context") or svg_executor_agent is None:
        return generate_svg_pages(content_plan=content_plan, design_stage=design_stage, paths=paths)

    try:
        return await _run_svg_deck_executor_agent(
            requirement=requirement,
            content_plan=content_plan,
            design_stage=design_stage,
            paths=paths,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            svg_executor_agent=svg_executor_agent,
        )
    except Exception as exc:
        agent_name = getattr(svg_executor_agent, "name", PPT_SVG_DECK_EXECUTOR_EXPERT_NAME)
        warning = f"{agent_name} fallback: {type(exc).__name__}: {exc}"
        _append_svg_route_warning(tool_context.state, warning)
        fallback = generate_svg_pages(content_plan=content_plan, design_stage=design_stage, paths=paths)
        return SvgPageGenerationResult(
            svg_pages=fallback.svg_pages,
            generation_mode=fallback.generation_mode,
            warnings=[warning, *fallback.warnings],
        )


def render_svg_slide(
    *,
    page: DeckPagePlan,
    content_plan: DeckContentPlan,
    design_stage: SvgDesignStrategyResult,
) -> str:
    """Render one supported-subset SVG slide from a deck page plan."""
    plan = design_stage.execution_plan
    width = plan.canvas_width
    height = plan.canvas_height
    margin = plan.safe_margin
    page_type = html.escape(page.page_type)
    bullet_items = _extract_page_bullet_texts(page)
    body_lines = bullet_items[:4] or [page.purpose or page.key_takeaway]
    section_label = html.escape(page.chapter or content_plan.title)
    visual_label = html.escape(_first_visual_label(page))
    rhythm = _page_rhythm(page)

    title_size = 58 if page.page_type in {"cover", "chapter_start"} else 42
    title_y = 128 if page.page_type in {"cover", "chapter_start"} else 92
    body_start_y = 302 if page.page_type in {"cover", "chapter_start"} else 244
    accent_x = width - 330
    accent_y = 98
    accent_w = 210
    accent_h = 168
    if page.page_type in {"cover", "chapter_start"}:
        accent_x = width - 380
        accent_y = 110
        accent_w = 260
        accent_h = 260

    body_svg = []
    for index, item in enumerate(body_lines, start=1):
        y = body_start_y + (index - 1) * 58
        body_svg.append(
            "\n".join(
                [
                    f'<circle cx="{margin + 14}" cy="{y - 7}" r="10" fill="{plan.secondary_accent_color}" opacity="0.85" />',
                    _svg_text(
                        text=item,
                        x=margin + 38,
                        y=y,
                        max_chars=58,
                        font_size=25,
                        fill=plan.primary_text_color,
                        font_family=plan.font_family,
                    ),
                ]
            )
        )

    return f"""<svg xmlns="{_SVG_NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}" data-route="ppt-svg" data-page-type="{page_type}" data-page-rhythm="{rhythm}">
  <rect x="0" y="0" width="{width}" height="{height}" fill="{plan.background_color}" />
  <rect x="{margin - 28}" y="{height - 112}" width="{width - (margin * 2) + 56}" height="2" fill="{plan.accent_color}" opacity="0.28" />
  <rect x="{accent_x}" y="{accent_y}" width="{accent_w}" height="{accent_h}" rx="22" fill="{plan.accent_color}" opacity="0.12" />
  <rect x="{accent_x + 26}" y="{accent_y + 30}" width="{accent_w - 52}" height="{max(48, accent_h - 60)}" rx="18" fill="#FFFFFF" opacity="0.72" />
  <text x="{margin}" y="58" font-family="{plan.font_family}" font-size="18" fill="{plan.accent_color}" font-weight="700">{section_label}</text>
  {_svg_text(text=page.title, x=margin, y=title_y, max_chars=28, font_size=title_size, fill=plan.primary_text_color, font_family=plan.font_family, font_weight="800", data_width=780)}
  {_svg_text(text=page.key_takeaway, x=margin, y=title_y + 106, max_chars=44, font_size=27, fill=plan.muted_text_color, font_family=plan.font_family, data_width=820)}
  {"".join(body_svg)}
  <text x="{accent_x + 52}" y="{accent_y + 82}" font-family="{plan.font_family}" font-size="20" fill="{plan.accent_color}" font-weight="700">Visual</text>
  {_svg_text(text=visual_label, x=accent_x + 52, y=accent_y + 126, max_chars=20, font_size=22, fill=plan.primary_text_color, font_family=plan.font_family, data_width=accent_w - 82)}
  <text x="{width - margin}" y="{height - 48}" text-anchor="end" font-family="{plan.font_family}" font-size="17" fill="{plan.muted_text_color}">{page.slide_number:02d}</text>
</svg>
"""


def check_svg_pages_quality(
    *,
    svg_page_paths: list[str],
    expected_page_count: int,
    execution_plan: PptSvgExecutionPlan,
) -> PptSvgQualityReport:
    """Check generated SVG pages against the route's PPTX-safe subset."""
    issues: list[dict[str, Any]] = []
    checks = {
        "page_count_matches": len(svg_page_paths) == expected_page_count,
        "all_svg_files_exist": True,
        "all_svg_xml_valid": True,
        "all_viewboxes_match_canvas": True,
        "no_unsupported_tags": True,
        "no_forbidden_features": True,
        "all_visual_elements_convertible": True,
        "all_image_refs_resolvable": True,
        "visible_text_present": True,
    }
    if len(svg_page_paths) != expected_page_count:
        issues.append(
            {
                "severity": "error",
                "code": "page_count_mismatch",
                "message": f"Expected {expected_page_count} SVG pages, got {len(svg_page_paths)}.",
            }
        )

    expected_viewbox = f"0 0 {execution_plan.canvas_width} {execution_plan.canvas_height}"
    for path in svg_page_paths:
        issue_context = {"path": path}
        try:
            svg_path = resolve_workspace_path(path)
        except Exception:
            svg_path = Path(path)
        if not svg_path.exists() or not svg_path.is_file():
            checks["all_svg_files_exist"] = False
            issues.append({**issue_context, "severity": "error", "code": "missing_svg_file", "message": "SVG file does not exist."})
            continue
        try:
            root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
        except Exception as exc:
            checks["all_svg_xml_valid"] = False
            issues.append({**issue_context, "severity": "error", "code": "invalid_svg_xml", "message": str(exc)})
            continue
        if _local_name(root.tag) != "svg":
            checks["all_svg_xml_valid"] = False
            issues.append({**issue_context, "severity": "error", "code": "missing_svg_root", "message": "Root tag is not svg."})
        if str(root.attrib.get("viewBox") or "").strip() != expected_viewbox:
            checks["all_viewboxes_match_canvas"] = False
            issues.append(
                {
                    **issue_context,
                    "severity": "error",
                    "code": "viewbox_mismatch",
                    "message": f"Expected viewBox `{expected_viewbox}`.",
                }
            )
        unsupported_tags = sorted(
            {
                _local_name(node.tag)
                for node in root.iter()
                if _local_name(node.tag) in _UNSUPPORTED_SVG_TAGS
            }
        )
        if unsupported_tags:
            checks["no_unsupported_tags"] = False
            checks["no_forbidden_features"] = False
            issues.append(
                {
                    **issue_context,
                    "severity": "error",
                    "code": "unsupported_svg_tags",
                    "message": f"Unsupported SVG tags: {', '.join(unsupported_tags)}.",
                }
            )
        native_issues = validate_svg_file(svg_path, execution_plan=execution_plan)
        for native_issue in native_issues:
            code = str(native_issue.get("code") or "")
            if code == "invalid_svg_xml":
                checks["all_svg_xml_valid"] = False
            if code == "viewbox_mismatch":
                checks["all_viewboxes_match_canvas"] = False
            if code in {"forbidden_svg_feature", "forbidden_svg_attribute", "unsupported_svg_attribute"}:
                checks["no_forbidden_features"] = False
            if code in {
                "unsupported_svg_tag",
                "unsupported_svg_path_command",
                "malformed_svg_path",
                "unsupported_transform",
                "unsupported_paint_server",
                "unsupported_color",
            }:
                checks["all_visual_elements_convertible"] = False
                checks["no_unsupported_tags"] = False
            if code in {"missing_image_href", "remote_image_href", "unsupported_image_data_uri", "unsupported_image_format", "missing_image_file"}:
                checks["all_image_refs_resolvable"] = False
            if not any(
                existing.get("code") == native_issue.get("code")
                and existing.get("message") == native_issue.get("message")
                and existing.get("path") == native_issue.get("path")
                for existing in issues
            ):
                issues.append(native_issue)
        if not any(_local_name(node.tag) == "text" and "".join(node.itertext()).strip() for node in root.iter()):
            checks["visible_text_present"] = False
            issues.append({**issue_context, "severity": "warning", "code": "missing_visible_text", "message": "SVG page has no visible text elements."})

    has_errors = any(issue.get("severity") == "error" for issue in issues)
    has_warnings = any(issue.get("severity") == "warning" for issue in issues)
    status = "failed" if has_errors else "warning" if has_warnings else "pass"
    return PptSvgQualityReport(
        status=status,
        page_count=len(svg_page_paths),
        svg_page_paths=svg_page_paths,
        checks=checks,
        issues=issues,
        warnings=[issue["message"] for issue in issues if issue.get("severity") == "warning"],
    )


def export_svg_pages_to_pptx(
    *,
    svg_page_paths: list[str],
    pptx_path: Path,
    execution_plan: PptSvgExecutionPlan,
) -> SvgPptxOutputResult:
    """Export route-generated SVG pages to editable native DrawingML PPTX."""
    try:
        export_result = export_svg_pages_to_native_pptx(
            svg_page_paths=svg_page_paths,
            pptx_path=pptx_path,
            execution_plan=execution_plan,
        )
    except PptSvgNativeConversionError as exc:
        failed_output_path = pptx_path.parent / f".{pptx_path.name}.failed"
        conversion_report = {
            "engine": "native_drawingml_svg_converter",
            "requested_strategy": "svg_to_native_drawingml_pptx",
            "final_strategy": "failed",
            "ok": False,
            "requested_output_path": str(pptx_path),
            "fallback_used": False,
            "editable_level": "none",
            "warnings": [],
            "errors": [str(exc)],
            "pages": [],
        }
        return SvgPptxOutputResult(
            pptx_path=failed_output_path,
            conversion_report=conversion_report,
            warnings=[],
        )
    return SvgPptxOutputResult(
        pptx_path=export_result.pptx_path,
        conversion_report=export_result.conversion_report,
        warnings=export_result.warnings,
    )


def deliver_svg_route_quality(
    *,
    content_plan: DeckContentPlan,
    design_stage: SvgDesignStrategyResult,
    page_generation: SvgPageGenerationResult,
    quality_report: PptSvgQualityReport,
    pptx_output: SvgPptxOutputResult,
    paths: SvgRoutePaths,
) -> SvgQualityDeliveryResult:
    """Write SVG route quality report and build log."""
    quality_payload = quality_report.model_dump(mode="json")
    quality_payload["route_stages"] = list(SVG_ROUTE_STAGE_SEQUENCE)
    quality_payload["delivery_stage"] = SVG_DELIVERY_STAGE
    quality_payload["pptx_conversion"] = pptx_output.conversion_report
    paths.quality_report_path.write_text(
        json.dumps(quality_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    build_log = {
        "route": "svg",
        "workflow_name": "SvgRouteSequentialAgent",
        "route_stages": list(SVG_ROUTE_STAGE_SEQUENCE),
        "delivery_stage": SVG_DELIVERY_STAGE,
        "design_generation_mode": design_stage.generation_mode,
        "svg_generation_mode": page_generation.generation_mode,
        "style_name": design_stage.strategy.style_name,
        "slide_count": len(content_plan.pages),
        "svg_page_paths": [page.svg_path for page in page_generation.svg_pages],
        "pptx_path": workspace_relative_path(pptx_output.pptx_path) if pptx_output.pptx_path.exists() else "",
        "quality_status": quality_report.status,
        "pptx_conversion": pptx_output.conversion_report,
    }
    paths.build_log_path.write_text(json.dumps(build_log, ensure_ascii=False, indent=2), encoding="utf-8")
    warnings = [
        *list(design_stage.warnings),
        *list(page_generation.warnings),
        *list(quality_report.warnings),
        *list(pptx_output.warnings),
    ]
    return SvgQualityDeliveryResult(
        quality_report_path=paths.quality_report_path,
        build_log_path=paths.build_log_path,
        quality_report=quality_report,
        warnings=warnings,
    )


def build_ppt_design_strategy_expert(
    *,
    save_design_strategy_tool: Any,
    save_svg_execution_plan_tool: Any,
) -> LlmAgent:
    """Build the PPT product expert that prepares generic design strategy."""
    return LlmAgent(
        name=PPT_DESIGN_STRATEGY_EXPERT_NAME,
        model=build_llm(),
        description="Prepares route-independent PPT design strategy and SVG execution constraints.",
        instruction=(
            "You are Creative Claw's PPT design strategy expert.\n"
            "Use ConfirmedRequirement, SourceUnderstanding, and DeckContentPlan to create a generic design strategy "
            "and a strict SVG execution plan for a deck.\n"
            "Do not generate SVG pages or PPTX files.\n"
            "Keep decisions practical: canvas, palette, font stack, page rhythm, icon/image strategy, "
            "supported SVG tags, forbidden SVG features, and PPTX editability constraints.\n"
            "The SVG execution plan is an authoring contract for a native DrawingML converter, similar in spirit "
            "to a spec lock: generated SVG must use only the converter subset and must avoid CSS, class/style, "
            "foreignObject, mask, script, symbol/use, textPath, animation, remote images, and rgba(). "
            "Only use paint servers for defs-based linearGradient/radialGradient.\n"
            "Call save_ppt_design_strategy with strategy_json and confirmation_json.\n"
            "Call save_ppt_svg_execution_plan with execution_plan_json."
        ),
        tools=[save_design_strategy_tool, save_svg_execution_plan_tool],
        output_key=PPT_SVG_DESIGN_AGENT_MESSAGE_KEY,
        include_contents="none",
    )


def build_ppt_svg_deck_executor_expert(
    *,
    read_svg_execution_plan_tool: Any,
    save_svg_page_tool: Any,
) -> LlmAgent:
    """Build the PPT product expert that generates SVG pages."""
    return LlmAgent(
        name=PPT_SVG_DECK_EXECUTOR_EXPERT_NAME,
        model=build_llm(),
        description="Generates PPT-route SVG pages from content plan and design strategy.",
        instruction=(
            "You are Creative Claw's PPT SVG deck executor.\n"
            "Generate one complete SVG file per slide from DeckContentPlan and the saved SVG execution plan.\n"
            "Before generating each slide, call read_ppt_svg_execution_plan and follow the latest plan exactly.\n"
            "Then call save_ppt_svg_page exactly once for that planned slide, in slide order.\n"
            "Use only the native DrawingML converter subset listed in the execution plan, including simple defs-based "
            "gradients, markers, clipPath on images, and drop-shadow/glow filters when needed. Use local/data images only.\n"
            "Do not use style/class, foreignObject, mask, script, symbol/use, textPath, SVG animation, remote images, "
            "CSS, rgba(), markdown, or code fences.\n"
            "Use HEX colors plus opacity attributes. Every SVG must include width, height, and viewBox matching the execution plan."
        ),
        tools=[read_svg_execution_plan_tool, save_svg_page_tool],
        output_key=PPT_SVG_EXECUTOR_AGENT_MESSAGE_KEY,
        include_contents="none",
    )


async def _run_svg_design_strategy_agent(
    *,
    requirement: ConfirmedRequirement,
    content_plan: DeckContentPlan,
    fallback: SvgDesignStrategyResult,
    paths: SvgRoutePaths,
    tool_context: ToolContext,
    app_name: str,
    artifact_service: BaseArtifactService | None,
    design_strategy_agent: BaseAgent,
) -> SvgDesignStrategyResult:
    """Run the design-strategy child expert and return saved strategy state."""
    invocation_context = tool_context._invocation_context
    child_session_service = InMemorySessionService()
    child_artifact_service = _resolve_child_artifact_service(
        tool_context=tool_context,
        fallback_service=artifact_service or InMemoryArtifactService(),
    )
    child_runner = _build_child_runner(
        agent=design_strategy_agent,
        app_name=app_name,
        session_service=child_session_service,
        artifact_service=child_artifact_service,
        invocation_context=invocation_context,
    )
    child_state = _copy_state(tool_context.state)
    child_state.update(
        {
            "ppt_confirmed_requirement": requirement.model_dump(mode="json"),
            "ppt_deck_content_plan": content_plan.model_dump(mode="json"),
            PPT_DESIGN_CONFIRMATION_STATE_KEY: fallback.confirmation.model_dump(mode="json"),
            PPT_DESIGN_STRATEGY_STATE_KEY: fallback.strategy.model_dump(mode="json"),
            PPT_SVG_EXECUTION_PLAN_STATE_KEY: fallback.execution_plan.model_dump(mode="json"),
            PPT_SVG_ROUTE_OUTPUT_DIR_KEY: str(paths.output_dir),
        }
    )
    try:
        child_session = await child_session_service.create_session(
            app_name=app_name,
            user_id=invocation_context.user_id,
            state=child_state,
        )
        async for _event in child_runner.run_async(
            user_id=child_session.user_id,
            session_id=child_session.id,
            new_message=Content(
                role="user",
                parts=[
                    Part(
                        text=_build_svg_design_strategy_user_message(
                            requirement=requirement,
                            content_plan=content_plan,
                            fallback=fallback,
                        )
                    )
                ],
            ),
        ):
            pass
        final_session = await child_session_service.get_session(
            app_name=app_name,
            user_id=child_session.user_id,
            session_id=child_session.id,
        )
        final_state = final_session.state if final_session is not None else child_state
        confirmation = PptDesignConfirmation.model_validate(
            final_state.get(PPT_DESIGN_CONFIRMATION_STATE_KEY) or fallback.confirmation
        )
        strategy = PptDesignStrategy.model_validate(
            final_state.get(PPT_DESIGN_STRATEGY_STATE_KEY) or fallback.strategy
        )
        execution_plan = PptSvgExecutionPlan.model_validate(
            final_state.get(PPT_SVG_EXECUTION_PLAN_STATE_KEY) or fallback.execution_plan
        )
        for key in (
            PPT_DESIGN_CONFIRMATION_STATE_KEY,
            PPT_DESIGN_STRATEGY_STATE_KEY,
            PPT_SVG_EXECUTION_PLAN_STATE_KEY,
            PPT_SVG_DESIGN_AGENT_MESSAGE_KEY,
        ):
            if key in final_state:
                tool_context.state[key] = copy.deepcopy(final_state[key])
        return SvgDesignStrategyResult(
            confirmation=confirmation,
            strategy=strategy,
            execution_plan=execution_plan,
            generation_mode="llm_agent_design_strategy",
        )
    finally:
        await child_runner.close()


async def _run_svg_deck_executor_agent(
    *,
    requirement: ConfirmedRequirement,
    content_plan: DeckContentPlan,
    design_stage: SvgDesignStrategyResult,
    paths: SvgRoutePaths,
    tool_context: ToolContext,
    app_name: str,
    artifact_service: BaseArtifactService | None,
    svg_executor_agent: BaseAgent,
) -> SvgPageGenerationResult:
    """Run the SVG deck executor child expert and return saved page artifacts."""
    invocation_context = tool_context._invocation_context
    child_session_service = InMemorySessionService()
    child_artifact_service = _resolve_child_artifact_service(
        tool_context=tool_context,
        fallback_service=artifact_service or InMemoryArtifactService(),
    )
    child_runner = _build_child_runner(
        agent=svg_executor_agent,
        app_name=app_name,
        session_service=child_session_service,
        artifact_service=child_artifact_service,
        invocation_context=invocation_context,
    )
    child_state = _copy_state(tool_context.state)
    child_state.update(
        {
            "ppt_confirmed_requirement": requirement.model_dump(mode="json"),
            "ppt_deck_content_plan": content_plan.model_dump(mode="json"),
            PPT_SVG_ROUTE_CONTENT_PLAN_KEY: content_plan.model_dump(mode="json"),
            PPT_DESIGN_CONFIRMATION_STATE_KEY: design_stage.confirmation.model_dump(mode="json"),
            PPT_DESIGN_STRATEGY_STATE_KEY: design_stage.strategy.model_dump(mode="json"),
            PPT_SVG_EXECUTION_PLAN_STATE_KEY: design_stage.execution_plan.model_dump(mode="json"),
            PPT_SVG_ROUTE_OUTPUT_DIR_KEY: str(paths.output_dir),
            PPT_SVG_ROUTE_GENERATED_PAGES_KEY: [],
        }
    )
    try:
        child_session = await child_session_service.create_session(
            app_name=app_name,
            user_id=invocation_context.user_id,
            state=child_state,
        )
        async for _event in child_runner.run_async(
            user_id=child_session.user_id,
            session_id=child_session.id,
            new_message=Content(
                role="user",
                parts=[
                    Part(
                        text=_build_svg_executor_user_message(
                            requirement=requirement,
                            content_plan=content_plan,
                            design_stage=design_stage,
                        )
                    )
                ],
            ),
        ):
            pass
        final_session = await child_session_service.get_session(
            app_name=app_name,
            user_id=child_session.user_id,
            session_id=child_session.id,
        )
        final_state = final_session.state if final_session is not None else child_state
        pages = _normalize_svg_page_results(
            final_state.get(PPT_SVG_ROUTE_GENERATED_PAGES_KEY) or [],
            content_plan=content_plan,
        )
        if not pages:
            raise ValueError(f"{getattr(svg_executor_agent, 'name', PPT_SVG_DECK_EXECUTOR_EXPERT_NAME)} did not save SVG pages.")
        tool_context.state[PPT_SVG_ROUTE_GENERATED_PAGES_KEY] = [
            page.model_dump(mode="json") for page in pages
        ]
        if final_state.get(PPT_SVG_EXECUTOR_AGENT_MESSAGE_KEY):
            tool_context.state[PPT_SVG_EXECUTOR_AGENT_MESSAGE_KEY] = str(
                final_state.get(PPT_SVG_EXECUTOR_AGENT_MESSAGE_KEY)
            )
        return SvgPageGenerationResult(
            svg_pages=pages,
            generation_mode="llm_agent_svg",
        )
    finally:
        await child_runner.close()


def _build_svg_route_package(
    *,
    design_stage: SvgDesignStrategyResult,
    page_stage: SvgPageGenerationResult,
    pptx_stage: SvgPptxOutputResult,
    quality_stage: SvgQualityDeliveryResult,
) -> PptSvgRouteBuildPackage:
    """Build the public SVG route package from stage results."""
    return PptSvgRouteBuildPackage(
        design_confirmation=design_stage.confirmation,
        design_strategy=design_stage.strategy,
        svg_execution_plan=design_stage.execution_plan,
        svg_page_paths=[page.svg_path for page in page_stage.svg_pages],
        preview_paths=[],
        pptx_path=workspace_relative_path(pptx_stage.pptx_path) if pptx_stage.pptx_path.exists() else "",
        quality_report_path=workspace_relative_path(quality_stage.quality_report_path),
        build_log_path=workspace_relative_path(quality_stage.build_log_path),
        warnings=quality_stage.warnings,
    )


def _build_svg_design_strategy_user_message(
    *,
    requirement: ConfirmedRequirement,
    content_plan: DeckContentPlan,
    fallback: SvgDesignStrategyResult,
) -> str:
    """Build the explicit user message for SVG design strategy."""
    payload = {
        "confirmed_requirement_json": requirement.model_dump(mode="json"),
        "deck_content_plan_json": content_plan.model_dump(mode="json"),
        "fallback_design_confirmation_json": fallback.confirmation.model_dump(mode="json"),
        "fallback_design_strategy_json": fallback.strategy.model_dump(mode="json"),
        "fallback_svg_execution_plan_json": fallback.execution_plan.model_dump(mode="json"),
        "route_capabilities": {
            "route": "svg",
            "supported_svg_subset": sorted(SUPPORTED_SVG_TAGS),
            "convertible_visual_tags": sorted(CONVERTIBLE_VISUAL_TAGS),
            "forbidden_svg_tags": sorted(FORBIDDEN_SVG_TAGS),
            "forbidden_svg_attributes": sorted(FORBIDDEN_SVG_ATTRS),
            "pptx_export": "strict native DrawingML PPTX conversion; unsupported visual SVG fails before output replacement",
            "image_policy": "local workspace image hrefs or data image URIs only",
            "color_policy": "HEX colors plus opacity attributes; defs-based linear/radial gradients are supported; no CSS or rgba()",
        },
    }
    return (
        "Create the PPT design strategy and SVG execution plan for this deck.\n"
        "Save the strategy only through save_ppt_design_strategy and save_ppt_svg_execution_plan.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _build_svg_executor_user_message(
    *,
    requirement: ConfirmedRequirement,
    content_plan: DeckContentPlan,
    design_stage: SvgDesignStrategyResult,
) -> str:
    """Build the explicit user message for SVG deck execution."""
    payload = {
        "confirmed_requirement_json": requirement.model_dump(mode="json"),
        "deck_content_plan_json": content_plan.model_dump(mode="json"),
        "design_strategy_json": design_stage.strategy.model_dump(mode="json"),
        "svg_execution_plan_json": design_stage.execution_plan.model_dump(mode="json"),
        "output_contract": {
            "tool": "save_ppt_svg_page",
            "call_count": len(content_plan.pages),
            "arguments": ["slide_number", "svg_content", "file_name", "title", "page_type", "page_rhythm"],
        },
    }
    return (
        "Generate SVG pages for this PPT deck.\n"
        "For each slide in order: call read_ppt_svg_execution_plan, generate one converter-safe SVG, "
        "then call save_ppt_svg_page once for that slide.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _persist_svg_design_to_state(
    tool_context: ToolContext | None,
    design_stage: SvgDesignStrategyResult,
) -> None:
    """Persist resolved SVG design artifacts into parent state."""
    if tool_context is None:
        return
    tool_context.state[PPT_DESIGN_CONFIRMATION_STATE_KEY] = design_stage.confirmation.model_dump(mode="json")
    tool_context.state[PPT_DESIGN_STRATEGY_STATE_KEY] = design_stage.strategy.model_dump(mode="json")
    tool_context.state[PPT_SVG_EXECUTION_PLAN_STATE_KEY] = design_stage.execution_plan.model_dump(mode="json")


def _normalize_svg_page_results(
    pages: list[dict[str, Any]],
    *,
    content_plan: DeckContentPlan,
) -> list[PptSvgPageResult]:
    """Validate and normalize saved SVG page results."""
    pages_by_number: dict[int, PptSvgPageResult] = {}
    for item in pages:
        if not isinstance(item, dict):
            continue
        try:
            page = PptSvgPageResult.model_validate(item)
        except Exception:
            continue
        pages_by_number[page.slide_number] = page
    normalized_pages: list[PptSvgPageResult] = []
    for planned_page in content_plan.pages:
        saved_page = pages_by_number.get(planned_page.slide_number)
        if saved_page is None:
            raise ValueError(f"Missing generated SVG for slide {planned_page.slide_number}.")
        normalized_pages.append(saved_page)
    return normalized_pages


def _svg_text(
    *,
    text: str,
    x: int,
    y: int,
    max_chars: int,
    font_size: int,
    fill: str,
    font_family: str,
    font_weight: str = "500",
    data_width: int = 720,
) -> str:
    """Render wrapped text as SVG text/tspan elements."""
    clean_text = str(text or "").strip()
    if not clean_text:
        clean_text = " "
    lines = _wrap_text_for_svg(clean_text, max_chars=max_chars)
    tspans = []
    for index, line in enumerate(lines):
        dy = "0" if index == 0 else str(round(font_size * 1.24, 1))
        tspans.append(f'<tspan x="{x}" dy="{dy}">{html.escape(line)}</tspan>')
    return (
        f'<text x="{x}" y="{y}" data-width="{data_width}" font-family="{font_family}" '
        f'font-size="{font_size}" fill="{fill}" font-weight="{font_weight}">'
        f'{"".join(tspans)}</text>'
    )


def _wrap_text_for_svg(text: str, *, max_chars: int) -> list[str]:
    """Wrap text for deterministic SVG text boxes."""
    clean_text = re.sub(r"\s+", " ", str(text or "").strip())
    if not clean_text:
        return [""]
    if re.search(r"[\u4e00-\u9fff]", clean_text):
        return textwrap.wrap(clean_text, width=max_chars, break_long_words=True, replace_whitespace=False)[:4]
    return textwrap.wrap(clean_text, width=max_chars, break_long_words=False)[:4]


def _extract_page_bullet_texts(page: DeckPagePlan) -> list[str]:
    """Extract concise bullet text from a page plan."""
    items: list[str] = []
    for block in page.content_blocks:
        if isinstance(block, str):
            items.append(block)
            continue
        if not isinstance(block, dict):
            continue
        for key in ("title", "summary", "text", "body"):
            value = str(block.get(key) or "").strip()
            if value:
                items.append(value)
        raw_items = block.get("items") or block.get("bullets")
        if isinstance(raw_items, list):
            items.extend(str(item).strip() for item in raw_items if str(item or "").strip())
    return _dedupe_preserve_order(items)


def _first_visual_label(page: DeckPagePlan) -> str:
    """Return a short visual label for the route-generated placeholder."""
    if page.assets:
        first_asset = page.assets[0]
        return first_asset.alt or first_asset.description or first_asset.prompt or page.asset_intent or "Supporting visual"
    return page.asset_intent or "Supporting visual"


def _page_rhythm(page: DeckPagePlan) -> str:
    """Return a simple rhythm label for one page."""
    if page.page_type in {"cover", "chapter_start"}:
        return "anchor"
    if page.page_type in {"ending", "quote", "stat"}:
        return "closure"
    return "body"


def _canvas_size(aspect_ratio: str) -> tuple[int, int]:
    """Return route canvas size from aspect ratio."""
    return (1024, 768) if aspect_ratio == "4:3" else (1280, 720)


def _normalize_aspect_ratio(aspect_ratio: str) -> str:
    """Normalize unsupported aspect ratios to the SVG route default."""
    clean_ratio = str(aspect_ratio or "").strip()
    return clean_ratio if clean_ratio in _SUPPORTED_ASPECT_RATIOS else "16:9"


def _local_name(tag: str) -> str:
    """Return an XML tag name without namespace."""
    return str(tag or "").rsplit("}", 1)[-1]


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """Deduplicate strings while preserving order."""
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        clean_item = str(item or "").strip()
        if not clean_item or clean_item in seen:
            continue
        seen.add(clean_item)
        deduped.append(clean_item)
    return deduped


def _copy_state(state: Any) -> dict[str, Any]:
    """Return a deep copy of an ADK state object or plain dict."""
    if hasattr(state, "to_dict"):
        return copy.deepcopy(state.to_dict())
    return copy.deepcopy(dict(state))


def _resolve_child_artifact_service(
    *,
    tool_context: ToolContext,
    fallback_service: BaseArtifactService,
) -> BaseArtifactService:
    """Pick the artifact service for an internal route runner."""
    required_methods = ("save_artifact", "load_artifact", "list_artifacts")
    if all(hasattr(tool_context, method_name) for method_name in required_methods):
        return ToolContextArtifactService(tool_context)
    return fallback_service


def _build_child_runner(
    *,
    agent: BaseAgent,
    app_name: str,
    session_service: InMemorySessionService,
    artifact_service: BaseArtifactService,
    invocation_context: Any,
) -> Runner:
    """Create a child ADK runner for SVG route experts."""
    child_plugins = getattr(getattr(invocation_context, "plugin_manager", None), "plugins", None)
    runner_kwargs = {
        "app_name": app_name,
        "session_service": session_service,
        "artifact_service": artifact_service,
        "memory_service": InMemoryMemoryService(),
        "credential_service": getattr(invocation_context, "credential_service", None),
    }
    if child_plugins:
        runner_kwargs["app"] = App(
            name=app_name,
            root_agent=agent,
            plugins=list(child_plugins),
        )
    else:
        runner_kwargs["agent"] = agent
    return Runner(**runner_kwargs)


def _append_svg_route_warning(state: Any, warning: str) -> None:
    """Append one SVG route warning to state."""
    clean_warning = str(warning or "").strip()
    if not clean_warning:
        return
    warnings = list(state.get(PPT_SVG_ROUTE_WARNINGS_KEY) or [])
    warnings.append(clean_warning)
    state[PPT_SVG_ROUTE_WARNINGS_KEY] = warnings


__all__ = [
    "PPT_DESIGN_CONFIRMATION_STATE_KEY",
    "PPT_DESIGN_STRATEGY_EXPERT_NAME",
    "PPT_DESIGN_STRATEGY_STATE_KEY",
    "PPT_SVG_DECK_EXECUTOR_EXPERT_NAME",
    "PPT_SVG_EXECUTION_PLAN_STATE_KEY",
    "PPT_SVG_ROUTE_CONTENT_PLAN_KEY",
    "PPT_SVG_ROUTE_GENERATED_PAGES_KEY",
    "PPT_SVG_ROUTE_OUTPUT_DIR_KEY",
    "SVG_DELIVERY_STAGE",
    "SVG_ROUTE_STAGE_SEQUENCE",
    "SvgDesignStrategyResult",
    "SvgPageGenerationResult",
    "SvgPptxOutputResult",
    "SvgQualityDeliveryResult",
    "SvgRoutePaths",
    "build_default_svg_design_strategy",
    "build_ppt_design_strategy_expert",
    "build_ppt_svg_deck_executor_expert",
    "build_svg_design_strategy_with_agent",
    "build_svg_route",
    "build_svg_route_with_agent",
    "check_svg_pages_quality",
    "deliver_svg_route_quality",
    "export_svg_pages_to_pptx",
    "generate_svg_pages",
    "generate_svg_pages_with_agent",
    "prepare_svg_route_paths",
    "render_svg_slide",
]
