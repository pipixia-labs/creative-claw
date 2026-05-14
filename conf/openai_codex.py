"""OpenAI Codex OAuth model backend for Google ADK."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import httpx
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.models.lite_llm import _get_completion_inputs
from google.genai import types

DEFAULT_CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_ORIGINATOR = "creative-claw"


class OpenAICodexAuthError(RuntimeError):
    """Raised when the local Codex OAuth session is unavailable."""


@dataclass(slots=True)
class _CodexToolCall:
    """One completed Codex function call."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class _CodexAccumulator:
    """Incrementally accumulate one Codex Responses SSE stream."""

    model_version: str
    text: str = ""
    status: str = "completed"
    usage: dict[str, Any] | None = None
    tool_calls: list[_CodexToolCall] | None = None
    tool_call_buffers: dict[str, dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        self.tool_calls = []
        self.tool_call_buffers = {}

    def process_event(self, event: dict[str, Any], *, emit_partial: bool) -> LlmResponse | None:
        """Process one Responses API event and optionally return a partial response."""
        event_type = event.get("type")
        if event_type == "response.output_item.added":
            item = _as_dict(event.get("item") or {})
            if item.get("type") == "function_call":
                call_id = str(item.get("call_id") or "")
                if call_id:
                    self.tool_call_buffers[call_id] = {
                        "id": item.get("id") or "fc_0",
                        "name": item.get("name") or "",
                        "arguments": item.get("arguments") or "",
                    }
            return None

        if event_type == "response.output_text.delta":
            delta_text = str(event.get("delta") or "")
            if not delta_text:
                return None
            self.text += delta_text
            if not emit_partial:
                return None
            return LlmResponse(
                content=types.Content(role="model", parts=[types.Part.from_text(text=delta_text)]),
                partial=True,
                model_version=self.model_version,
            )

        if event_type == "response.function_call_arguments.delta":
            call_id = str(event.get("call_id") or "")
            if call_id and call_id in self.tool_call_buffers:
                self.tool_call_buffers[call_id]["arguments"] += str(event.get("delta") or "")
            return None

        if event_type == "response.function_call_arguments.done":
            call_id = str(event.get("call_id") or "")
            if call_id and call_id in self.tool_call_buffers:
                self.tool_call_buffers[call_id]["arguments"] = str(event.get("arguments") or "")
            return None

        if event_type == "response.output_item.done":
            item = _as_dict(event.get("item") or {})
            if item.get("type") == "function_call":
                self._append_tool_call(item)
            return None

        if event_type == "response.completed":
            response = _as_dict(event.get("response") or {})
            self.status = str(response.get("status") or "completed")
            usage = response.get("usage")
            self.usage = _as_dict(usage) if usage else None
            return None

        if event_type in {"error", "response.failed"}:
            detail = event.get("error") or event.get("message") or event
            raise RuntimeError(f"Codex response failed: {str(detail)[:500]}")

        return None

    def _append_tool_call(self, item: dict[str, Any]) -> None:
        """Append one completed function call item."""
        call_id = str(item.get("call_id") or "")
        if not call_id:
            return
        buffer = self.tool_call_buffers.get(call_id, {})
        item_id = str(buffer.get("id") or item.get("id") or "fc_0")
        args_raw = str(buffer.get("arguments") or item.get("arguments") or "{}")
        self.tool_calls.append(
            _CodexToolCall(
                id=f"{call_id}|{item_id}",
                name=str(buffer.get("name") or item.get("name") or ""),
                arguments=_parse_json_object(args_raw),
            )
        )

    def final_response(self) -> LlmResponse:
        """Build the final ADK LLM response for the completed Codex turn."""
        parts: list[types.Part] = []
        if self.text:
            parts.append(types.Part.from_text(text=self.text))
        for tool_call in self.tool_calls or []:
            part = types.Part.from_function_call(name=tool_call.name, args=tool_call.arguments)
            part.function_call.id = tool_call.id
            parts.append(part)

        response = LlmResponse(
            content=types.Content(role="model", parts=parts),
            partial=False,
            finish_reason=_map_finish_reason(self.status),
            model_version=self.model_version,
        )
        usage_metadata = _usage_metadata(self.usage)
        if usage_metadata is not None:
            response.usage_metadata = usage_metadata
        return response


class OpenAICodexLlm(BaseLlm):
    """ADK model backend that authenticates with OpenAI Codex OAuth."""

    api_base: str = DEFAULT_CODEX_RESPONSES_URL
    originator: str = DEFAULT_ORIGINATOR
    request_timeout_seconds: float = 60.0

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        """Generate one ADK model turn through the Codex Responses backend."""
        self._maybe_append_user_content(llm_request)
        effective_model = llm_request.model or self.model

        try:
            messages, tools, response_format, generation_params = await _get_completion_inputs(
                llm_request,
                effective_model,
            )
            body = _build_responses_body(
                messages=messages,
                tools=tools,
                response_format=response_format,
                generation_params=generation_params,
                model=effective_model,
            )
            token = await _get_codex_token()
            headers = _build_headers(
                account_id=str(getattr(token, "account_id", "") or ""),
                access_token=str(getattr(token, "access", "") or ""),
                originator=self.originator,
            )
            async for response in _request_codex(
                self.api_base,
                headers,
                body,
                timeout=self.request_timeout_seconds,
                emit_partial=stream,
                model_version=effective_model,
            ):
                yield response
        except Exception as exc:
            yield _error_response(exc, model_version=effective_model)

    @classmethod
    def supported_models(cls) -> list[str]:
        """Return model regexes recognized by ADK model registries."""
        return [r"openai_codex/.*", r"openai-codex/.*"]


def strip_openai_codex_model_prefix(model: str) -> str:
    """Return the bare Codex model id from a prefixed or bare model reference."""
    if model.startswith("openai_codex/") or model.startswith("openai-codex/"):
        return model.split("/", 1)[1]
    return model


async def _get_codex_token() -> Any:
    """Load the local Codex OAuth token from oauth-cli-kit."""
    try:
        from oauth_cli_kit import get_token
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise OpenAICodexAuthError(
            "OpenAI Codex OAuth support requires oauth-cli-kit. "
            "Install project dependencies, then run `creative-claw provider login openai-codex`."
        ) from exc

    token = await asyncio.to_thread(get_token)
    if not token or not getattr(token, "access", None):
        raise OpenAICodexAuthError(
            "OpenAI Codex OAuth login is missing. Run `creative-claw provider login openai-codex`."
        )
    if not getattr(token, "account_id", None):
        raise OpenAICodexAuthError("OpenAI Codex OAuth token is missing an account id.")
    return token


def _build_headers(account_id: str, access_token: str, *, originator: str) -> dict[str, str]:
    """Build HTTP headers expected by the Codex Responses endpoint."""
    return {
        "Authorization": f"Bearer {access_token}",
        "chatgpt-account-id": account_id,
        "OpenAI-Beta": "responses=experimental",
        "originator": originator,
        "User-Agent": "creative-claw (python)",
        "accept": "text/event-stream",
        "content-type": "application/json",
    }


def _build_responses_body(
    *,
    messages: list[Any],
    tools: list[dict[str, Any]] | None,
    response_format: dict[str, Any] | None,
    generation_params: dict[str, Any] | None,
    model: str,
) -> dict[str, Any]:
    """Build a Codex Responses request body from ADK/LiteLLM inputs."""
    system_prompt, input_items = _convert_messages(messages)
    converted_tools = _convert_tools(tools or [])
    body: dict[str, Any] = {
        "model": strip_openai_codex_model_prefix(model),
        "store": False,
        "stream": True,
        "instructions": system_prompt,
        "input": input_items,
        "text": _responses_text_config(response_format),
        "include": ["reasoning.encrypted_content"],
        "prompt_cache_key": _prompt_cache_key(messages),
    }
    if converted_tools:
        body["tools"] = converted_tools
        body["tool_choice"] = "auto"
        body["parallel_tool_calls"] = True
    if generation_params and generation_params.get("max_completion_tokens") is not None:
        body["max_output_tokens"] = generation_params["max_completion_tokens"]
    return body


async def _request_codex(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    timeout: float,
    emit_partial: bool,
    model_version: str,
) -> AsyncGenerator[LlmResponse, None]:
    """Request Codex once, retrying without certificate verification if necessary."""
    try:
        async for response in _request_codex_once(
            url,
            headers,
            body,
            timeout=timeout,
            verify=True,
            emit_partial=emit_partial,
            model_version=model_version,
        ):
            yield response
    except Exception as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
            raise
        async for response in _request_codex_once(
            url,
            headers,
            body,
            timeout=timeout,
            verify=False,
            emit_partial=emit_partial,
            model_version=model_version,
        ):
            yield response


async def _request_codex_once(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    timeout: float,
    verify: bool,
    emit_partial: bool,
    model_version: str,
) -> AsyncGenerator[LlmResponse, None]:
    """Stream one Codex Responses request into ADK responses."""
    async with httpx.AsyncClient(timeout=timeout, verify=verify) as client:
        async with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code != 200:
                raw = await response.aread()
                raise RuntimeError(_friendly_http_error(response.status_code, raw.decode("utf-8", "ignore")))

            accumulator = _CodexAccumulator(model_version=model_version)
            async for event in _iter_sse(response):
                partial = accumulator.process_event(event, emit_partial=emit_partial)
                if partial is not None:
                    yield partial
            yield accumulator.final_response()


async def _iter_sse(response: httpx.Response) -> AsyncGenerator[dict[str, Any], None]:
    """Yield parsed JSON payloads from a Server-Sent Events response."""
    buffer: list[str] = []

    async for line in response.aiter_lines():
        if line == "":
            event = _flush_sse_buffer(buffer)
            if event is not None:
                yield event
            continue
        buffer.append(line)

    event = _flush_sse_buffer(buffer)
    if event is not None:
        yield event


def _flush_sse_buffer(buffer: list[str]) -> dict[str, Any] | None:
    """Flush one SSE event buffer into a parsed JSON payload."""
    if not buffer:
        return None
    data_lines = [line[5:].strip() for line in buffer if line.startswith("data:")]
    buffer.clear()
    if not data_lines:
        return None
    data = "\n".join(data_lines).strip()
    if not data or data == "[DONE]":
        return None
    return json.loads(data)


def _convert_messages(messages: list[Any]) -> tuple[str, list[dict[str, Any]]]:
    """Convert OpenAI chat messages into Responses API input items."""
    system_prompts: list[str] = []
    input_items: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        role = _get(message, "role")
        content = _get(message, "content")
        if role == "system":
            if isinstance(content, str) and content:
                system_prompts.append(content)
            continue
        if role == "user":
            input_items.append(_convert_user_content(content))
            continue
        if role == "assistant":
            if isinstance(content, str) and content:
                input_items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": content}],
                        "status": "completed",
                        "id": f"msg_{index}",
                    }
                )
            for tool_call in _get(message, "tool_calls", []) or []:
                function = _get(tool_call, "function", {})
                call_id, item_id = _split_tool_call_id(_get(tool_call, "id"))
                input_items.append(
                    {
                        "type": "function_call",
                        "id": item_id or f"fc_{index}",
                        "call_id": call_id or f"call_{index}",
                        "name": _get(function, "name"),
                        "arguments": _get(function, "arguments") or "{}",
                    }
                )
            continue
        if role == "tool":
            call_id, _ = _split_tool_call_id(_get(message, "tool_call_id"))
            output_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
            input_items.append({"type": "function_call_output", "call_id": call_id, "output": output_text})
    return "\n\n".join(system_prompts), input_items


