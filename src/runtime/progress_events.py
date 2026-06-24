"""Structured progress-copy helpers for user-facing activity updates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.runtime.interaction_language import LANGUAGE_ZH, normalize_interaction_language


@dataclass(frozen=True)
class ProgressCopy:
    """User-facing title and detail for one progress stage."""

    title: str
    detail: str


DEFAULT_PROGRESS_COPY = ProgressCopy(
    title="Working on your request",
    detail="The system is working on your request.",
)
DEFAULT_PROGRESS_COPY_ZH = ProgressCopy(
    title="正在处理你的请求",
    detail="系统正在处理你的请求。",
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

STAGE_PROGRESS_COPY_ZH: dict[str, ProgressCopy] = {
    "started": ProgressCopy("正在准备请求", "系统正在准备处理你的请求。"),
    "attachment_received": ProgressCopy("正在读取附件", "系统正在准备你提供的文件。"),
    "in_progress": DEFAULT_PROGRESS_COPY_ZH,
    "orchestrating": DEFAULT_PROGRESS_COPY_ZH,
    "planning": ProgressCopy("正在规划下一步", "系统正在判断接下来要做什么。"),
    "inspection": ProgressCopy("正在检查上下文", "系统正在查看相关上下文和文件。"),
    "editing": ProgressCopy("正在更新文件", "系统正在进行你要求的修改。"),
    "image_processing": ProgressCopy("正在处理图像", "系统正在处理图像素材。"),
    "video_processing": ProgressCopy("正在处理视频", "系统正在处理视频素材。"),
    "audio_processing": ProgressCopy("正在处理音频", "系统正在处理音频素材。"),
    "execution": ProgressCopy("正在执行任务", "系统正在为这个请求执行本地任务。"),
    "research": ProgressCopy("正在检索资料", "系统正在收集相关信息。"),
    "design_planning": ProgressCopy("正在准备设计", "系统正在分析设计需求。"),
    "expert_execution": ProgressCopy("正在生成内容", "系统正在使用专门能力处理这一步。"),
    "finalizing": ProgressCopy("正在整理结果", "系统正在整理最终回复。"),
    "completed": ProgressCopy("已完成", "任务已完成。"),
    "cancelled": ProgressCopy("已取消", "任务已停止。"),
    "failed": ProgressCopy("失败", "任务执行失败。"),
    "ppt_product_planning": ProgressCopy("正在准备演示文稿", "系统正在规划演示文稿流程。"),
    "ppt_product_confirmation": ProgressCopy(
        "正在更新演示文稿方案",
        "系统正在继续处理演示文稿流程。",
    ),
    "page_planning": ProgressCopy("正在准备页面", "系统正在规划页面流程。"),
    "page_product": ProgressCopy("正在创建页面", "系统正在处理页面交付物。"),
    "design_product": ProgressCopy("正在创建设计", "系统正在处理设计交付物。"),
    "template_selection": ProgressCopy("正在选择版式", "系统正在选择合适的版式方案。"),
    "content_draft": ProgressCopy("正在起草内容", "系统正在起草页面内容。"),
    "material_preparation": ProgressCopy("正在准备素材", "系统正在准备辅助素材。"),
    "final_draft": ProgressCopy("正在准备最终简报", "系统正在整理最终生成简报。"),
    "html_generation": ProgressCopy("正在生成页面", "系统正在生成最终 HTML 输出。"),
}

TOOL_PROGRESS_COPY_ZH: dict[str, ProgressCopy] = {
    "list_session_files": ProgressCopy(
        "正在检查上下文",
        "系统正在查看本次对话的文件和之前的输出。",
    ),
    "list_skills": ProgressCopy("正在检查能力", "系统正在检查可用能力。"),
    "read_skill": ProgressCopy("正在读取指引", "系统正在读取相关工作流指引。"),
    "list_dir": ProgressCopy("正在检查文件", "系统正在查看相关工作区文件。"),
    "glob": ProgressCopy("正在查找文件", "系统正在查找相关工作区文件。"),
    "grep": ProgressCopy("正在搜索文件", "系统正在搜索相关工作区内容。"),
    "read_file": ProgressCopy("正在读取上下文", "系统正在读取相关工作区内容。"),
    "write_file": ProgressCopy("正在写入文件", "系统正在创建或更新文件。"),
    "edit_file": ProgressCopy("正在更新文件", "系统正在应用文件改动。"),
    "exec_command": ProgressCopy("正在执行任务", "系统正在为这个请求执行本地任务。"),
    "process_session": ProgressCopy("正在检查任务输出", "系统正在检查本地任务结果。"),
    "web_search": ProgressCopy("正在检索资料", "系统正在搜索相关信息。"),
    "web_fetch": ProgressCopy("正在读取参考资料", "系统正在读取相关参考资料。"),
    "invoke_agent": ProgressCopy("正在生成内容", "系统正在使用专门能力。"),
    "run_ppt_product": ProgressCopy("正在创建演示文稿", "系统正在处理演示文稿。"),
    "continue_ppt_product": ProgressCopy(
        "正在继续演示文稿",
        "系统正在继续处理演示文稿流程。",
    ),
    "run_page_product": ProgressCopy("正在创建页面", "系统正在处理页面交付物。"),
    "run_design_product": ProgressCopy("正在创建设计", "系统正在处理设计交付物。"),
}


def resolve_user_progress(
    *,
    stage: str,
    debug_title: str = "",
    user_title: str | None = None,
    user_detail: str | None = None,
    interaction_language: str = "",
) -> ProgressCopy:
    """Return user-facing progress copy while keeping debug text out of the UI."""
    explicit_title = str(user_title or "").strip()
    explicit_detail = str(user_detail or "").strip()
    if explicit_title or explicit_detail:
        fallback = _progress_copy_for(
            stage=stage,
            debug_title=debug_title,
            interaction_language=interaction_language,
        )
        return ProgressCopy(
            title=explicit_title or fallback.title,
            detail=explicit_detail or fallback.detail,
        )
    return _progress_copy_for(
        stage=stage,
        debug_title=debug_title,
        interaction_language=interaction_language,
    )


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
    activity_group_id: str | None = None,
    activity_sequence: int | None = None,
    interaction_language: str = "",
) -> dict[str, Any]:
    """Build metadata for one progress event with separated user/debug fields."""
    normalized_session_id = str(session_id or "").strip()
    normalized_stage = str(stage or "in_progress").strip() or "in_progress"
    normalized_debug_title = str(debug_title or "").strip()
    normalized_debug_detail = str(debug_detail or "").strip()
    copy = resolve_user_progress(
        stage=normalized_stage,
        debug_title=normalized_debug_title,
        user_title=user_title,
        user_detail=user_detail,
        interaction_language=interaction_language,
    )
    metadata: dict[str, Any] = {
        "session_id": normalized_session_id,
        "display_style": "progress",
        "stage": normalized_stage,
        "stage_title": copy.title,
        "user_title": copy.title,
        "user_detail": copy.detail,
        "debug_title": normalized_debug_title,
        "debug_detail": normalized_debug_detail,
        "activity_group_id": _resolve_activity_group_id(
            session_id=normalized_session_id,
            turn_index=turn_index,
            activity_group_id=activity_group_id,
        ),
        "activity_status": _activity_status_for_stage(normalized_stage),
    }
    if debug_events:
        metadata["debug_events"] = list(debug_events)
    if turn_index is not None:
        metadata["turn_index"] = turn_index
    if activity_sequence is not None:
        metadata["activity_sequence"] = activity_sequence
    return metadata


def progress_text_from_metadata(metadata: dict[str, Any]) -> str:
    """Return the user-facing text body for a progress event."""
    return str(metadata.get("user_detail") or "").strip() or DEFAULT_PROGRESS_COPY.detail


def _progress_copy_for(*, stage: str, debug_title: str, interaction_language: str = "") -> ProgressCopy:
    use_zh = normalize_interaction_language(interaction_language, fallback="") == LANGUAGE_ZH
    tool_copy_map = TOOL_PROGRESS_COPY_ZH if use_zh else TOOL_PROGRESS_COPY
    stage_copy_map = STAGE_PROGRESS_COPY_ZH if use_zh else STAGE_PROGRESS_COPY
    default_copy = DEFAULT_PROGRESS_COPY_ZH if use_zh else DEFAULT_PROGRESS_COPY
    tool_key = _tool_progress_key(debug_title)
    tool_copy = tool_copy_map.get(tool_key)
    if tool_copy is not None:
        return tool_copy
    stage_copy = stage_copy_map.get(str(stage or "").strip())
    if stage_copy is not None:
        return stage_copy
    return default_copy


def _resolve_activity_group_id(
    *,
    session_id: str,
    turn_index: int | None,
    activity_group_id: str | None,
) -> str:
    """Return the stable user-facing Activity group id for one request turn."""
    explicit = str(activity_group_id or "").strip()
    if explicit:
        return explicit
    if session_id and turn_index is not None:
        return f"{session_id}:turn:{turn_index}"
    return session_id


def _activity_status_for_stage(stage: str) -> str:
    """Return the broad lifecycle status represented by a progress stage."""
    normalized = str(stage or "").strip().lower()
    if normalized in {"completed", "cancelled", "failed"}:
        return normalized
    return "running"


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
