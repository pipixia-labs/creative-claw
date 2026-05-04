"""Deck content planning for the PPT product line."""

from __future__ import annotations

import copy
import inspect
import json
import re
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
from src.productions.ppt.schemas import (
    ConfirmedRequirement,
    DeckChapter,
    DeckContentPlan,
    DeckPageAsset,
    DeckPagePlan,
    SourceUnderstanding,
)
from src.runtime.expert_dispatcher import dispatch_expert_call
from src.runtime.tool_context_artifact_service import ToolContextArtifactService
from src.runtime.workspace import resolve_workspace_path

PPT_CONTENT_PLANNING_OUTPUT_KEY = "ppt_deck_content_plan"
PPT_CONTENT_PLANNING_MARKDOWN_KEY = "ppt_deck_content_plan_markdown"
PPT_CONTENT_PLANNING_AGENT_MESSAGE_KEY = "ppt_content_planning_agent_message"
PPT_MARKDOWN_SOURCE_TEXTS_STATE_KEY = "ppt_markdown_source_texts"
PPT_CONTENT_PLANNING_WARNINGS_STATE_KEY = "ppt_content_planning_warnings"
_NUMBERED_LIST_RE = re.compile(r"^\d+[.)]\s+(.+)$")
_SLIDE_HEADING_RE = re.compile(r"^##\s+Slide\s+(\d+)\s*\|\s*([^|]+)\|\s*(.+)$", re.IGNORECASE)


