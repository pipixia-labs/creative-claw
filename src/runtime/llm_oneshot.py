"""ADK-native helpers for one-shot private LLM calls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models import LlmRequest
from google.genai.types import Content, Part


@dataclass(slots=True)
class OneShotLlmResult:
    """Collected visible text and inline data from one private LLM call."""

    texts: list[str]
    final_texts: list[str]
    inline_data: list[bytes]

    @property
    def text(self) -> str:
        """Return the last visible text emitted by the model."""
        return self.texts[-1] if self.texts else ""

    @property
    def final_text(self) -> str:
        """Return the last visible final-response text emitted by the model."""
        return self.final_texts[-1] if self.final_texts else ""

    @property
    def image_data(self) -> bytes | None:
        """Return the last inline binary payload emitted by the model."""
        return self.inline_data[-1] if self.inline_data else None


async def run_oneshot_llm(
    ctx: InvocationContext,
    *,
    name: str,
    model: Any,
    instruction: str,
    user_text: str = "",
    user_parts: list[Part] | None = None,
    include_contents: str = "none",
    generate_content_config: Any | None = None,
    agent_cls: Callable[..., Any] = LlmAgent,
) -> OneShotLlmResult:
    """Run a temporary ADK LlmAgent and collect visible model outputs.

    This helper intentionally stays inside ADK instead of calling provider
    clients directly, so existing runtime context, plugins, callbacks, and
    usage accounting remain available to the one-shot call.
    """
    parts = list(user_parts or [])
    if user_text:
        parts.insert(0, Part(text=user_text))

    def before_model_callback(
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> None:
        """Inject this one-shot request into the private model call."""
        del callback_context
        llm_request.contents.append(Content(role="user", parts=parts))

    agent_kwargs: dict[str, Any] = {
        "name": name,
        "model": model,
        "instruction": instruction,
        "include_contents": include_contents,
        "before_model_callback": before_model_callback,
    }
    if generate_content_config is not None:
        agent_kwargs["generate_content_config"] = generate_content_config

    llm = agent_cls(**agent_kwargs)
    texts: list[str] = []
    final_texts: list[str] = []
    inline_data: list[bytes] = []

    async for event in llm.run_async(ctx):
        if not getattr(event, "content", None) or not event.content.parts:
            continue
        event_texts: list[str] = []
        for part in event.content.parts:
            if getattr(part, "thought", False):
                continue
            raw_text = getattr(part, "text", None)
            text = str(raw_text) if raw_text is not None else ""
            if text.strip():
                texts.append(text)
                event_texts.append(text)
            if getattr(part, "inline_data", None) is not None:
                inline_data.append(part.inline_data.data)
        if event_texts and event.is_final_response():
            final_texts.append("\n".join(event_texts))

    return OneShotLlmResult(
        texts=texts,
        final_texts=final_texts,
        inline_data=inline_data,
    )
