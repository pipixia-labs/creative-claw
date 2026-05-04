"""Deck content planning for the PPT product line."""

from __future__ import annotations

from typing import Any

from google.adk.agents import LlmAgent

from conf.llm import build_llm
from src.productions.ppt.schemas import (
    ConfirmedRequirement,
    DeckChapter,
    DeckContentPlan,
    DeckPagePlan,
    SourceUnderstanding,
)


class PptContentPlanner:
    """Build source-aware, template-independent deck plans."""

    def build_agent(self) -> LlmAgent:
        """Build the ADK shell for future LLM-based deck content planning."""
        return LlmAgent(
            name="PptContentPlanningAgent",
            model=build_llm(),
            instruction=(
                "Create a template-independent DeckContentPlan from ConfirmedRequirement. "
                "Use source_understanding.markdown_sources and figures as source material references; "
                "read the referenced Markdown files before making content decisions. "
                "The plan must include cover, toc, chapter_start, chapter_content, and ending pages."
            ),
            output_key="ppt_deck_content_plan",
        )

    def build_plan(self, requirement: ConfirmedRequirement) -> DeckContentPlan:
        """Build a deterministic source-aware deck plan for the current HTML MVP."""
        understanding = requirement.source_understanding
        slide_count = _select_slide_count(requirement)
        chapters = _build_chapters(understanding)
        key_points = _build_key_points(requirement, understanding)
        source_preference = "user" if understanding.figures else "placeholder"

        pages: list[DeckPagePlan] = [
            _build_page(
                slide_number=1,
                page_type="cover",
                title=requirement.topic,
                purpose="Introduce the deck topic and set the audience context.",
                chapter="",
                key_takeaway=key_points[0],
                content_blocks=[
                    {"title": "Audience", "body": requirement.audience or "Primary stakeholders"},
                    {"title": "Source basis", "body": _source_basis_text(understanding)},
                ],
                asset_intent="A clean title composition anchored by the prepared source materials.",
                asset_source_preference=source_preference,
            ),
            _build_page(
                slide_number=2,
                page_type="toc",
                title="Agenda",
                purpose="Orient the audience before the main content.",
                chapter="",
                key_takeaway="The deck is organized around prepared source material references.",
                content_blocks=[
                    {"title": chapter.title, "body": chapter.purpose}
                    for chapter in chapters[:4]
                ],
                asset_intent="A structured agenda list.",
                asset_source_preference=source_preference,
            ),
            _build_page(
                slide_number=3,
                page_type="chapter_start",
                title=chapters[0].title,
                purpose=chapters[0].purpose,
                chapter=chapters[0].title,
                key_takeaway=key_points[0],
                content_blocks=[
                    {"title": "Chapter focus", "body": chapters[0].purpose},
                    {"title": "Primary signal", "body": key_points[0]},
                ],
                asset_intent="A section divider with one concise material-backed message.",
                asset_source_preference=source_preference,
            ),
        ]

        content_page_count = max(1, slide_count - 4)
        for index in range(content_page_count):
            slide_number = 4 + index
            chapter = chapters[index % len(chapters)]
            point = key_points[index % len(key_points)]
            support = key_points[(index + 1) % len(key_points)]
            pages.append(
                _build_page(
                    slide_number=slide_number,
                    page_type="chapter_content",
                    title=_compact_title(point, fallback=f"Key Point {index + 1}"),
                    purpose=f"Develop the {chapter.title} part of the story.",
                    chapter=chapter.title,
                    key_takeaway=point,
                    content_blocks=[
                        {"title": "Core message", "body": point},
                        {"title": "Supporting signal", "body": support},
                        {"title": "Implication", "body": _build_implication(point)},
                    ],
                    asset_intent=_asset_intent(understanding),
                    asset_source_preference=source_preference,
                )
            )

        pages.append(
            _build_page(
                slide_number=slide_count,
                page_type="ending",
                title="Summary",
                purpose="Close the deck with a clear next action.",
                chapter=chapters[-1].title,
                key_takeaway=_closing_takeaway(key_points),
                content_blocks=[
                    {"title": "What matters", "body": key_points[0]},
                    {"title": "Next action", "body": "Use this PPTX draft as the reviewable first delivery artifact."},
                ],
                asset_intent="A calm closing visual with clear next-step emphasis.",
                asset_source_preference=source_preference,
            )
        )

        return DeckContentPlan(
            title=requirement.topic,
            core_narrative=_core_narrative(requirement, key_points),
            chapters=chapters,
            pages=pages,
        )


def _select_slide_count(requirement: ConfirmedRequirement) -> int:
    """Select a practical slide count while keeping required page types present."""
    requested_count = requirement.slide_count_policy.target or requirement.slide_count_policy.maximum
    return max(5, int(requested_count or 5))