class PptContentPlanner:
    """Build source-aware, template-independent deck plans."""

    def build_agent(self) -> LlmAgent:
        """Build the ADK agent for deck content planning."""
        return LlmAgent(
            name="PptContentPlanningAgent",
            model=build_llm(),
            instruction=(
                "You are Creative Claw's PPT content planning agent.\n"
                "Create a template-independent Markdown deck plan from the confirmed PPT requirement.\n"
                "Use `ppt_confirmed_requirement.request_brief` as the primary task brief. "
                "Use `source_understanding` only for user-provided documents and assets, not for the task text itself.\n"
                "Always call read_ppt_markdown_sources first so source Markdown is read before planning.\n"
                "Then call save_ppt_deck_content_plan_markdown with one Markdown string.\n"
                "The plan must include cover, toc, chapter_start, chapter_content, and ending pages.\n"
                "Do not copy the raw user request, delivery command, file format request, or style instructions "
                "into slide titles or slide body text. Treat those as planning metadata only.\n"
                "Use concise audience-facing titles. For example, a request to make a PPTX for humanities "
                "students about AI should become a title like `文科生也能理解的AI`, not the original task sentence.\n"
                "Use exactly this Markdown shape:\n"
                "# Deck: <deck title>\n"
                "Audience: <audience>\n"
                "Language: <language>\n"
                "SlideCount: <count>\n"
                "Narrative: <one sentence>\n\n"
                "## Slide 1 | cover | <title>\n"
                "Purpose: <why this slide exists>\n"
                "Takeaway: <what the audience should remember>\n"
                "Content:\n"
                "- <short audience-facing point>\n"
                "Visual:\n"
                "- ai | role=hero | description=<image prompt without text inside image>\n\n"
                "Visual source kinds are: ai, search, material, user, placeholder. "
                "Use `search | role=reference | query=... | description=...` for web images. "
                "Use `placeholder | role=grid | description=...` for layout-only image placeholders.\n"
                "When style_keywords include `illustrated`, `kid_friendly`, or the user asks for 图文并茂, "
                "配图, 插图, children, kindergarten, or flashcards, plan image_generation assets for the "
                "cover, chapter, and content pages unless suitable material_figure/user_upload assets already exist. "
                "For young children, asset prompts must be safe, colorful, simple, and easy to recognize.\n"
                "For kindergarten English word decks, plan concrete word-card pages such as Apple 苹果, "
                "Cat 猫, Dog 狗, Ball 球, Book 书, Sun 太阳, plus a review game. "
                "Never use internal placeholders like Context, Insight, Next Steps, No source file, "
                "or ContentPlanningAgent in slide titles or slide body text.\n"
                "Keep slide content concise, material-backed, and independent from HTML/SVG/XML templates."
            ),
            tools=[
                self.read_ppt_markdown_sources,
                self.save_ppt_deck_content_plan_markdown,
            ],
            output_key=PPT_CONTENT_PLANNING_AGENT_MESSAGE_KEY,
        )

    async def build_plan_with_agent(
        self,
        requirement: ConfirmedRequirement,
        *,
        tool_context: ToolContext,
        app_name: str,
        artifact_service: BaseArtifactService | None,
    ) -> DeckContentPlan:
        """Run the ADK content planning agent, falling back to deterministic planning."""
        if not hasattr(tool_context, "_invocation_context"):
            return self._build_fallback_plan(
                requirement,
                tool_context=tool_context,
                warning="Content planning agent skipped because no ADK invocation context was available.",
            )

        invocation_context = tool_context._invocation_context
        child_session_service = InMemorySessionService()
        child_artifact_service = _resolve_child_artifact_service(
            tool_context=tool_context,
            fallback_service=artifact_service or InMemoryArtifactService(),
        )
        planner_agent = self.build_agent()
        child_runner = _build_child_runner(
            agent=planner_agent,
            app_name=app_name,
            session_service=child_session_service,
            artifact_service=child_artifact_service,
            invocation_context=invocation_context,
        )
        child_state = _copy_state(tool_context.state)
        child_state["ppt_confirmed_requirement"] = requirement.model_dump(mode="json")

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
                            text=_build_content_planning_user_message(requirement)
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
            markdown_plan = final_state.get(PPT_CONTENT_PLANNING_MARKDOWN_KEY)
            if not markdown_plan:
                raise ValueError("PptContentPlanningAgent did not save a Markdown deck plan.")
            plan_payload = final_state.get(PPT_CONTENT_PLANNING_OUTPUT_KEY)
            if not plan_payload:
                raise ValueError("PptContentPlanningAgent did not save ppt_deck_content_plan.")
            plan = DeckContentPlan.model_validate(plan_payload)
            tool_context.state[PPT_CONTENT_PLANNING_MARKDOWN_KEY] = str(markdown_plan)
            if final_state.get(PPT_CONTENT_PLANNING_AGENT_MESSAGE_KEY):
                tool_context.state[PPT_CONTENT_PLANNING_AGENT_MESSAGE_KEY] = str(
                    final_state.get(PPT_CONTENT_PLANNING_AGENT_MESSAGE_KEY)
                )
            tool_context.state[PPT_CONTENT_PLANNING_OUTPUT_KEY] = plan.model_dump(mode="json")
            tool_context.state["ppt_content_planning_output"] = {
                "status": "success",
                "message": "PptContentPlanningAgent produced DeckContentPlan.",
                "source": "llm_agent",
            }
            return plan
        except Exception as exc:
            return self._build_fallback_plan(
                requirement,
                tool_context=tool_context,
                warning=f"Content planning agent fallback: {type(exc).__name__}: {exc}",
            )
        finally:
            await child_runner.close()

    async def resolve_plan_assets(
        self,
        plan: DeckContentPlan,
        requirement: ConfirmedRequirement,
        *,
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent] | None = None,
        app_name: str = "creative_claw",
        artifact_service: BaseArtifactService | None = None,
        asset_resolver: Any | None = None,
    ) -> DeckContentPlan:
        """Resolve planned slide assets before route-specific page generation."""
        resolved_plan = plan.model_copy(deep=True)
        expert_agents = expert_agents or {}
        ready_assets = _collect_ready_input_assets(requirement)
        used_ready_paths: set[str] = set()
        warnings: list[str] = []

        for page in resolved_plan.pages:
            page.assets = [DeckPageAsset.model_validate(asset) for asset in page.assets]
            if not page.assets:
                assigned_asset = _next_ready_asset_for_page(
                    ready_assets,
                    used_paths=used_ready_paths,
                    page=page,
                )
                if assigned_asset is not None:
                    page.assets.append(assigned_asset)
            if not page.assets:
                pending_asset = _build_pending_asset_request(page, requirement)
                if pending_asset is not None:
                    page.assets.append(pending_asset)

            resolved_assets: list[DeckPageAsset] = []
            for asset in page.assets:
                resolved_asset = await self._resolve_single_asset(
                    asset,
                    page=page,
                    requirement=requirement,
                    tool_context=tool_context,
                    expert_agents=expert_agents,
                    app_name=app_name,
                    artifact_service=artifact_service,
                    asset_resolver=asset_resolver,
                )
                warnings.extend(resolved_asset.warnings)
                resolved_assets.append(resolved_asset)
            page.assets = resolved_assets

        manifest = _build_resolved_asset_manifest(resolved_plan)
        tool_context.state["ppt_resolved_asset_manifest"] = manifest
        tool_context.state[PPT_CONTENT_PLANNING_OUTPUT_KEY] = resolved_plan.model_dump(mode="json")
        for warning in warnings:
            _append_planning_warning(tool_context.state, warning)
        return resolved_plan

    async def _resolve_single_asset(
        self,
        asset: DeckPageAsset,
        *,
        page: DeckPagePlan,
        requirement: ConfirmedRequirement,
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: BaseArtifactService | None,
        asset_resolver: Any | None,
    ) -> DeckPageAsset:
        """Resolve one pending asset through injected or expert-agent resolvers."""
        asset = DeckPageAsset.model_validate(asset)
        if asset.status == "ready" and asset.path and _workspace_image_exists(asset.path):
            return asset
        if asset.path and asset.source_kind in {"material_figure", "user_upload"}:
            if _workspace_image_exists(asset.path):
                return asset.model_copy(update={"status": "ready"})
            return _mark_asset_failed(asset, f"Planned asset path is missing or unsupported: {asset.path}")

        if asset_resolver is not None and asset.source_kind in {"search", "image_generation"}:
            resolved = await _call_asset_resolver(
                asset_resolver,
                asset=asset,
                page=page,
                requirement=requirement,
            )
            if resolved is not None:
                return _merge_resolved_asset(asset, resolved)

        if asset.source_kind == "search":
            return await self._resolve_asset_with_expert(
                asset,
                agent_name="SearchAgent",
                parameters={
                    "query": asset.search_query or asset.description or page.title,
                    "mode": "image",
                },
                tool_context=tool_context,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
            )
        if asset.source_kind == "image_generation":
            return await self._resolve_asset_with_expert(
                asset,
                agent_name="ImageGenerationAgent",
                parameters={
                    "prompt": asset.prompt or asset.description or page.asset_intent or page.title,
                    "aspect_ratio": asset.aspect_ratio or requirement.aspect_ratio,
                    "resolution": asset.resolution or "1K",
                },
                tool_context=tool_context,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
            )
        if asset.status == "pending":
            return _mark_asset_failed(asset, f"No resolver was available for asset source_kind `{asset.source_kind}`.")
        return asset

    async def _resolve_asset_with_expert(
        self,
        asset: DeckPageAsset,
        *,
        agent_name: str,
        parameters: dict[str, Any],
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: BaseArtifactService | None,
    ) -> DeckPageAsset:
        """Resolve an asset by invoking a registered expert as an ADK agent."""
        if not _can_dispatch_expert(
            agent_name=agent_name,
            tool_context=tool_context,
            expert_agents=expert_agents,
            artifact_service=artifact_service,
        ):
            return _mark_asset_failed(asset, f"{agent_name} is not available for PPT asset resolution.")

        invocation = await dispatch_expert_call(
            agent_name=agent_name,
            prompt=json.dumps(parameters, ensure_ascii=False),
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        current_output = invocation.current_output
        output_files = list(current_output.get("output_files") or invocation.tool_result.get("output_files") or [])
        output_path = _first_existing_output_path(output_files)
        if current_output.get("status") == "success" and output_path:
            return asset.model_copy(
                update={
                    "status": "ready",
                    "path": output_path,
                    "provider": agent_name,
                }
            )
        message = str(current_output.get("message") or f"{agent_name} did not return an image file.")
        return _mark_asset_failed(asset, message)

    def read_ppt_markdown_sources(self, tool_context: ToolContext) -> dict[str, Any]:
        """Read prepared Markdown source files for PPT content planning."""
        requirement_payload = tool_context.state.get("ppt_confirmed_requirement") or {}
        requirement = ConfirmedRequirement.model_validate(requirement_payload)
        source_records = list(requirement.source_understanding.markdown_sources)
        if not source_records:
            source_records = list(tool_context.state.get("ppt_source_markdown_sources") or [])

        source_texts: list[dict[str, Any]] = []
        warnings: list[str] = []
        remaining_chars = 24000
        for source in source_records:
            output_path = str(source.get("output_path") or "").strip()
            if not output_path:
                warnings.append(f"Markdown source `{source.get('name', '')}` has no output_path.")
                continue
            try:
                text = resolve_workspace_path(output_path).read_text(encoding="utf-8")
            except Exception as exc:
                warnings.append(f"Could not read Markdown source `{output_path}`: {exc}")
                continue

            clipped_text = text[: max(0, remaining_chars)]
            remaining_chars -= len(clipped_text)
            source_texts.append(
                {
                    "name": str(source.get("name") or output_path),
                    "output_path": output_path,
                    "text": clipped_text,
                    "truncated": len(clipped_text) < len(text),
                }
            )
            if remaining_chars <= 0:
                break

        payload = {
            "status": "success",
            "source_texts": source_texts,
            "warnings": warnings,
        }
        tool_context.state[PPT_MARKDOWN_SOURCE_TEXTS_STATE_KEY] = source_texts
        if warnings:
            _append_planning_warning(tool_context.state, "; ".join(warnings))
        return payload

    def save_ppt_deck_content_plan(
        self,
        plan: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Validate and save the final PPT DeckContentPlan."""
        requirement_payload = tool_context.state.get("ppt_confirmed_requirement") or {}
        requirement = ConfirmedRequirement.model_validate(requirement_payload)
        validated_plan = DeckContentPlan.model_validate(plan)
        _validate_plan_matches_requirement(validated_plan, requirement=requirement)
        payload = validated_plan.model_dump(mode="json")
        tool_context.state[PPT_CONTENT_PLANNING_OUTPUT_KEY] = payload
        tool_context.state["current_output"] = {
            "status": "success",
            "message": "PptContentPlanningAgent saved DeckContentPlan.",
            "deck_content_plan": payload,
        }
        return {
            "status": "success",
            "message": "DeckContentPlan saved.",
            "deck_content_plan": payload,
        }

    def save_ppt_deck_content_plan_markdown(
        self,
        markdown: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Parse, validate, and save a Markdown PPT deck plan."""
        requirement_payload = tool_context.state.get("ppt_confirmed_requirement") or {}
        requirement = ConfirmedRequirement.model_validate(requirement_payload)
        plan = parse_ppt_deck_plan_markdown(markdown, requirement=requirement)
        _validate_plan_matches_requirement(plan, requirement=requirement)
        payload = plan.model_dump(mode="json")
        tool_context.state[PPT_CONTENT_PLANNING_MARKDOWN_KEY] = str(markdown or "")
        tool_context.state[PPT_CONTENT_PLANNING_OUTPUT_KEY] = payload
        tool_context.state["current_output"] = {
            "status": "success",
            "message": "PptContentPlanningAgent saved Markdown DeckContentPlan.",
            "deck_content_plan_markdown": str(markdown or ""),
            "deck_content_plan": payload,
        }
        return {
            "status": "success",
            "message": "Markdown DeckContentPlan saved.",
            "deck_content_plan": payload,
        }

    def build_plan(
        self,
        requirement: ConfirmedRequirement,
        *,
        source_texts: list[dict[str, Any]] | None = None,
    ) -> DeckContentPlan:
        """Build a deterministic source-aware deck plan for the current HTML MVP."""
        understanding = requirement.source_understanding
        if _should_use_kindergarten_english_plan(requirement, understanding):
            return parse_ppt_deck_plan_markdown(
                _build_kindergarten_english_word_markdown(requirement),
                requirement=requirement,
            )

        slide_count = _select_slide_count(requirement)
        chapters = _build_chapters(understanding)
        key_points = _build_key_points(requirement, understanding, source_texts=source_texts)
        source_preference = _select_asset_source_preference(requirement, understanding)

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
                asset_intent=_cover_asset_intent(requirement, understanding),
                asset_source_preference=_asset_source_preference_for_page("cover", source_preference),
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
                asset_source_preference=_asset_source_preference_for_page("toc", source_preference),
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
                asset_intent=_chapter_asset_intent(requirement, chapters[0].title),
                asset_source_preference=_asset_source_preference_for_page("chapter_start", source_preference),
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
                    asset_intent=_asset_intent(requirement, understanding),
                    asset_source_preference=_asset_source_preference_for_page("chapter_content", source_preference),
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
                asset_source_preference=_asset_source_preference_for_page("ending", source_preference),
            )
        )

        return DeckContentPlan(
            title=requirement.topic,
            core_narrative=_core_narrative(requirement, key_points),
            chapters=chapters,
            pages=pages,
        )

    def _build_fallback_plan(
        self,
        requirement: ConfirmedRequirement,
        *,
        tool_context: ToolContext,
        warning: str,
    ) -> DeckContentPlan:
        """Build and record deterministic fallback planning output."""
        self.read_ppt_markdown_sources(tool_context)
        plan = self.build_plan(
            requirement,
            source_texts=list(tool_context.state.get(PPT_MARKDOWN_SOURCE_TEXTS_STATE_KEY) or []),
        )
        _append_planning_warning(tool_context.state, warning)
        tool_context.state[PPT_CONTENT_PLANNING_OUTPUT_KEY] = plan.model_dump(mode="json")
        tool_context.state["ppt_content_planning_output"] = {
            "status": "fallback",
            "message": warning,
            "source": "deterministic",
        }
        return plan