def _convert_user_content(content: Any) -> dict[str, Any]:
    """Convert one user chat message content value to Responses input format."""
    if isinstance(content, str):
        return {"role": "user", "content": [{"type": "input_text", "text": content}]}
    if isinstance(content, list):
        converted: list[dict[str, Any]] = []
        for item in content:
            item = _as_dict(item)
            item_type = item.get("type")
            if item_type in {"text", "input_text"}:
                converted.append({"type": "input_text", "text": str(item.get("text") or "")})
            elif item_type in {"image_url", "input_image"}:
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    image_url = image_url.get("url")
                if image_url:
                    converted.append({"type": "input_image", "image_url": str(image_url), "detail": "auto"})
        if converted:
            return {"role": "user", "content": converted}
    return {"role": "user", "content": [{"type": "input_text", "text": ""}]}


def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI chat function tools to Responses API tools."""
    converted: list[dict[str, Any]] = []
    for tool in tools:
        function = _get(tool, "function", {}) if _get(tool, "type") == "function" else tool
        name = _get(function, "name")
        if not name:
            continue
        parameters = _get(function, "parameters", {})
        converted.append(
            {
                "type": "function",
                "name": name,
                "description": _get(function, "description") or "",
                "parameters": parameters if isinstance(parameters, dict) else {},
            }
        )
    return converted


def _responses_text_config(response_format: dict[str, Any] | None) -> dict[str, Any]:
    """Return the Responses API text config, including structured output if present."""
    text_config: dict[str, Any] = {"verbosity": "medium"}
    if not response_format:
        return text_config
    if response_format.get("type") == "json_schema":
        json_schema = _as_dict(response_format.get("json_schema") or {})
        text_config["format"] = {
            "type": "json_schema",
            "name": json_schema.get("name") or "response",
            "schema": json_schema.get("schema") or {},
            "strict": bool(json_schema.get("strict", True)),
        }
    elif response_format.get("type") == "json_object":
        text_config["format"] = {"type": "json_object"}
    return text_config


def _split_tool_call_id(tool_call_id: Any) -> tuple[str, str | None]:
    """Split a compound `call_id|item_id` value."""
    if isinstance(tool_call_id, str) and tool_call_id:
        if "|" in tool_call_id:
            call_id, item_id = tool_call_id.split("|", 1)
            return call_id, item_id or None
        return tool_call_id, None
    return "call_0", None


def _prompt_cache_key(messages: list[Any]) -> str:
    """Return a stable prompt cache key for the outgoing message list."""
    raw = json.dumps(_jsonable(messages), ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object, falling back to a raw wrapper for malformed arguments."""
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"raw": raw}