def _build_chapters(understanding: SourceUnderstanding) -> list[DeckChapter]:
    """Build deck chapters from prepared source material names with stable fallbacks."""
    chapter_titles = [
        _title_from_source_record(source)
        for source in understanding.markdown_sources
    ]
    if not chapter_titles:
        chapter_titles = ["Context", "Insight", "Next Steps"]

    deduped_titles = _dedupe_text(chapter_titles)[:4]
    if len(deduped_titles) == 1:
        deduped_titles.append("Implications")
    if len(deduped_titles) == 2:
        deduped_titles.append("Next Steps")
    return [
        DeckChapter(
            title=title,
            purpose=f"Explain {title} using prepared source materials.",
            order=index,
        )
        for index, title in enumerate(deduped_titles, start=1)
    ]


def _build_key_points(
    requirement: ConfirmedRequirement,
    understanding: SourceUnderstanding,
) -> list[str]:
    """Build generic planning anchors without pre-extracting source content."""
    key_points = [
        requirement.topic,
        _source_basis_text(understanding),
        "The content planning agent should read prepared Markdown sources before finalizing slide content.",
    ]
    while len(key_points) < 3:
        key_points.append(key_points[-1])
    return key_points


def _build_page(
    *,
    slide_number: int,
    page_type: str,
    title: str,
    purpose: str,
    chapter: str,
    key_takeaway: str,
    content_blocks: list[dict[str, Any]],
    asset_intent: str,
    asset_source_preference: str,
) -> DeckPagePlan:
    """Build one template-independent slide plan."""
    return DeckPagePlan(
        slide_number=slide_number,
        page_type=page_type,
        title=title,
        purpose=purpose,
        chapter=chapter,
        key_takeaway=key_takeaway,
        content_blocks=content_blocks,
        asset_intent=asset_intent,
        asset_roles=["supporting_visual"],
        asset_semantic_positions=["bottom_band"],
        asset_source_preference=asset_source_preference,
    )


def _source_basis_text(understanding: SourceUnderstanding) -> str:
    """Summarize what kind of material shaped the plan."""
    if understanding.markdown_sources:
        return f"{len(understanding.markdown_sources)} converted Markdown source(s) are available for planning."
    if understanding.extraction_warnings:
        return "Source files were listed, but extraction needs review."
    return "No source file was provided; the plan uses the task request as its brief."


def _asset_intent(understanding: SourceUnderstanding) -> str:
    """Describe the preferred visual treatment for content pages."""
    if understanding.figures:
        return "Use the provided figures where they support the slide message."
    if understanding.markdown_sources:
        return "Use a restrained evidence block based on the prepared Markdown sources."
    return "Use a restrained evidence block that supports the requested topic."


def _compact_title(text: str, *, fallback: str) -> str:
    """Turn one key point into a slide-safe title."""
    clean_text = " ".join(str(text or "").split())
    if not clean_text:
        return fallback
    if len(clean_text) <= 46:
        return clean_text
    return clean_text[:45].rstrip() + "..."


def _build_implication(point: str) -> str:
    """Build a concise implication sentence from one source point."""
    clean_point = " ".join(str(point or "").split())
    if not clean_point:
        return "Clarify the practical implication before final delivery."
    return f"This points to a focused slide narrative around: {clean_point[:96]}"


def _closing_takeaway(key_points: list[str]) -> str:
    """Build the final takeaway from the first and last source signals."""
    if not key_points:
        return "The PPT product line can now produce a reviewable first deck."
    if len(key_points) == 1:
        return key_points[0]
    return f"{key_points[0]} The closing emphasis is: {key_points[-1]}"


def _core_narrative(requirement: ConfirmedRequirement, key_points: list[str]) -> str:
    """Summarize the deck's narrative arc."""
    leading_point = key_points[0] if key_points else requirement.topic
    return f"{requirement.topic} is framed around prepared source materials and the requested audience outcome: {leading_point}"


def _title_from_source_record(source: dict[str, Any]) -> str:
    """Build a chapter-like label from one prepared source material record."""
    raw_name = str(source.get("name") or source.get("output_path") or source.get("source_path") or "").strip()
    if not raw_name:
        return ""
    stem = raw_name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    title = stem.replace("_", " ").replace("-", " ").strip()
    return title.title() if title else ""


def _dedupe_text(items: list[str]) -> list[str]:
    """Dedupe non-empty strings while preserving order."""
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        clean_item = " ".join(str(item or "").split()).strip()
        key = clean_item.lower()
        if not clean_item or key in seen:
            continue
        seen.add(key)
        deduped.append(clean_item)
    return deduped