def _build_content_planning_user_message(requirement: ConfirmedRequirement) -> str:
    """Build the explicit user message passed to the child content-planning agent."""
    requirement_json = json.dumps(requirement.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return "\n".join(
        [
            "Plan the PPT deck from the ConfirmedRequirement JSON below.",
            "Use `request_brief` as the primary user task. Use `topic`, `audience`, `language`, "
            "`slide_count_policy`, and `style_requirement` as hard planning constraints.",
            "Use `source_understanding` only for user-provided documents and assets.",
            "Do not invent a generic business communication deck unless the request is actually business communication.",
            "Before saving, make sure the deck title, chapters, slide titles, and body content clearly match "
            "`request_brief`, `topic`, and `audience`.",
            "Call read_ppt_markdown_sources first, then call save_ppt_deck_content_plan_markdown with the final Markdown plan.",
            "",
            "ConfirmedRequirement JSON:",
            "```json",
            requirement_json,
            "```",
        ]
    )


def _validate_plan_matches_requirement(plan: DeckContentPlan, *, requirement: ConfirmedRequirement) -> None:
    """Reject content plans that are structurally valid but semantically off-task."""
    if _should_use_kindergarten_english_plan(requirement, requirement.source_understanding):
        if not _plan_matches_kindergarten_english_requirement(plan):
            raise ValueError(
                "DeckContentPlan does not match the kindergarten English-word task; "
                "expected concrete child-friendly English word-card pages."
            )


def _plan_matches_kindergarten_english_requirement(plan: DeckContentPlan) -> bool:
    """Return whether a plan looks like a child-friendly English word-card deck."""
    plan_text = _deck_plan_text(plan).lower()
    word_markers = (
        "apple",
        "cat",
        "dog",
        "ball",
        "book",
        "sun",
        "苹果",
        "猫",
        "狗",
        "球",
        "书",
        "太阳",
    )
    child_markers = ("幼儿园", "小朋友", "儿童", "孩子", "kindergarten", "child", "children", "kids")
    business_markers = ("目标对齐", "沟通稿", "商务", "协作安排", "行动确认", "团队需要")
    matched_words = sum(1 for marker in word_markers if marker in plan_text)
    has_child_marker = any(marker in plan_text for marker in child_markers)
    has_english_word_marker = any(marker in plan_text for marker in ("英语单词", "english word", "word-card", "word card"))
    has_business_contamination = any(marker in plan_text for marker in business_markers)
    return matched_words >= 3 and (has_child_marker or has_english_word_marker) and not has_business_contamination


def _deck_plan_text(plan: DeckContentPlan) -> str:
    """Flatten key plan text for lightweight semantic checks."""
    parts = [plan.title, plan.core_narrative]
    for chapter in plan.chapters:
        parts.extend([chapter.title, chapter.purpose])
    for page in plan.pages:
        parts.extend(
            [
                page.title,
                page.purpose,
                page.chapter,
                page.key_takeaway,
                page.asset_intent,
            ]
        )
        for block in page.content_blocks:
            if isinstance(block, dict):
                parts.extend([block.get("title", ""), block.get("body", "")])
            else:
                parts.extend([getattr(block, "title", ""), getattr(block, "body", "")])
        for asset in page.assets:
            if isinstance(asset, dict):
                parts.extend(
                    [
                        asset.get("description", ""),
                        asset.get("alt", ""),
                        asset.get("prompt", ""),
                        asset.get("search_query", ""),
                    ]
                )
            else:
                parts.extend([asset.description, asset.alt, asset.prompt, asset.search_query])
    return " ".join(str(part or "") for part in parts)


def _select_slide_count(requirement: ConfirmedRequirement) -> int:
    """Select a practical slide count while keeping required page types present."""
    requested_count = requirement.slide_count_policy.target or requirement.slide_count_policy.maximum
    return max(5, int(requested_count or 5))


def parse_ppt_deck_plan_markdown(markdown: str, *, requirement: ConfirmedRequirement) -> DeckContentPlan:
    """Parse the planner's fixed Markdown format into a DeckContentPlan."""
    clean_markdown = str(markdown or "").strip()
    if not clean_markdown:
        raise ValueError("Markdown deck plan is empty.")

    deck_meta: dict[str, str] = {}
    page_sections: list[dict[str, Any]] = []
    current_page: dict[str, Any] | None = None
    current_block = ""

    for raw_line in clean_markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        heading_match = _SLIDE_HEADING_RE.match(line)
        if heading_match:
            current_page = {
                "slide_number": int(heading_match.group(1)),
                "page_type": _normalize_page_type(heading_match.group(2)),
                "title": heading_match.group(3).strip(),
                "purpose": "",
                "key_takeaway": "",
                "content_items": [],
                "visual_items": [],
            }
            page_sections.append(current_page)
            current_block = ""
            continue

        if current_page is None:
            _parse_deck_meta_line(line, deck_meta)
            continue

        lowered = line.lower()
        if lowered.startswith("purpose:"):
            current_page["purpose"] = _value_after_colon(line)
            current_block = ""
        elif lowered.startswith("takeaway:"):
            current_page["key_takeaway"] = _value_after_colon(line)
            current_block = ""
        elif lowered.startswith("chapter:"):
            current_page["chapter"] = _value_after_colon(line)
            current_block = ""
        elif lowered == "content:":
            current_block = "content"
        elif lowered == "visual:":
            current_block = "visual"
        elif line.startswith(("- ", "* ")) and current_block == "content":
            current_page["content_items"].append(line[2:].strip())
        elif line.startswith(("- ", "* ")) and current_block == "visual":
            current_page["visual_items"].append(line[2:].strip())
        elif current_block == "content":
            current_page["content_items"].append(line)

    pages = _build_pages_from_markdown_sections(page_sections, requirement=requirement)
    title = deck_meta.get("title") or requirement.topic
    core_narrative = deck_meta.get("narrative") or f"{title} for {requirement.audience or 'the target audience'}."
    return DeckContentPlan(
        title=title,
        core_narrative=core_narrative,
        chapters=_chapters_from_pages(pages),
        pages=pages,
    )


def _parse_deck_meta_line(line: str, deck_meta: dict[str, str]) -> None:
    """Parse one deck-level metadata line from the Markdown plan."""
    normalized = line.strip()
    lowered = normalized.lower()
    if lowered.startswith("# deck:"):
        deck_meta["title"] = _value_after_colon(normalized.lstrip("#").strip())
        return
    if ":" not in normalized and "：" not in normalized:
        return
    key, value = re.split(r"[:：]", normalized, maxsplit=1)
    meta_key = key.strip().lstrip("#").lower()
    if meta_key in {"audience", "language", "slidecount", "narrative"}:
        deck_meta[meta_key] = value.strip()


def _build_pages_from_markdown_sections(
    sections: list[dict[str, Any]],
    *,
    requirement: ConfirmedRequirement,
) -> list[DeckPagePlan]:
    """Build validated page plans from parsed Markdown slide sections."""
    if not sections:
        raise ValueError("Markdown deck plan must contain at least one slide section.")

    pages: list[DeckPagePlan] = []
    current_chapter = ""
    for section in sorted(sections, key=lambda item: int(item.get("slide_number") or 0)):
        slide_number = int(section.get("slide_number") or len(pages) + 1)
        page_type = _normalize_page_type(str(section.get("page_type") or "chapter_content"))
        title = str(section.get("title") or f"Slide {slide_number}").strip()
        if page_type == "chapter_start":
            current_chapter = title
        chapter = str(section.get("chapter") or current_chapter).strip()

        parsed_assets = [
            _parse_visual_item(item, slide_number=slide_number, asset_index=index, requirement=requirement)
            for index, item in enumerate(section.get("visual_items") or [], start=1)
        ]
        route_assets = [asset for asset in parsed_assets if asset.source_kind != "placeholder"]
        content_blocks = _content_blocks_from_markdown_items(
            list(section.get("content_items") or []),
            fallback=title,
        )

        pages.append(
            DeckPagePlan(
                slide_number=slide_number,
                page_type=page_type,
                title=title,
                purpose=str(section.get("purpose") or f"Explain {title}."),
                chapter=chapter,
                key_takeaway=str(section.get("key_takeaway") or _first_content_body(content_blocks) or title),
                content_blocks=content_blocks,
                asset_intent=_asset_intent_from_assets(parsed_assets) or f"Visual support for {title}.",
                asset_roles=[asset.role for asset in parsed_assets] or ["supporting_visual"],
                asset_semantic_positions=[asset.semantic_position for asset in parsed_assets] or ["bottom_band"],
                asset_source_preference=_select_page_visual_preference(parsed_assets, fallback="placeholder"),
                assets=route_assets,
            )
        )
    return pages


def _parse_visual_item(
    item: str,
    *,
    slide_number: int,
    asset_index: int,
    requirement: ConfirmedRequirement,
) -> DeckPageAsset:
    """Parse one visual intent line into a slide asset request."""
    parts = [part.strip() for part in str(item or "").split("|") if part.strip()]
    source_kind = _visual_source_kind(parts[0] if parts else "placeholder")
    fields: dict[str, str] = {}
    loose_parts: list[str] = []
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            fields[key.strip().lower()] = value.strip()
        else:
            loose_parts.append(part)

    description = fields.get("description") or " ".join(loose_parts).strip() or str(item or "").strip()
    path = fields.get("path") or ""
    status = "ready" if path and _workspace_image_exists(path) else "pending"
    role = fields.get("role") or ("hero" if slide_number == 1 else "supporting_visual")
    semantic_position = fields.get("position") or fields.get("semantic_position") or "bottom_band"

    return DeckPageAsset(
        asset_id=f"slide_{slide_number:02d}_visual_{asset_index}",
        role=role,
        semantic_position=semantic_position,
        source_kind=source_kind,
        status=status,
        description=description,
        alt=fields.get("alt") or description or f"Slide {slide_number} visual",
        path=path,
        prompt=fields.get("prompt") or (description if source_kind == "image_generation" else ""),
        search_query=fields.get("query") or fields.get("search_query") or (description if source_kind == "search" else ""),
        aspect_ratio=fields.get("aspect_ratio") or requirement.aspect_ratio,
        resolution=fields.get("resolution") or "1K",
        placeholder_name=fields.get("placeholder") or fields.get("placeholder_name") or role,
    )


def _visual_source_kind(value: str) -> str:
    """Normalize Markdown visual source aliases to the schema source kind."""
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"ai", "generated", "image_generation", "imagegen"}:
        return "image_generation"
    if normalized in {"search", "web", "web_search"}:
        return "search"
    if normalized in {"material", "material_figure", "figure"}:
        return "material_figure"
    if normalized in {"user", "upload", "user_upload"}:
        return "user_upload"
    return "placeholder"