def _usage_metadata(usage: dict[str, Any] | None) -> types.GenerateContentResponseUsageMetadata | None:
    """Map Responses API usage fields to ADK usage metadata."""
    if not usage:
        return None
    prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or prompt_tokens + output_tokens)
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt_tokens,
        candidates_token_count=output_tokens,
        total_token_count=total_tokens,
    )


def _map_finish_reason(status: str | None) -> types.FinishReason:
    """Map a Responses status to a Google GenAI finish reason."""
    if status == "incomplete":
        return types.FinishReason.MAX_TOKENS
    if status in {"failed", "cancelled"}:
        return types.FinishReason.OTHER
    return types.FinishReason.STOP


def _error_response(exc: Exception, *, model_version: str) -> LlmResponse:
    """Build one ADK error response from an exception."""
    return LlmResponse(
        error_code="openai_codex_error",
        error_message=str(exc),
        finish_reason=types.FinishReason.OTHER,
        model_version=model_version,
        partial=False,
    )


def _friendly_http_error(status_code: int, raw: str) -> str:
    """Return a readable Codex backend error."""
    if status_code == 429:
        return "ChatGPT usage quota exceeded or Codex rate limit triggered. Please try again later."
    return f"Codex HTTP {status_code}: {raw[:1000]}"


def _get(value: Any, key: str, default: Any = None) -> Any:
    """Read a field from either a mapping-like object or an SDK object."""
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _as_dict(value: Any) -> dict[str, Any]:
    """Return a shallow dict representation for SDK objects."""
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(exclude_none=True)
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _jsonable(value: Any) -> Any:
    """Convert SDK objects recursively into JSON-friendly values."""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return _as_dict(value) or str(value)
