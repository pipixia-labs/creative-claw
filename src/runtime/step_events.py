"""Realtime step-event publishing for tool lifecycle callbacks."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable

from google.adk.plugins.base_plugin import BasePlugin

from src.channels.events import OutboundMessage
from src.runtime.interaction_language import INTERACTION_LANGUAGE_STATE_KEY
from src.runtime.progress_events import build_progress_metadata, progress_text_from_metadata
from src.runtime.tool_context import get_route
from src.runtime.tool_display import format_tool_args, summarize_tool_result

_STEP_EVENT_PUBLISHER: Callable[[OutboundMessage], Awaitable[None] | None] | None = None
_HISTORY_BY_SESSION: dict[str, list[dict[str, str]]] = {}
ASSISTANT_DELTA_KIND_THINKING_PLACEHOLDER = "thinking_placeholder"
_BUILTIN_TOOL_STAGES = {
    "list_dir": "inspection",
    "read_file": "inspection",
    "write_file": "editing",
    "edit_file": "editing",
    "image_crop": "image_processing",
    "image_rotate": "image_processing",
    "image_flip": "image_processing",
    "image_info": "image_processing",
    "image_resize": "image_processing",
    "image_convert": "image_processing",
    "video_info": "video_processing",
    "video_extract_frame": "video_processing",
    "video_trim": "video_processing",
    "video_concat": "video_processing",
    "video_convert": "video_processing",
    "audio_info": "audio_processing",
    "audio_trim": "audio_processing",
    "audio_concat": "audio_processing",
    "audio_convert": "audio_processing",
    "exec_command": "execution",
    "web_search": "research",
    "web_fetch": "research",
}


def configure_step_event_publisher(
    publisher: Callable[[OutboundMessage], Awaitable[None] | None] | None,
) -> None:
    """Configure the async publisher used by realtime step events."""
    global _STEP_EVENT_PUBLISHER
    _STEP_EVENT_PUBLISHER = publisher


def step_event_publisher_configured() -> bool:
    """Return whether realtime step publishing is currently enabled."""
    return _STEP_EVENT_PUBLISHER is not None


def step_event_streaming_active() -> bool:
    """Return whether realtime step publishing is active for the current route."""
    channel, chat_id = get_route()
    return _STEP_EVENT_PUBLISHER is not None and bool(channel) and bool(chat_id)


def assistant_delta_streaming_active() -> bool:
    """Return whether assistant text deltas can be consumed by the Web UI."""
    channel, chat_id = get_route()
    return _STEP_EVENT_PUBLISHER is not None and channel == "web" and bool(chat_id)


def _debug_history(history: list[dict[str, str]], limit: int = 8) -> list[dict[str, str]]:
    """Return recent raw tool events for trace/debug consumers."""
    return list(history[-limit:])


def _normalize_turn_index(turn_index: Any) -> int | None:
    """Return a positive turn index when one is available."""
    try:
        normalized = int(turn_index or 0)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _session_history_key(
    channel: str,
    chat_id: str,
    session_id: str,
    turn_index: int | None = None,
) -> str:
    """Build the in-memory history key for one channel turn."""
    turn = _normalize_turn_index(turn_index)
    if turn is None:
        return f"{channel}:{chat_id}:{session_id}"
    return f"{channel}:{chat_id}:{session_id}:turn:{turn}"


def _build_detail(*, status: str, args: dict[str, Any], result_text: str | None = None) -> str:
    """Build the detail body shown in the progress card."""
    lines = [f"Status: {status}", f"Args: {format_tool_args(args)}"]
    if result_text:
        lines.append(f"Result: {result_text}")
    return "\n".join(lines)


def _resolve_tool_turn_index(tool_context: Any) -> int | None:
    """Extract the current turn index from an ADK tool context when present."""
    session = getattr(tool_context, "session", None)
    state = getattr(session, "state", None)
    if not isinstance(state, dict):
        return None
    return _normalize_turn_index(state.get("turn_index"))


def _resolve_tool_interaction_language(tool_context: Any) -> str:
    """Extract the current interaction language from an ADK tool context when present."""
    session = getattr(tool_context, "session", None)
    state = getattr(session, "state", None)
    if not isinstance(state, dict):
        return ""
    return str(state.get(INTERACTION_LANGUAGE_STATE_KEY) or "")


async def _publish_step_event(
    *,
    session_id: str,
    turn_index: int | None = None,
    tool_name: str,
    stage: str,
    detail: str,
    user_title: str | None = None,
    user_detail: str | None = None,
    interaction_language: str = "",
) -> None:
    """Publish one realtime tool progress event with user/debug fields separated."""
    publisher = _STEP_EVENT_PUBLISHER
    channel, chat_id = get_route()
    if publisher is None or not channel or not chat_id:
        return

    normalized_turn = _normalize_turn_index(turn_index)
    key = _session_history_key(channel, chat_id, session_id, normalized_turn)
    history = _HISTORY_BY_SESSION.setdefault(key, [])
    history.append({"title": tool_name, "detail": detail, "stage": stage})
    metadata = build_progress_metadata(
        session_id=session_id,
        stage=stage,
        debug_title=tool_name,
        debug_detail=detail,
        user_title=user_title,
        user_detail=user_detail,
        turn_index=normalized_turn,
        debug_events=_debug_history(history),
        activity_sequence=len(history),
        interaction_language=interaction_language,
    )

    maybe_awaitable = publisher(
        OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            text=progress_text_from_metadata(metadata),
            metadata=metadata,
        )
    )
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


async def publish_assistant_delta(
    *,
    session_id: str,
    delta: str,
    turn_index: int | None = None,
    delta_kind: str | None = None,
) -> bool:
    """Publish one realtime Web assistant text delta through the configured publisher."""
    publisher = _STEP_EVENT_PUBLISHER
    normalized_delta = str(delta or "")
    if not assistant_delta_streaming_active() or not normalized_delta:
        return False
    channel, chat_id = get_route()

    metadata: dict[str, Any] = {
        "session_id": session_id,
        "display_style": "assistant_delta",
    }
    normalized_turn = _normalize_turn_index(turn_index)
    if normalized_turn is not None:
        metadata["turn_index"] = normalized_turn
    normalized_delta_kind = str(delta_kind or "").strip()
    if normalized_delta_kind:
        metadata["assistant_delta_kind"] = normalized_delta_kind

    maybe_awaitable = publisher(
        OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            text=normalized_delta,
            metadata=metadata,
        )
    )
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable
    return True


def reset_step_event_history(*, session_id: str, turn_index: int | None = None) -> None:
    """Reset the in-memory realtime history for the current routed turn."""
    channel, chat_id = get_route()
    if not channel or not chat_id or not session_id:
        return
    _HISTORY_BY_SESSION[_session_history_key(channel, chat_id, session_id, turn_index)] = []


def publish_orchestration_step_event(
    *,
    session_id: str,
    turn_index: int | None = None,
    title: str,
    detail: str,
    stage: str,
    user_title: str | None = None,
    user_detail: str | None = None,
    interaction_language: str = "",
) -> None:
    """Schedule one realtime publish for an orchestrator-level step event."""
    if not step_event_streaming_active():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        _publish_step_event(
            session_id=session_id,
            turn_index=turn_index,
            tool_name=title,
            stage=stage,
            detail=detail,
            user_title=user_title,
            user_detail=user_detail,
            interaction_language=interaction_language,
        )
    )


def append_orchestration_step_event(
    state: Any,
    *,
    title: str,
    detail: str,
    stage: str = "orchestrating",
    session_id: str = "",
    user_title: str | None = None,
    user_detail: str | None = None,
) -> None:
    """Append one progress event to session state and publish it when realtime streaming is active."""
    normalized_title = str(title or "").strip() or "In Progress"
    normalized_detail = str(detail or "").strip() or "Processing the current step."
    normalized_stage = str(stage or "").strip() or "orchestrating"
    events = list(state.get("orchestration_events", []) or [])
    metadata = build_progress_metadata(
        session_id=str(session_id or "").strip() or str(state.get("sid", "") or "").strip(),
        stage=normalized_stage,
        debug_title=normalized_title,
        debug_detail=normalized_detail,
        user_title=user_title,
        user_detail=user_detail,
        turn_index=_normalize_turn_index(state.get("turn_index")),
        activity_sequence=len(events) + 1,
        interaction_language=str(state.get(INTERACTION_LANGUAGE_STATE_KEY) or ""),
    )
    events.append(
        {
            "title": normalized_title,
            "detail": normalized_detail,
            "stage": normalized_stage,
            "user_title": str(metadata.get("user_title") or ""),
            "user_detail": str(metadata.get("user_detail") or ""),
            "debug_title": normalized_title,
            "debug_detail": normalized_detail,
            "activity_group_id": str(metadata.get("activity_group_id") or ""),
            "activity_sequence": metadata.get("activity_sequence"),
        }
    )
    state["orchestration_events"] = events
    resolved_session_id = str(session_id or "").strip() or str(state.get("sid", "") or "").strip()
    if not resolved_session_id:
        return
    publish_orchestration_step_event(
        session_id=resolved_session_id,
        turn_index=_normalize_turn_index(state.get("turn_index")),
        title=normalized_title,
        detail=normalized_detail,
        stage=normalized_stage,
        user_title=str(metadata.get("user_title") or ""),
        user_detail=str(metadata.get("user_detail") or ""),
        interaction_language=str(state.get(INTERACTION_LANGUAGE_STATE_KEY) or ""),
    )


class CreativeClawStepEventPlugin(BasePlugin):
    """Publish builtin tool lifecycle events in realtime during ADK execution."""

    def __init__(self) -> None:
        super().__init__(name="creative_claw_step_events")

    async def before_run_callback(self, *, invocation_context) -> None:
        """Initialize one empty realtime history per invocation."""
        return None

    async def after_run_callback(self, *, invocation_context) -> None:
        """Release one invocation history after the runner finishes."""
        return None

    async def before_tool_callback(
        self,
        *,
        tool,
        tool_args: dict[str, Any],
        tool_context,
    ) -> None:
        """Publish one realtime start event before builtin tool execution."""
        stage = _BUILTIN_TOOL_STAGES.get(tool.name)
        if stage is None or not step_event_streaming_active():
            return None
        await _publish_step_event(
            session_id=tool_context.session.id,
            turn_index=_resolve_tool_turn_index(tool_context),
            tool_name=tool.name,
            stage=stage,
            detail=_build_detail(status="started", args=tool_args),
            interaction_language=_resolve_tool_interaction_language(tool_context),
        )
        return None

    async def after_tool_callback(
        self,
        *,
        tool,
        tool_args: dict[str, Any],
        tool_context,
        result: Any,
    ) -> None:
        """Publish one realtime completion event after builtin tool execution."""
        stage = _BUILTIN_TOOL_STAGES.get(tool.name)
        if stage is None or not step_event_streaming_active():
            return None
        status, summary = summarize_tool_result(tool.name, result)
        await _publish_step_event(
            session_id=tool_context.session.id,
            turn_index=_resolve_tool_turn_index(tool_context),
            tool_name=tool.name,
            stage=stage,
            detail=_build_detail(
                status="success" if status == "success" else "error",
                args=tool_args,
                result_text=summary,
            ),
            interaction_language=_resolve_tool_interaction_language(tool_context),
        )
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool,
        tool_args: dict[str, Any],
        tool_context,
        error: Exception,
    ) -> None:
        """Publish one realtime error event when builtin tool execution fails."""
        stage = _BUILTIN_TOOL_STAGES.get(tool.name)
        if stage is None or not step_event_streaming_active():
            return None
        await _publish_step_event(
            session_id=tool_context.session.id,
            turn_index=_resolve_tool_turn_index(tool_context),
            tool_name=tool.name,
            stage=stage,
            detail=_build_detail(status="error", args=tool_args, result_text=str(error).strip()),
            interaction_language=_resolve_tool_interaction_language(tool_context),
        )
        return None