def _select_page_visual_preference(assets: list[DeckPageAsset], *, fallback: str) -> str:
    """Select a page-level visual preference from parsed visual intents."""
    source_kinds = [asset.source_kind for asset in assets if asset.source_kind != "placeholder"]
    if not source_kinds:
        return "placeholder" if assets else fallback
    mapped = {
        "image_generation": "ai",
        "search": "search",
        "material_figure": "user",
        "user_upload": "user",
    }
    preferences = {mapped.get(source_kind, "placeholder") for source_kind in source_kinds}
    if len(preferences) > 1:
        return "mixed"
    return next(iter(preferences))


def _content_blocks_from_markdown_items(items: list[str], *, fallback: str) -> list[dict[str, Any]]:
    """Convert Markdown content bullets into route-friendly content blocks."""
    blocks: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        clean_item = " ".join(str(item or "").split()).strip()
        if not clean_item:
            continue
        separator = "：" if "：" in clean_item else ":"
        if separator in clean_item:
            title, body = clean_item.split(separator, 1)
            title = title.strip()
            body = body.strip()
            if title and body and len(title) <= 24:
                blocks.append({"title": title, "body": body})
                continue
        blocks.append({"title": f"Point {index}", "body": clean_item})
    return blocks or [{"title": "Message", "body": fallback}]


