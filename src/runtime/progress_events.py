"""Structured progress-copy helpers for user-facing activity updates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProgressCopy:
    """User-facing title and detail for one progress stage."""

    title: str
    detail: str


DEFAULT_PROGRESS_COPY = ProgressCopy(
    title="Working on your request",
    detail="The system is working on your request.",
)

STAGE_PROGRESS_COPY: dict[str, ProgressCopy] = {
    "started": ProgressCopy("Preparing your request", "The system is getting ready to work on your request."),
    "attachment_received": ProgressCopy("Reading attachments", "The system is preparing the files you shared."),
    "in_progress": DEFAULT_PROGRESS_COPY,
    "orchestrating": DEFAULT_PROGRESS_COPY,
    "planning": ProgressCopy("Planning next steps", "The system is deciding what to do next."),
    "inspection": ProgressCopy("Checking context", "The system is reviewing relevant context and files."),
    "editing": ProgressCopy("Updating files", "The system is making the requested changes."),
    "image_processing": ProgressCopy("Processing images", "The system is working with image assets."),
    "video_processing": ProgressCopy("Processing video", "The system is working with video assets."),
    "audio_processing": ProgressCopy("Processing audio", "The system is working with audio assets."),
    "execution": ProgressCopy("Running a task", "The system is running a local task for this request."),
    "research": ProgressCopy("Researching", "The system is gathering relevant information."),
    "design_planning": ProgressCopy("Preparing the design", "The system is reviewing the design request."),
    "expert_execution": ProgressCopy("Generating content", "The system is using a specialist capability for this step."),
    "finalizing": ProgressCopy("Preparing results", "The system is organizing the final response."),
    "completed": ProgressCopy("Completed", "The task is complete."),
    "cancelled": ProgressCopy("Cancelled", "The task was stopped."),
    "failed": ProgressCopy("Failed", "The task failed."),
    "ppt_product_planning": ProgressCopy("Preparing the presentation", "The system is planning the presentation workflow."),
    "ppt_product_confirmation": ProgressCopy(
        "Updating the presentation plan",
        "The system is continuing the presentation workflow.",
    ),
    "page_planning": ProgressCopy("Preparing the page", "The system is planning the page workflow."),
    "page_product": ProgressCopy("Creating the page", "The system is working on the page deliverable."),
    "design_product": ProgressCopy("Creating the design", "The system is working on the design deliverable."),
    "template_selection": ProgressCopy("Choosing a layout", "The system is selecting a suitable layout approach."),
    "content_draft": ProgressCopy("Drafting content", "The system is drafting the page content."),
    "material_preparation": ProgressCopy("Preparing assets", "The system is preparing supporting materials."),
    "final_draft": ProgressCopy("Preparing the final brief", "The system is assembling the final generation brief."),
    "html_generation": ProgressCopy("Generating the page", "The system is generating the final HTML output."),
}

TOOL_PROGRESS_COPY: dict[str, ProgressCopy] = {
    "list_session_files": ProgressCopy(
        "Checking context",
        "The system is reviewing this conversation's files and previous outputs.",
    ),
    "list_skills": ProgressCopy("Checking capabilities", "The system is checking available capabilities."),
    "read_skill": ProgressCopy("Reading guidance", "The system is reading the relevant workflow guidance."),
    "list_dir": ProgressCopy("Checking files", "The system is reviewing relevant workspace files."),
    "glob": ProgressCopy("Finding files", "The system is finding relevant workspace files."),
    "grep": ProgressCopy("Searching files", "The system is searching relevant workspace content."),
    "read_file": ProgressCopy("Reading context", "The system is reading relevant workspace content."),
    "write_file": ProgressCopy("Writing files", "The system is creating or updating files."),
    "edit_file": ProgressCopy("Updating files", "The system is applying file changes."),
    "exec_command": ProgressCopy("Running a task", "The system is running a local task for this request."),
    "process_session": ProgressCopy("Checking task output", "The system is checking a local task result."),
    "web_search": ProgressCopy("Researching", "The system is searching for relevant information."),
    "web_fetch": ProgressCopy("Reading reference material", "The system is reading relevant reference material."),
    "invoke_agent": ProgressCopy("Generating content", "The system is using a specialist capability."),
    "run_ppt_product": ProgressCopy("Creating the presentation", "The system is working on the presentation."),
    "continue_ppt_product": ProgressCopy(
        "Continuing the presentation",
        "The system is continuing the presentation workflow.",
    ),
    "run_page_product": ProgressCopy("Creating the page", "The system is working on the page deliverable."),
    "run_design_product": ProgressCopy("Creating the design", "The system is working on the design deliverable."),
}


def resolve_user_progress(
    *,
    stage: str,
    debug_title: str = "",
    user_title: str | None = None,
    user_detail: str | None = None,
) -> ProgressCopy:
    """Return user-facing progress copy while keeping debug text out of the UI."""
    explicit_title = str(user_title or "").strip()
    explicit_detail = str(user_detail or "").strip()
    if explicit_title or explicit_detail:
        fallback = _progress_copy_for(stage=stage, debug_title=debug_title)
        return ProgressCopy(
            title=explicit_title or fallback.title,
            detail=explicit_detail or fallback.detail,
        )
    return _progress_copy_for(stage=stage, debug_title=debug_title)


def build_progress_metadata(
    *,
    session_id: str,
    stage: str,
    debug_title: str = "",
    debug_detail: str = "",
    user_title: str | None = None,
    user_detail: str | None = None,
    turn_index: int | None = None,
    debug_events: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build metadata for one progress event with separated user/debug fields."""
    normalized_stage = str(stage or "in_progress").strip() or "in_progress"
    normalized_debug_title = str(debug_title or "").strip()
    normalized_debug_detail = str(debug_detail or "").strip()
    copy = resolve_user_progress(
        stage=normalized_stage,
        debug_title=normalized_debug_title,
        user_title=user_title,
        user_detail=user_detail,
    )
    metadata: dict[str, Any] = {
        "session_id": str(session_id or "").strip(),
        "display_style": "progress",
        "stage": normalized_stage,
        "stage_title": copy.title,
        "user_title": copy.title,
        "user_detail": copy.detail,
        "debug_title": normalized_debug_title,
        "debug_detail": normalized_debug_detail,
    }
    if debug_events:
        metadata["debug_events"] = list(debug_events)
    if turn_index is not None:
        metadata["turn_index"] = turn_index
    return metadata


def progress_text_from_metadata(metadata: dict[str, Any]) -> str:
    """Return the user-facing text body for a progress event."""
    return str(metadata.get("user_detail") or "").strip() or DEFAULT_PROGRESS_COPY.detail


def _progress_copy_for(*, stage: str, debug_title: str) -> ProgressCopy:
    tool_key = _tool_progress_key(debug_title)
    tool_copy = TOOL_PROGRESS_COPY.get(tool_key)
    if tool_copy is not None:
        return tool_copy
    stage_copy = STAGE_PROGRESS_COPY.get(str(stage or "").strip())
    if stage_copy is not None:
        return stage_copy
    return DEFAULT_PROGRESS_COPY


def _tool_progress_key(debug_title: str) -> str:
    """Normalize a tool/debug title so display copy is not case-format dependent."""
    normalized = "".join(
        char.lower() if char.isalnum() else "_"
        for char in str(debug_title or "").strip()
    )
    return "_".join(part for part in normalized.split("_") if part)


__all__ = [
    "ProgressCopy",
    "build_progress_metadata",
    "progress_text_from_metadata",
    "resolve_user_progress",
]
