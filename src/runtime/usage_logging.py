"""LLM usage logging plugin for Creative Claw runtimes."""

from __future__ import annotations

from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

from src.logger import logger

LLM_USAGE_HISTORY_STATE_KEY = "llm_usage_history"
LLM_USAGE_TOTALS_STATE_KEY = "llm_usage_totals"
_HISTORY_LIMIT = 200


class CreativeClawUsageLoggingPlugin(BasePlugin):
    """Log and aggregate ADK model usage metadata for each LLM response."""

    def __init__(self) -> None:
        """Initialize the usage logging plugin."""
        super().__init__(name="creative_claw_usage_logging")

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        """Record token usage after one model response when metadata exists."""
        usage = extract_usage_metadata(llm_response)
        if not usage:
            return None

        entry = {
            "agent_name": str(callback_context.agent_name or ""),
            "model_version": str(getattr(llm_response, "model_version", "") or ""),
            "usage": usage,
        }
        _append_usage_entry(callback_context.state, entry)
        logger.info(
            "llm usage: agent={} model={} prompt_tokens={} output_tokens={} total_tokens={} cached_tokens={} thoughts_tokens={} tool_prompt_tokens={}",
            entry["agent_name"],
            entry["model_version"],
            usage.get("prompt_token_count", 0),
            usage.get("candidates_token_count", 0),
            usage.get("total_token_count", 0),
            usage.get("cached_content_token_count", 0),
            usage.get("thoughts_token_count", 0),
            usage.get("tool_use_prompt_token_count", 0),
        )
        return None


def extract_usage_metadata(response_or_event: Any) -> dict[str, int]:
    """Extract a compact token-usage dictionary from an ADK response or event."""
    usage = getattr(response_or_event, "usage_metadata", None)
    if usage is None:
        return {}

    if isinstance(usage, dict):
        payload = dict(usage)
    elif hasattr(usage, "model_dump"):
        payload = usage.model_dump(mode="json", exclude_none=True)
    else:
        payload = {
            field: getattr(usage, field, None)
            for field in _USAGE_FIELDS
        }

    compact: dict[str, int] = {}
    for field in _USAGE_FIELDS:
        value = payload.get(field)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            compact[field] = int(value)
    return compact


def _append_usage_entry(state: Any, entry: dict[str, Any]) -> None:
    """Append one usage entry and update aggregate counters in ADK state."""
    try:
        history = list(state.get(LLM_USAGE_HISTORY_STATE_KEY) or [])
        history.append(entry)
        state[LLM_USAGE_HISTORY_STATE_KEY] = history[-_HISTORY_LIMIT:]

        totals = dict(state.get(LLM_USAGE_TOTALS_STATE_KEY) or {})
        for key, value in dict(entry.get("usage") or {}).items():
            totals[key] = int(totals.get(key, 0) or 0) + int(value)
        state[LLM_USAGE_TOTALS_STATE_KEY] = totals
    except Exception as exc:  # pragma: no cover - defensive logging only
        logger.warning("failed to append llm usage metadata: {}", exc)


_USAGE_FIELDS = (
    "prompt_token_count",
    "candidates_token_count",
    "total_token_count",
    "cached_content_token_count",
    "thoughts_token_count",
    "tool_use_prompt_token_count",
)


__all__ = [
    "CreativeClawUsageLoggingPlugin",
    "LLM_USAGE_HISTORY_STATE_KEY",
    "LLM_USAGE_TOTALS_STATE_KEY",
    "extract_usage_metadata",
]