def _asset_intent_from_assets(assets: list[DeckPageAsset]) -> str:
    """Summarize parsed visual intents for layout and exporter agents."""
    descriptions = _dedupe_text([asset.description for asset in assets if asset.description])
    return " ".join(descriptions[:2])


def _first_content_body(content_blocks: list[dict[str, Any]]) -> str:
    """Return the first non-empty content block body."""
    for block in content_blocks:
        body = str(block.get("body") or "").strip()
        if body:
            return body
    return ""


def _chapters_from_pages(pages: list[DeckPagePlan]) -> list[DeckChapter]:
    """Infer deck chapters from page-level chapter labels."""
    chapter_titles = _dedupe_text(
        [page.chapter for page in pages if page.chapter]
        or [page.title for page in pages if page.page_type == "chapter_start"]
        or [page.title for page in pages if page.page_type == "chapter_content"]
    )
    if not chapter_titles:
        chapter_titles = ["Main Story"]
    return [
        DeckChapter(
            title=title,
            purpose=f"Explain {title}.",
            order=index,
        )
        for index, title in enumerate(chapter_titles[:4], start=1)
    ]


def _normalize_page_type(value: str) -> str:
    """Normalize Markdown page type labels to supported deck page types."""
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "agenda": "toc",
        "contents": "toc",
        "section": "chapter_start",
        "section_divider": "chapter_start",
        "divider": "chapter_start",
        "content": "chapter_content",
        "main": "chapter_content",
        "summary": "ending",
        "closing": "ending",
        "review": "ending",
    }
    normalized = aliases.get(normalized, normalized)
    supported = {
        "cover",
        "toc",
        "chapter_start",
        "chapter_content",
        "ending",
        "quote",
        "stat",
        "kpi_grid",
        "comparison",
        "timeline",
        "roadmap",
        "process",
        "chart",
        "image_grid",
        "code",
        "appendix",
        "disclaimer",
    }
    return normalized if normalized in supported else "chapter_content"


