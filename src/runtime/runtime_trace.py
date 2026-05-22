"""Environment-gated runtime communication tracing for Creative Claw."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from src.logger import logger

RUNTIME_TRACE_ENV_VAR = "CREATIVE_CLAW_RUNTIME_TRACE"
RUNTIME_TRACE_RAW_EVENTS_ENV_VAR = "CREATIVE_CLAW_RUNTIME_TRACE_RAW_EVENTS"
RUNTIME_TRACE_STREAM_DELTAS_ENV_VAR = "CREATIVE_CLAW_RUNTIME_TRACE_STREAM_DELTAS"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_SENSITIVE_KEYS = {
    "api_key",
    "access_key",
    "secret_key",
    "access_token",
    "refresh_token",
    "id_token",
    "session_token",
    "bot_token",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
}
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "access_token",
    "refresh_token",
    "id_token",
    "session_token",
    "bot_token",
    "secret_key",
)
_MAX_OBJECT_DEPTH = 8


def runtime_trace_enabled() -> bool:
    """Return whether verbose runtime communication tracing is enabled."""
    return _env_flag_enabled(RUNTIME_TRACE_ENV_VAR)


def runtime_trace_raw_events_enabled() -> bool:
    """Return whether raw ADK runner events should be traced."""
    return _env_flag_enabled(RUNTIME_TRACE_RAW_EVENTS_ENV_VAR)


def runtime_trace_stream_deltas_enabled() -> bool:
    """Return whether partial streaming model responses should be traced."""
    return _env_flag_enabled(RUNTIME_TRACE_STREAM_DELTAS_ENV_VAR)


def trace_runtime_event(event_type: str, payload: Any | None = None) -> bool:
    """Print one runtime trace event to the backend logger when tracing is enabled."""
    if not runtime_trace_enabled():
        return False
    rendered_payload = serialize_trace_payload(payload if payload is not None else {})
    logger.info("[runtime-trace] {} {}", str(event_type or "event").strip() or "event", rendered_payload)
    return True


def serialize_trace_payload(payload: Any) -> str:
    """Render one trace payload as redacted JSON text without content truncation."""
    safe_payload = _to_trace_safe_value(payload)
    try:
        rendered = json.dumps(safe_payload, ensure_ascii=False, indent=2, sort_keys=True)
    except TypeError:
        rendered = str(safe_payload)
    return rendered


class CreativeClawRuntimeTracePlugin(BasePlugin):
    """ADK plugin that logs runtime communication content for local debugging."""

    def __init__(self) -> None:
        """Initialize the runtime trace plugin."""
        super().__init__(name="creative_claw_runtime_trace")

    async def on_user_message_callback(self, *, invocation_context, user_message) -> None:
        """Trace the user message entering one ADK invocation."""
        trace_runtime_event(
            "user_message",
            {
                "invocation": _invocation_context_summary(invocation_context),
                "message": user_message,
            },
        )
        return None

    async def before_run_callback(self, *, invocation_context) -> None:
        """Trace the beginning of one ADK runner invocation."""
        trace_runtime_event("run.start", _invocation_context_summary(invocation_context))
        return None

    async def after_run_callback(self, *, invocation_context) -> None:
        """Trace the end of one ADK runner invocation."""
        trace_runtime_event("run.finish", _invocation_context_summary(invocation_context))
        return None

    async def on_event_callback(self, *, invocation_context, event) -> None:
        """Trace raw ADK events emitted by the runner when explicitly enabled."""
        if not runtime_trace_raw_events_enabled():
            return None
        trace_runtime_event(
            "runner.event",
            {
                "invocation": _invocation_context_summary(invocation_context),
                "event": event,
            },
        )
        return None

    async def before_agent_callback(self, *, agent, callback_context) -> None:
        """Trace agent entry."""
        trace_runtime_event(
            "agent.start",
            {
                "agent": _agent_summary(agent),
                "callback": _callback_context_summary(callback_context),
            },
        )
        return None

    async def after_agent_callback(self, *, agent, callback_context) -> None:
        """Trace agent exit."""
        trace_runtime_event(
            "agent.finish",
            {
                "agent": _agent_summary(agent),
                "callback": _callback_context_summary(callback_context),
            },
        )
        return None

    async def before_model_callback(self, *, callback_context, llm_request) -> None:
        """Trace model request content before it is sent."""
        trace_runtime_event(
            "model.request",
            {
                "callback": _callback_context_summary(callback_context),
                "request": llm_request,
            },
        )
        return None

    async def after_model_callback(self, *, callback_context, llm_response) -> None:
        """Trace model response content after it is received."""
        if _is_partial_model_response(llm_response) and not runtime_trace_stream_deltas_enabled():
            return None
        trace_runtime_event(
            "model.response",
            {
                "callback": _callback_context_summary(callback_context),
                "response": _model_response_trace_payload(llm_response),
            },
        )
        return None

    async def on_model_error_callback(self, *, callback_context, llm_request, error) -> None:
        """Trace model errors without swallowing them."""
        trace_runtime_event(
            "model.error",
            {
                "callback": _callback_context_summary(callback_context),
                "request": llm_request,
                "error": f"{type(error).__name__}: {error}",
            },
        )
        return None

    async def before_tool_callback(self, *, tool, tool_args, tool_context) -> None:
        """Trace tool call arguments before execution."""
        trace_runtime_event(
            "tool.start",
            {
                "tool": _tool_summary(tool),
                "tool_context": _tool_context_summary(tool_context),
                "args": tool_args,
            },
        )
        return None

    async def after_tool_callback(self, *, tool, tool_args, tool_context, result) -> None:
        """Trace tool result after execution."""
        trace_runtime_event(
            "tool.finish",
            {
                "tool": _tool_summary(tool),
                "tool_context": _tool_context_summary(tool_context),
                "args": tool_args,
                "result": result,
            },
        )
        return None

    async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error) -> None:
        """Trace tool errors without swallowing them."""
        trace_runtime_event(
            "tool.error",
            {
                "tool": _tool_summary(tool),
                "tool_context": _tool_context_summary(tool_context),
                "args": tool_args,
                "error": f"{type(error).__name__}: {error}",
            },
        )
        return None


def _env_flag_enabled(name: str) -> bool:
    """Return whether an environment flag is set to a supported true value."""
    return os.getenv(name, "").strip().lower() in _TRUE_VALUES


def _is_partial_model_response(llm_response: Any) -> bool:
    """Return whether an ADK model response is a streaming partial chunk."""
    if isinstance(llm_response, Mapping):
        return llm_response.get("partial") is True
    return getattr(llm_response, "partial", None) is True


def _to_trace_safe_value(value: Any, *, depth: int = 0) -> Any:
    """Convert an arbitrary ADK object into bounded JSON-safe trace data."""
    if depth > _MAX_OBJECT_DEPTH:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            clean_key = str(key)
            if _is_sensitive_key(clean_key):
                result[clean_key] = "[REDACTED]"
            else:
                result[clean_key] = _to_trace_safe_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        return [_to_trace_safe_value(item, depth=depth + 1) for item in items]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _to_trace_safe_value(model_dump(mode="json", exclude_none=True), depth=depth + 1)
        except Exception:
            pass

    model_dump_json = getattr(value, "model_dump_json", None)
    if callable(model_dump_json):
        try:
            return _to_trace_safe_value(json.loads(model_dump_json(exclude_none=True)), depth=depth + 1)
        except Exception:
            pass

    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        public_attributes = {
            key: item
            for key, item in attributes.items()
            if not str(key).startswith("_")
        }
        if public_attributes:
            return _to_trace_safe_value(public_attributes, depth=depth + 1)

    return str(value)


def _model_response_trace_payload(llm_response: Any) -> Any:
    """Return a model response trace payload with content parts aggregated."""
    response = _to_trace_safe_value(llm_response)
    if not isinstance(response, Mapping):
        return response

    result = dict(response)
    if "content" in result:
        result["content"] = _model_content_trace_payload(result["content"])
    return result


def _model_content_trace_payload(content: Any) -> Any:
    """Return trace-safe model content without exposing raw part fragments."""
    if not isinstance(content, Mapping):
        return content

    result = {key: value for key, value in content.items() if key != "parts"}
    parts = content.get("parts")
    if not isinstance(parts, list):
        return result

    text_segments: list[str] = []
    thought_segments: list[str] = []
    function_calls: list[Any] = []
    function_responses: list[Any] = []
    other_content: list[dict[str, Any]] = []

    for part in parts:
        if not isinstance(part, Mapping):
            other_content.append({"value": part})
            continue

        text = part.get("text")
        if isinstance(text, str):
            if part.get("thought") is True:
                thought_segments.append(text)
            else:
                text_segments.append(text)

        if part.get("function_call") is not None:
            function_calls.append(part["function_call"])
        if part.get("function_response") is not None:
            function_responses.append(part["function_response"])

        non_text_content = {
            key: value
            for key, value in part.items()
            if key not in {"text", "thought", "function_call", "function_response"}
        }
        if non_text_content:
            other_content.append(non_text_content)

    if text_segments:
        result["text"] = "".join(text_segments)
    if thought_segments:
        result["thought_text"] = "".join(thought_segments)
    if function_calls:
        result["function_calls"] = function_calls
    if function_responses:
        result["function_responses"] = function_responses
    if other_content:
        result["other_content"] = other_content
    return result


def _is_sensitive_key(key: str) -> bool:
    """Return whether a dictionary key should be redacted in trace logs."""
    normalized = str(key or "").strip().lower()
    return normalized in _SENSITIVE_KEYS or any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _invocation_context_summary(invocation_context: Any) -> dict[str, Any]:
    """Return stable identifiers from an ADK invocation context."""
    session = getattr(invocation_context, "session", None)
    agent = getattr(invocation_context, "agent", None)
    return {
        "app_name": getattr(invocation_context, "app_name", ""),
        "user_id": getattr(invocation_context, "user_id", ""),
        "session_id": getattr(session, "id", getattr(invocation_context, "session_id", "")),
        "agent_name": getattr(agent, "name", ""),
        "invocation_id": getattr(invocation_context, "invocation_id", ""),
    }


def _callback_context_summary(callback_context: Any) -> dict[str, Any]:
    """Return stable identifiers from an ADK callback context."""
    state = getattr(callback_context, "state", None)
    state_keys: list[str] = []
    try:
        state_keys = sorted(str(key) for key in list(state.keys()))
    except Exception:
        state_keys = []
    return {
        "agent_name": getattr(callback_context, "agent_name", ""),
        "invocation_id": getattr(callback_context, "invocation_id", ""),
        "state_keys": state_keys,
    }


def _agent_summary(agent: Any) -> dict[str, Any]:
    """Return compact agent metadata."""
    return {
        "name": getattr(agent, "name", ""),
        "class": type(agent).__name__,
    }


def _tool_summary(tool: Any) -> dict[str, Any]:
    """Return compact tool metadata."""
    return {
        "name": getattr(tool, "name", ""),
        "class": type(tool).__name__,
    }


def _tool_context_summary(tool_context: Any) -> dict[str, Any]:
    """Return stable identifiers from a tool context."""
    session = getattr(tool_context, "session", None)
    state = getattr(session, "state", None) or getattr(tool_context, "state", None)
    return {
        "session_id": getattr(session, "id", ""),
        "invocation_id": getattr(tool_context, "invocation_id", ""),
        "turn_index": _state_get(state, "turn_index"),
        "step": _state_get(state, "step"),
        "expert_step": _state_get(state, "expert_step"),
    }


def _state_get(state: Any, key: str) -> Any:
    """Read one key from dict-like state without assuming its concrete type."""
    try:
        return state.get(key)
    except Exception:
        return None


__all__ = [
    "CreativeClawRuntimeTracePlugin",
    "RUNTIME_TRACE_ENV_VAR",
    "RUNTIME_TRACE_RAW_EVENTS_ENV_VAR",
    "RUNTIME_TRACE_STREAM_DELTAS_ENV_VAR",
    "runtime_trace_enabled",
    "runtime_trace_raw_events_enabled",
    "runtime_trace_stream_deltas_enabled",
    "serialize_trace_payload",
    "trace_runtime_event",
]