def _value_after_colon(line: str) -> str:
    """Return text after the first English or Chinese colon."""
    for separator in (":", "："):
        if separator in line:
            return line.split(separator, 1)[1].strip()
    return line.strip()


def _should_use_kindergarten_english_plan(
    requirement: ConfirmedRequirement,
    understanding: SourceUnderstanding,
) -> bool:
    """Return whether the generic no-source fallback should become a word-card deck."""
    if understanding.markdown_sources or understanding.figures:
        return False
    text = " ".join(
        [
            requirement.topic,
            requirement.audience,
            requirement.scenario,
            " ".join(requirement.style_requirement.style_keywords),
        ]
    ).lower()
    has_child_audience = any(
        keyword in text
        for keyword in ("幼儿园", "小朋友", "儿童", "孩子", "kindergarten", "kid", "child", "children")
    )
    has_english_words = any(keyword in text for keyword in ("英语单词", "英文单词", "单词", "english word", "english words", "word"))
    return has_child_audience and has_english_words


def _build_kindergarten_english_word_markdown(requirement: ConfirmedRequirement) -> str:
    """Build a simple Markdown plan for kid-friendly English word teaching decks."""
    audience = requirement.audience or "幼儿园小朋友"
    return f"""# Deck: 开心学英语单词
Audience: {audience}
Language: zh-CN
SlideCount: 8
Narrative: 用可爱的图片和小游戏帮助小朋友认识常见英语单词。

## Slide 1 | cover | 开心学英语单词
Purpose: 用轻松可爱的方式开场。
Takeaway: 今天要用图片认识几个简单英语单词。
Content:
- 一起看图片、听单词、跟着读。
Visual:
- ai | role=hero | description=Colorful cute kindergarten classroom illustration with happy children learning English words, no text inside image

## Slide 2 | toc | 今天学什么
Purpose: 让小朋友知道今天会看到哪些词。
Takeaway: 我们会学水果、动物、玩具和自然里的单词。
Content:
- 水果：Apple
- 动物：Cat、Dog
- 玩具和物品：Ball、Book
- 自然：Sun
Visual:
- placeholder | role=word_card_grid | description=Six rounded word cards with simple picture spaces

## Slide 3 | chapter_start | 看图学单词
Purpose: 进入看图识词环节。
Takeaway: 看到图片时，先说中文，再试着说英文。
Content:
- 看一看：图片里是什么？
- 读一读：跟老师说英文。
Visual:
- ai | role=section_hero | description=Friendly illustrated flashcards on a classroom table, colorful simple shapes, no text inside image

## Slide 4 | chapter_content | Apple 苹果
Purpose: 学习第一个水果单词。
Takeaway: Apple 是苹果。
Content:
- Apple：苹果
- 读音练习：Ap-ple
- 小互动：找一找红色的苹果。
Visual:
- ai | role=word_picture | description=A single cute red apple with a smiling friendly style for kindergarten flashcard, plain light background, no text inside image

## Slide 5 | chapter_content | Cat 猫
Purpose: 学习一个动物单词。
Takeaway: Cat 是猫。
Content:
- Cat：猫
- 读音练习：Cat
- 小互动：学小猫轻轻叫。
Visual:
- ai | role=word_picture | description=A cute orange kitten sitting happily, simple colorful kindergarten flashcard style, plain light background, no text inside image

## Slide 6 | chapter_content | Dog 狗
Purpose: 学习另一个动物单词。
Takeaway: Dog 是狗。
Content:
- Dog：狗
- 读音练习：Dog
- 小互动：做一个小狗挥手动作。
Visual:
- ai | role=word_picture | description=A friendly puppy waving one paw, simple colorful kindergarten flashcard style, plain light background, no text inside image

## Slide 7 | chapter_content | Ball 球 / Book 书 / Sun 太阳
Purpose: 一页复习三个常见单词。
Takeaway: Ball 是球，Book 是书，Sun 是太阳。
Content:
- Ball：球
- Book：书
- Sun：太阳
- 小互动：老师说英文，小朋友指图片。
Visual:
- ai | role=word_picture_grid | description=Three cute simple objects: colorful ball, children's book, smiling sun, kindergarten flashcard grid, no text inside image

## Slide 8 | ending | 我说你指
Purpose: 用小游戏结束并复习。
Takeaway: 听到英文单词后，可以找到对应图片。
Content:
- 老师说：Apple / Cat / Dog / Ball / Book / Sun
- 小朋友指一指对应图片。
- 最后一起大声读一遍。
Visual:
- placeholder | role=review_game_board | description=Review game board with six picture spaces for teacher-led pointing game
"""


def _select_asset_source_preference(
    requirement: ConfirmedRequirement,
    understanding: SourceUnderstanding,
) -> str:
    """Select default visual sourcing for deterministic content planning."""
    if understanding.figures:
        return "user"
    if _requires_generated_visuals(requirement):
        return "ai"
    return "placeholder"


def _asset_source_preference_for_page(page_type: str, source_preference: str) -> str:
    """Limit generated visuals to pages where they add value."""
    if source_preference != "ai":
        return source_preference
    if page_type in {"cover", "chapter_start", "chapter_content"}:
        return "ai"
    return "placeholder"


def _requires_generated_visuals(requirement: ConfirmedRequirement) -> bool:
    """Return whether the request requires generated illustrations by default."""
    style_keywords = {keyword.lower() for keyword in requirement.style_requirement.style_keywords}
    return bool({"illustrated", "kid_friendly"} & style_keywords)


def _collect_ready_input_assets(requirement: ConfirmedRequirement) -> list[DeckPageAsset]:
    """Collect ready image assets from user inputs and prepared source figures."""
    assets: list[DeckPageAsset] = []
    for index, figure in enumerate(requirement.source_understanding.figures, start=1):
        path = str(figure.get("path") or "").strip()
        if not path or not _workspace_image_exists(path):
            continue
        assets.append(
            DeckPageAsset(
                asset_id=f"material_figure_{index}",
                source_kind="material_figure",
                status="ready",
                path=path,
                alt=str(figure.get("alt") or figure.get("source_name") or "Source figure"),
                description=str(figure.get("alt") or figure.get("source_name") or "Prepared source figure"),
            )
        )

    for index, source_input in enumerate(requirement.source_inputs, start=1):
        if not _looks_like_image_path(source_input.path) or not _workspace_image_exists(source_input.path):
            continue
        assets.append(
            DeckPageAsset(
                asset_id=f"user_input_image_{index}",
                source_kind="user_upload",
                status="ready",
                path=source_input.path,
                alt=source_input.name,
                description=source_input.description or source_input.name,
            )
        )

    for index, reference_asset in enumerate(requirement.reference_assets, start=1):
        if not _looks_like_image_path(reference_asset.path) or not _workspace_image_exists(reference_asset.path):
            continue
        assets.append(
            DeckPageAsset(
                asset_id=f"reference_image_{index}",
                source_kind="user_upload",
                status="ready",
                path=reference_asset.path,
                alt=reference_asset.name,
                description=reference_asset.description or reference_asset.name,
            )
        )
    return assets


def _next_ready_asset_for_page(
    ready_assets: list[DeckPageAsset],
    *,
    used_paths: set[str],
    page: DeckPagePlan,
) -> DeckPageAsset | None:
    """Assign one existing asset to a slide when the plan prefers user material."""
    if page.page_type in {"toc", "ending"}:
        return None
    if page.asset_source_preference not in {"user", "mixed"} and not page.asset_intent:
        return None
    for asset in ready_assets:
        if asset.path in used_paths:
            continue
        used_paths.add(asset.path)
        return asset.model_copy(
            update={
                "asset_id": f"slide_{page.slide_number:02d}_{asset.asset_id}",
                "role": (page.asset_roles or [asset.role])[0],
                "semantic_position": (page.asset_semantic_positions or [asset.semantic_position])[0],
            }
        )
    return None


def _build_pending_asset_request(
    page: DeckPagePlan,
    requirement: ConfirmedRequirement,
) -> DeckPageAsset | None:
    """Create a pending asset request from page-level visual intent."""
    if page.asset_source_preference not in {"search", "ai"}:
        return None
    source_kind = "search" if page.asset_source_preference == "search" else "image_generation"
    description = _build_asset_description(page, requirement)
    return DeckPageAsset(
        asset_id=f"slide_{page.slide_number:02d}_visual_1",
        role=(page.asset_roles or ["supporting_visual"])[0],
        semantic_position=(page.asset_semantic_positions or ["bottom_band"])[0],
        source_kind=source_kind,
        status="pending",
        description=description,
        alt=page.title,
        prompt=description if source_kind == "image_generation" else "",
        search_query=description if source_kind == "search" else "",
        aspect_ratio=requirement.aspect_ratio,
        resolution="1K",
    )


def _build_asset_description(page: DeckPagePlan, requirement: ConfirmedRequirement) -> str:
    """Build a concrete prompt/search description for one planned visual asset."""
    base = page.asset_intent or page.key_takeaway or page.title
    audience = requirement.audience or "the target audience"
    if _requires_generated_visuals(requirement):
        return (
            f"Colorful, simple, kid-friendly illustration for {audience}. "
            f"Slide title: {page.title}. Key message: {page.key_takeaway}. "
            "Use clear objects, friendly composition, no text inside the image."
        )
    return f"{base} Slide title: {page.title}. Key message: {page.key_takeaway}."


async def _call_asset_resolver(
    asset_resolver: Any,
    *,
    asset: DeckPageAsset,
    page: DeckPagePlan,
    requirement: ConfirmedRequirement,
) -> dict[str, Any] | DeckPageAsset | None:
    """Call an injected asset resolver used by tests or custom product integrations."""
    result = asset_resolver(asset, page, requirement)
    if inspect.isawaitable(result):
        result = await result
    if result is None:
        return None
    if isinstance(result, DeckPageAsset):
        return result
    if isinstance(result, dict):
        return result
    return None


def _merge_resolved_asset(
    original_asset: DeckPageAsset,
    resolved: dict[str, Any] | DeckPageAsset,
) -> DeckPageAsset:
    """Merge one resolver result into the original planned asset."""
    if isinstance(resolved, DeckPageAsset):
        payload = resolved.model_dump(mode="json")
    else:
        payload = dict(resolved)
    if payload.get("path") and not payload.get("status"):
        payload["status"] = "ready"
    merged = original_asset.model_dump(mode="json")
    merged.update(payload)
    return DeckPageAsset.model_validate(merged)


def _mark_asset_failed(asset: DeckPageAsset, warning: str) -> DeckPageAsset:
    """Mark an asset request as failed without blocking deck generation."""
    warnings = list(asset.warnings)
    clean_warning = str(warning or "").strip()
    if clean_warning:
        warnings.append(clean_warning)
    return asset.model_copy(update={"status": "failed", "warnings": warnings})


def _build_resolved_asset_manifest(plan: DeckContentPlan) -> dict[str, Any]:
    """Build the stage-3 manifest summarizing resolved slide assets."""
    assets = [
        {
            **asset.model_dump(mode="json"),
            "slide_number": page.slide_number,
            "slide_title": page.title,
        }
        for page in plan.pages
        for asset in page.assets
    ]
    return {
        "status": "success",
        "stage": "content_and_asset_planning",
        "asset_count": len(assets),
        "ready_asset_count": sum(1 for asset in assets if asset.get("status") == "ready"),
        "assets": assets,
    }


def _can_dispatch_expert(
    *,
    agent_name: str,
    tool_context: ToolContext,
    expert_agents: dict[str, BaseAgent],
    artifact_service: BaseArtifactService | None,
) -> bool:
    """Return whether an expert can be invoked through the ADK dispatcher."""
    return (
        agent_name in expert_agents
        and artifact_service is not None
        and hasattr(tool_context, "_invocation_context")
        and hasattr(tool_context.state, "to_dict")
    )


def _first_existing_output_path(output_files: list[dict[str, Any]]) -> str:
    """Return the first generated image path that exists in the workspace."""
    for file_info in output_files:
        path = str(file_info.get("path") or "").strip()
        if path and _workspace_image_exists(path):
            return path
    return ""


def _workspace_image_exists(path: str) -> bool:
    """Return whether a workspace image path exists and looks usable."""
    clean_path = str(path or "").strip()
    if not clean_path or clean_path.lower().startswith(("http://", "https://")):
        return False
    if not _looks_like_image_path(clean_path):
        return False
    try:
        return resolve_workspace_path(clean_path).exists()
    except Exception:
        return False


def _looks_like_image_path(path: str) -> bool:
    """Return whether a path has an image extension supported by the HTML route."""
    return str(path or "").lower().split("?", 1)[0].endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))


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
    *,
    source_texts: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Build generic planning anchors without pre-extracting source content."""
    source_points = _extract_source_key_points(source_texts or [])
    key_points = source_points + [
        requirement.topic,
        _source_basis_text(understanding),
        "The content planning agent should read prepared Markdown sources before finalizing slide content.",
    ]
    while len(key_points) < 3:
        key_points.append(key_points[-1])
    return key_points


def _extract_source_key_points(source_texts: list[dict[str, Any]]) -> list[str]:
    """Extract a few lightweight planning anchors from prepared Markdown text."""
    key_points: list[str] = []
    for source in source_texts:
        text = str(source.get("text") or "")
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                cleaned = line.lstrip("#").strip()
            elif line.startswith(("- ", "* ")):
                cleaned = line[2:].strip()
            elif re_match := _NUMBERED_LIST_RE.match(line):
                cleaned = re_match.group(1).strip()
            else:
                continue
            cleaned = " ".join(cleaned.split())
            if len(cleaned) >= 6:
                key_points.append(cleaned)
            if len(key_points) >= 6:
                return _dedupe_text(key_points)
    return _dedupe_text(key_points)


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


def _cover_asset_intent(
    requirement: ConfirmedRequirement,
    understanding: SourceUnderstanding,
) -> str:
    """Describe the preferred cover visual."""
    if _requires_generated_visuals(requirement):
        return f"A colorful, simple cover illustration for {requirement.audience or 'young learners'} about {requirement.topic}."
    return "A clean title composition anchored by the prepared source materials."


def _chapter_asset_intent(requirement: ConfirmedRequirement, chapter_title: str) -> str:
    """Describe the preferred chapter divider visual."""
    if _requires_generated_visuals(requirement):
        return f"A friendly chapter illustration for {requirement.audience or 'young learners'} about {chapter_title}."
    return "A section divider with one concise material-backed message."


def _asset_intent(
    requirement: ConfirmedRequirement,
    understanding: SourceUnderstanding,
) -> str:
    """Describe the preferred visual treatment for content pages."""
    if understanding.figures:
        return "Use the provided figures where they support the slide message."
    if _requires_generated_visuals(requirement):
        return f"A simple, colorful illustration that helps {requirement.audience or 'the audience'} understand {requirement.topic}."
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
    """Pick the artifact service for the internal content-planning runner."""
    required_methods = ("save_artifact", "load_artifact", "list_artifacts")
    if all(hasattr(tool_context, method_name) for method_name in required_methods):
        return ToolContextArtifactService(tool_context)
    return fallback_service


def _build_child_runner(
    *,
    agent: LlmAgent,
    app_name: str,
    session_service: InMemorySessionService,
    artifact_service: BaseArtifactService,
    invocation_context: Any,
) -> Runner:
    """Create a child ADK runner for the internal content-planning agent."""
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


def _append_planning_warning(state: Any, warning: str) -> None:
    """Append one planning warning to ADK state."""
    clean_warning = str(warning or "").strip()
    if not clean_warning:
        return
    warnings = list(state.get(PPT_CONTENT_PLANNING_WARNINGS_STATE_KEY) or [])
    warnings.append(clean_warning)
    state[PPT_CONTENT_PLANNING_WARNINGS_STATE_KEY] = warnings
