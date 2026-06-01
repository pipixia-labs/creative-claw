"""Planning-oriented orchestrator runtime for Creative Claw."""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Iterator, Optional

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.apps import App, ResumabilityConfig
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.adk.models import LlmRequest
from google.genai.types import Content, FunctionResponse, Part

from conf.agent import EXPERTS_LIST
from conf.llm import build_llm, resolve_llm_model_name, resolve_structured_output_mode
from conf.system import SYS_CONFIG
from src.agents.experts.video_generation.capabilities import build_video_generation_routing_notes
from src.agents.orchestrator.final_response import (
    ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY,
    OrchestratorFinalResponse,
)
from src.logger import logger
from src.productions.design.design_product_manager import DesignProductManager
from src.productions.design.design_product_manager.brief_form import (
    DESIGN_BRIEF_FORM_ANSWERS_STATE_KEY,
    DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY,
)
from src.productions.page.page_product_manager import PageProductManager
from src.productions.ppt.ppt_product_manager import PptProductManager
from src.productions.ppt.schemas import PptAdkConfirmationResponse
from src.runtime.expert_dispatcher import ExpertInvocationRequest, dispatch_expert_request
from src.runtime.expert_registry import build_expert_contract_summary
from src.runtime.product_results import (
    is_completed_product_result,
    is_completed_page_product_result,
    is_product_confirmation_result,
    is_terminal_product_result,
    slim_product_result,
)
from src.runtime.product_protocol import ProductToolRequest
from src.runtime.step_events import (
    ASSISTANT_DELTA_KIND_THINKING_PLACEHOLDER,
    CreativeClawStepEventPlugin,
    append_orchestration_step_event,
    assistant_delta_streaming_active,
    publish_assistant_delta,
    step_event_streaming_active,
)
from src.runtime.cancellation import get_cancellation_manager
from src.runtime.runtime_trace import CreativeClawRuntimeTracePlugin
from src.runtime.tool_display import format_tool_args, stringify_value, summarize_tool_result
from src.runtime.usage_logging import CreativeClawUsageLoggingPlugin
from src.runtime.workspace import (
    build_workspace_file_record,
    relocate_generated_output,
    resolve_workspace_path,
    workspace_relative_path,
)
from src.skills import get_skill_registry
from src.tools.builtin_tools import (
    BuiltinToolbox,
    builtin_tool_scope,
)

_PLUGIN_MANAGED_TOOL_NAMES = {
    "list_dir",
    "glob",
    "grep",
    "read_file",
    "write_file",
    "edit_file",
    "image_crop",
    "image_rotate",
    "image_flip",
    "image_info",
    "image_resize",
    "image_convert",
    "video_info",
    "video_extract_frame",
    "video_trim",
    "video_concat",
    "video_convert",
    "audio_info",
    "audio_trim",
    "audio_concat",
    "audio_convert",
    "exec_command",
    "process_session",
    "web_search",
    "web_fetch",
}
ADK_REQUEST_CONFIRMATION_FUNCTION_NAME = "adk_request_confirmation"
PPT_ADK_HITL_ENABLED_STATE_KEY = "ppt_adk_hitl_enabled"
PPT_ADK_PENDING_CONFIRMATION_STATE_KEY = "ppt_adk_pending_confirmation"

_AUTO_OUTPUT_TOOL_NAMES = {
    "image_crop",
    "image_rotate",
    "image_flip",
    "image_resize",
    "image_convert",
    "video_extract_frame",
    "video_trim",
    "video_concat",
    "video_convert",
    "audio_trim",
    "audio_concat",
    "audio_convert",
}

_DEEPSEEK_THINKING_PLACEHOLDER = "thinking ..."

_DISPLAY_TOOL_TITLES = {
    "list_skills": "List Skills",
    "read_skill": "Read Skill",
    "list_session_files": "List Session Files",
    "run_ppt_product": "Run PPT Product",
    "continue_ppt_product": "Continue PPT Product",
    "run_page_product": "Run Page Product",
    "run_design_product": "Run Design Product",
}

_WorkspaceFileSnapshot = dict[str, tuple[int, int]]


def _format_file_summary(file_info: dict[str, Any], *, index: int) -> str:
    """Render one compact workspace file summary."""
    file_name = str(file_info.get("name", "")).strip() or f"file_{index}"
    file_path = str(file_info.get("path", "")).strip()
    file_description = str(file_info.get("description", "")).strip()
    metadata_parts = [f"name={file_name}", f"path={file_path}"]
    if file_description:
        metadata_parts.append(f"description={file_description}")
    turn = file_info.get("turn")
    step = file_info.get("step")
    expert_step = file_info.get("expert_step")
    if turn is not None:
        metadata_parts.append(f"turn={turn}")
    if step is not None:
        metadata_parts.append(f"step={step}")
    if expert_step is not None:
        metadata_parts.append(f"expert_step={expert_step}")
    return f"file {index}: {'; '.join(metadata_parts)}"


def _format_turn_file_history(label: str, history: list[dict[str, Any]]) -> list[str]:
    """Render one turn-grouped file history section."""
    if not history:
        return []
    rendered = [label]
    for entry in history:
        turn = int(entry.get("turn", 0) or 0)
        files = list(entry.get("files") or [])
        if not files:
            rendered.append(f"- Turn {turn}: no files")
            continue
        rendered.append(
            f"- Turn {turn}: {' | '.join(_format_file_summary(file_info, index=index) for index, file_info in enumerate(files, start=1))}"
        )
    return rendered


def _latest_generated_files(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the latest generated file batch from current-turn or historical state."""
    final_files = _file_records_for_paths(list(state.get("final_file_paths") or []), state=state)
    if final_files:
        return final_files
    new_files = _non_channel_files(list(state.get("new_files") or []))
    if new_files:
        return new_files
    generated = list(state.get("generated") or [])
    if generated:
        return generated
    generated_history = list(state.get("generated_history") or [])
    for entry in reversed(generated_history):
        files = list(entry.get("files") or [])
        if files:
            return files
    files_history = list(state.get("files_history") or state.get("artifacts_history") or [])
    return _select_latest_non_channel_files(files_history)


def _non_channel_files(file_group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return generated/supporting files while excluding current-turn uploads."""
    return [
        file_info
        for file_info in file_group
        if isinstance(file_info, dict) and str(file_info.get("source", "")).strip() != "channel"
    ]


def _file_records_for_paths(paths: list[str], *, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return current-session file records matching explicit workspace-relative paths."""
    if not paths:
        return []

    records_by_path: dict[str, dict[str, Any]] = {}

    def _normalize_path(path: str) -> str:
        try:
            return workspace_relative_path(path)
        except Exception:
            return str(path or "").strip()

    def _index(file_group: list[dict[str, Any]]) -> None:
        for file_info in file_group:
            if not isinstance(file_info, dict):
                continue
            path = str(file_info.get("path", "") or "").strip()
            if path:
                records_by_path.setdefault(_normalize_path(path), file_info)

    _index(list(state.get("new_files") or []))
    _index(list(state.get("generated") or []))
    _index(list(state.get("uploaded") or state.get("input_files") or []))
    for file_group in list(state.get("files_history") or state.get("artifacts_history") or []):
        if isinstance(file_group, list):
            _index(list(file_group))
    for entry in list(state.get("generated_history") or []):
        if isinstance(entry, dict):
            _index(list(entry.get("files") or []))

    matched: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for path in paths:
        if not isinstance(path, str):
            continue
        normalized_path = _normalize_path(path)
        if not normalized_path or normalized_path in seen_paths:
            continue
        record = records_by_path.get(normalized_path)
        if record:
            matched.append(record)
            seen_paths.add(normalized_path)
    return matched


async def orchestrator_before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> None:
    """Inject compact runtime state and recent workspace files into the model request."""
    state = callback_context.state
    turn_index = state.get("turn_index", 0)
    step = state.get("step", 0)
    expert_step = state.get("expert_step", 0)
    workflow_status = state.get("workflow_status", "running")
    delivery_channel = str(state.get("channel", "")).strip()
    delivery_chat_id = str(state.get("chat_id", "")).strip()
    delivery_sender_id = str(state.get("sender_id", "")).strip()
    product_line = str(state.get("product_line", "") or "").strip()
    product_line_options = state.get("product_line_options") or {}
    ppt_workflow_state = state.get("ppt_workflow_state") or {}
    design_brief_pending_task = str(state.get(DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY) or "").strip()
    uploaded = list(state.get("uploaded") or state.get("input_files") or state.get("input_artifacts") or [])
    uploaded_history = list(state.get("uploaded_history") or [])
    generated = list(state.get("generated") or [])
    generated_history = list(state.get("generated_history") or [])

    summary_lines = [
        f"# Workflow status: {workflow_status}",
        f"# Current turn: {turn_index}",
        f"# Executed actions: {step}",
        f"# Expert calls: {expert_step}",
        f"# User task:\n{state.get('user_prompt', '')}",
    ]

    if delivery_channel:
        delivery_parts = [f"channel={delivery_channel}"]
        if delivery_chat_id:
            delivery_parts.append(f"chat_id={delivery_chat_id}")
        if delivery_sender_id:
            delivery_parts.append(f"sender_id={delivery_sender_id}")
        summary_lines.append(f"# Delivery context: {'; '.join(delivery_parts)}")

    if product_line:
        summary_lines.append(f"# Product line: {product_line}")
        if isinstance(product_line_options, dict) and product_line_options:
            summary_lines.append(
                "# Product line options:\n"
                f"{json.dumps(product_line_options, ensure_ascii=False, indent=2)}"
            )

    if isinstance(ppt_workflow_state, dict) and ppt_workflow_state.get("stage"):
        pending_summary = {
            "workflow_id": ppt_workflow_state.get("workflow_id", ""),
            "stage": ppt_workflow_state.get("stage", ""),
            "revision": ppt_workflow_state.get("revision", 0),
        }
        summary_lines.append(
            "# Active PPT workflow:\n"
            f"{json.dumps(pending_summary, ensure_ascii=False, indent=2)}"
        )

    if design_brief_pending_task:
        pending_summary = {
            "stage": "awaiting_brief_form_answers",
            "pending_task": design_brief_pending_task,
        }
        summary_lines.append(
            "# Active Design brief form:\n"
            f"{json.dumps(pending_summary, ensure_ascii=False, indent=2)}"
        )

    if uploaded:
        summary_lines.append("# Uploaded files in current turn:")
        summary_lines.extend(
            f"- {_format_file_summary(file_info, index=index)}"
            for index, file_info in enumerate(uploaded, start=1)
        )

    summary_lines.extend(_format_turn_file_history("# Uploaded file history by turn:", uploaded_history))

    if generated:
        summary_lines.append("# Generated files in current turn:")
        summary_lines.extend(
            f"- {_format_file_summary(file_info, index=index)}"
            for index, file_info in enumerate(generated, start=1)
        )

    summary_lines.extend(_format_turn_file_history("# Generated file history by turn:", generated_history))

    summary_history = state.get("summary_history", [])
    message_history = state.get("message_history", [])
    if summary_history and message_history:
        summary_lines.append("# Execution history:")
        for index, (summary, message) in enumerate(zip(summary_history, message_history), start=1):
            summary_lines.append(f"- Step {index}: target={summary}; result={message}")

    latest_file_group = _latest_generated_files(state)
    if latest_file_group:
        latest_paths = ", ".join(
            str(file_info.get("path", "")).strip()
            for file_info in latest_file_group
            if str(file_info.get("path", "")).strip()
        )
        if latest_paths:
            summary_lines.append(f"# Most recent available output files: {latest_paths}")

    summary_lines.extend(
        [
            "# Final response contract:",
            "- When the task is complete, return the final structured response with `reply_text` and `final_file_paths`.",
            "- `reply_text` must contain the complete user-facing reply in the user's language.",
            "- `final_file_paths` must contain exact workspace-relative paths from the current session, or be `[]` when no attachments are needed.",
            "- If schema enforcement is unavailable, emit only a JSON object with `reply_text` and `final_file_paths`; do not wrap it in markdown.",
            "- Use `list_session_files(section=\"latest_output\")` when you need the exact attachment paths to return.",
        ]
    )

    llm_request.contents.append(
        Content(role="user", parts=[Part(text="\n".join(summary_lines))])
    )

    reference_groups = [
        ("Uploaded workspace files from current turn:", uploaded),
        ("Generated workspace files from current turn:", generated),
    ]
    if not any(file_group for _, file_group in reference_groups):
        return

    file_parts: list[Part] = []
    for label, file_group in reference_groups:
        if not file_group:
            continue
        file_parts.append(Part(text=f"{label}\n"))
        for index, file_info in enumerate(file_group, start=1):
            file_parts.append(
                Part(
                    text=(
                        f"File {index}: {file_info['name']}. "
                        f"Path: {file_info.get('path', '')}. "
                        f"Description: {file_info.get('description', '')}\n"
                    )
                )
            )

    llm_request.contents.append(Content(role="user", parts=file_parts))


def _select_latest_non_channel_files(files_history: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Return the latest non-channel file batch recorded in session state."""
    for file_group in reversed(files_history):
        if file_group and any(str(file_info.get("source", "")).strip() != "channel" for file_info in file_group):
            return file_group
    return []


def _collect_known_workspace_paths(state: dict[str, Any]) -> set[str]:
    """Collect every normalized workspace path already tracked in the current session."""
    known_paths: set[str] = set()

    def _record(file_group: list[dict[str, Any]]) -> None:
        for file_info in file_group:
            path = str(file_info.get("path", "")).strip()
            if path:
                known_paths.add(path)

    _record(list(state.get("uploaded") or state.get("input_files") or []))
    _record(list(state.get("generated") or []))
    _record(list(state.get("new_files") or []))
    for entry in list(state.get("uploaded_history") or []):
        if isinstance(entry, dict):
            _record(list(entry.get("files") or []))
    for entry in list(state.get("generated_history") or []):
        if isinstance(entry, dict):
            _record(list(entry.get("files") or []))
    for file_group in list(state.get("files_history") or []):
        if isinstance(file_group, list):
            _record(list(file_group))
    return known_paths


def _normalize_final_response_paths(paths: list[str], *, state: dict[str, Any]) -> list[str]:
    """Validate final attachment paths against the current session file history."""
    known_paths = _collect_known_workspace_paths(state)
    normalized_paths: list[str] = []
    seen_paths: set[str] = set()
    for raw_path in paths:
        if not isinstance(raw_path, str):
            raise ValueError("Each final attachment path must be a string.")
        cleaned_path = raw_path.strip()
        if not cleaned_path:
            continue
        if Path(cleaned_path).is_absolute():
            raise ValueError("Final attachment paths must be workspace-relative, not absolute.")
        relative_path = workspace_relative_path(cleaned_path)
        if relative_path not in known_paths:
            raise ValueError(
                f"Final attachment path '{relative_path}' is not part of the current session file history."
            )
        resolved = resolve_workspace_path(relative_path)
        if not resolved.exists() or not resolved.is_file():
            raise ValueError(f"Final attachment path '{relative_path}' does not resolve to an existing file.")
        if relative_path in seen_paths:
            continue
        seen_paths.add(relative_path)
        normalized_paths.append(relative_path)
    return normalized_paths


def _select_fallback_final_response_paths(state: dict[str, Any]) -> list[str]:
    """Return valid tracked attachment paths when the LLM final response path is invalid."""
    explicit_paths = list(state.get("final_file_paths") or [])
    if explicit_paths:
        try:
            return _normalize_final_response_paths(explicit_paths, state=state)
        except ValueError:
            pass

    latest_file_group = _latest_generated_files(state)
    latest_paths = [
        str(file_info.get("path", "")).strip()
        for file_info in latest_file_group
        if isinstance(file_info, dict) and str(file_info.get("path", "")).strip()
    ]
    if not latest_paths:
        return []
    return _normalize_final_response_paths(latest_paths, state=state)


def _build_missing_structured_final_response_fallback(
    state: dict[str, Any],
    *,
    raw_final_response: str,
) -> Optional[OrchestratorFinalResponse]:
    """Create a final response from tracked outputs when the model omitted the schema."""
    try:
        fallback_final_paths = _select_fallback_final_response_paths(state)
    except ValueError:
        fallback_final_paths = []
    if not fallback_final_paths and not str(raw_final_response or "").strip():
        return None

    reply_text = _select_missing_structured_final_response_text(
        state,
        raw_final_response=raw_final_response,
    )
    return OrchestratorFinalResponse(
        reply_text=reply_text,
        final_file_paths=fallback_final_paths,
    )


def _select_missing_structured_final_response_text(
    state: dict[str, Any],
    *,
    raw_final_response: str,
) -> str:
    """Choose a user-facing reply for synthesized final responses."""
    authoritative_candidates = [
        state.get("final_response"),
        state.get("final_summary"),
        state.get("last_orchestrator_response"),
        _plain_text_final_response_payload(state.get(ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY)),
        raw_final_response,
    ]
    for candidate in authoritative_candidates:
        normalized = str(candidate or "").strip()
        if normalized:
            return normalized

    message_candidates = [
        state.get("last_output_message"),
        (state.get("current_output") or {}).get("message")
        if isinstance(state.get("current_output"), dict)
        else "",
        (state.get("last_expert_result") or {}).get("message")
        if isinstance(state.get("last_expert_result"), dict)
        else "",
    ]
    for candidate in message_candidates:
        normalized = str(candidate or "").strip()
        if normalized and not _looks_like_internal_execution_message(normalized):
            return normalized

    return "已完成，生成的文件见附件。"


def _plain_text_final_response_payload(payload: Any) -> str:
    """Return plain output-key text that is safe to use as a compatibility fallback."""
    if not isinstance(payload, str):
        return ""
    normalized = payload.strip()
    if not normalized:
        return ""
    if normalized.startswith("{") or '"reply_text"' in normalized:
        return ""
    return normalized


def _coerce_structured_final_response(payload: Any) -> OrchestratorFinalResponse:
    """Return a structured final response from dict or JSON-text payloads."""
    if isinstance(payload, OrchestratorFinalResponse):
        return payload
    if isinstance(payload, dict):
        return OrchestratorFinalResponse.model_validate(payload)
    if isinstance(payload, str):
        json_text = _extract_final_response_json(payload)
        return OrchestratorFinalResponse.model_validate_json(json_text)
    raise TypeError(f"Unsupported final response payload type: {type(payload).__name__}")


def _extract_final_response_json(text: str) -> str:
    """Extract the first JSON object from a raw model final response."""
    stripped = _strip_markdown_json_fence(text)
    if not stripped:
        raise ValueError("Final response text is empty.")

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            parsed, end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return stripped[index : index + end]
    raise ValueError("Final response text does not contain a JSON object.")


def _strip_markdown_json_fence(text: str) -> str:
    """Remove a surrounding markdown code fence from JSON-like model output."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if not lines:
        return stripped
    if lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _looks_like_internal_execution_message(message: str) -> bool:
    """Return true for tool/expert status text that should not be shown as final copy."""
    normalized = message.lower()
    internal_markers = [
        "agent has completed",
        "finished with status=",
        " output file:",
        " output files:",
        "status=success",
    ]
    return any(marker in normalized for marker in internal_markers)


def _iter_function_response_results(event: Event) -> Iterator[dict[str, Any]]:
    """Yield normalized function response payloads from one ADK event."""
    if not event.content or not event.content.parts:
        return
    for part in event.content.parts:
        function_response = getattr(part, "function_response", None)
        if not function_response:
            continue
        response = getattr(function_response, "response", None)
        if not isinstance(response, dict):
            continue
        yield response.get("result") if isinstance(response.get("result"), dict) else response


def _extract_confirmation_tool_result(event: Event) -> dict[str, Any] | None:
    """Return a tool result that asks the user to confirm before continuing."""
    for result in _iter_function_response_results(event):
        if _format_confirmation_reply(result):
            return result
    return None


def _extract_question_form_tool_result(event: Event) -> dict[str, Any] | None:
    """Return a tool result that asks the Web UI to collect form input."""
    for result in _iter_function_response_results(event):
        if _format_question_form_reply(result):
            return result
    return None


def _extract_product_confirmation_tool_result(event: Event) -> dict[str, Any] | None:
    """Return a slim product result that asks the user for confirmation."""
    for result in _iter_function_response_results(event):
        if is_product_confirmation_result(result):
            return result
    return None


def _select_ppt_product_confirmation_state_result(state: dict[str, Any]) -> dict[str, Any] | None:
    """Return the current PPT confirmation result persisted by the product manager."""
    candidates = [
        state.get("ppt_product_result"),
        state.get("last_product_result"),
        state.get("current_output"),
    ]
    for candidate in candidates:
        if is_product_confirmation_result(candidate):
            return candidate
    return None


def _extract_adk_ppt_confirmation_request(event: Event) -> dict[str, Any] | None:
    """Return a PPT ADK tool-confirmation request emitted by the runner."""
    get_function_calls = getattr(event, "get_function_calls", None)
    if not callable(get_function_calls):
        return None
    for call in get_function_calls():
        if getattr(call, "name", "") != ADK_REQUEST_CONFIRMATION_FUNCTION_NAME:
            continue
        args = getattr(call, "args", None)
        if not isinstance(args, dict):
            continue
        tool_confirmation = args.get("toolConfirmation")
        if not isinstance(tool_confirmation, dict):
            continue
        payload = tool_confirmation.get("payload")
        if not isinstance(payload, dict):
            continue
        if str(payload.get("product_line") or "").strip() != "ppt":
            continue
        return {
            "kind": "adk_tool_confirmation",
            "product_line": "ppt",
            "function_name": ADK_REQUEST_CONFIRMATION_FUNCTION_NAME,
            "function_call_id": str(getattr(call, "id", "") or ""),
            "invocation_id": str(getattr(event, "invocation_id", "") or ""),
            "tool_confirmation": tool_confirmation,
            "payload": payload,
        }
    return None


def _format_adk_ppt_confirmation_reply(request: dict[str, Any]) -> str:
    """Render an ADK PPT confirmation request as the current plain-text UX."""
    payload = request.get("payload")
    if not isinstance(payload, dict):
        return ""
    parts = [
        str(payload.get("message") or "").strip(),
        str(payload.get("summary_markdown") or "").strip(),
        str(payload.get("expected_user_action") or "").strip(),
    ]
    return "\n\n".join(part for part in parts if part)


def _build_ppt_adk_confirmation_response_payload(
    *,
    user_response: str,
    pending_request: dict[str, Any],
) -> dict[str, Any]:
    """Map ordinary user confirmation text into the PPT ADK confirmation schema."""
    response = PptAdkConfirmationResponse.model_validate(user_response)
    response_payload = response.model_dump(mode="json")
    request_payload = pending_request.get("payload")
    if isinstance(request_payload, dict):
        if request_payload.get("confirmation_id"):
            response_payload["confirmation_id"] = str(request_payload.get("confirmation_id") or "")
        if request_payload.get("stage"):
            response_payload["stage"] = str(request_payload.get("stage") or "")
    return response_payload


def _ppt_adk_hitl_enabled(state: dict[str, Any]) -> bool:
    """Return whether runtime should request ADK-native PPT HITL boundaries."""
    return bool(state.get(PPT_ADK_HITL_ENABLED_STATE_KEY))


def _with_runtime_ppt_adk_hitl_options(
    output: dict[str, Any] | None,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Add the runtime PPT ADK HITL opt-in without overriding explicit caller policy."""
    options = dict(output or {})
    if not state or not _ppt_adk_hitl_enabled(state):
        return options
    if any(options.get(key) for key in ("auto_confirm", "skip_confirmations")):
        return options
    if "confirmation_mode" not in options and "adk_hitl" not in options and "adk_tool_confirmation" not in options:
        options["confirmation_mode"] = "adk_hitl"
    return options


def _extract_completed_product_tool_result(event: Event) -> dict[str, Any] | None:
    """Return a completed product result from a tool response event."""
    for result in _iter_function_response_results(event):
        if is_completed_product_result(result):
            return result
    return None


def _extract_terminal_product_tool_result(event: Event) -> dict[str, Any] | None:
    """Return a failed terminal product result from a tool response event."""
    for result in _iter_function_response_results(event):
        if is_terminal_product_result(result):
            return result
    return None


def _should_skip_tool_result_summarization(result: Any) -> bool:
    """Return whether one tool result is already the deterministic user outcome."""
    return bool(
        _format_confirmation_reply(result)
        or _format_question_form_reply(result)
        or is_product_confirmation_result(result)
        or is_completed_product_result(result)
        or is_terminal_product_result(result)
    )


def _mark_tool_result_skip_summarization(tool_context: ToolContext | None, result: Any) -> None:
    """Ask ADK to treat deterministic tool results as final without LLM summarization."""
    if tool_context is None or not _should_skip_tool_result_summarization(result):
        return
    actions = getattr(tool_context, "actions", None)
    if actions is not None:
        actions.skip_summarization = True


def _extract_completed_page_product_tool_result(event: Event) -> dict[str, Any] | None:
    """Return a completed Page product result from a tool response event."""
    for result in _iter_function_response_results(event):
        if is_completed_page_product_result(result):
            return result
    return None


def _format_confirmation_reply(result: Any) -> str:
    """Render a product/tool confirmation request as one user-facing reply."""
    if not isinstance(result, dict):
        return ""
    confirmation_request = result.get("confirmation_request")
    if not isinstance(confirmation_request, dict):
        return ""
    summary_markdown = str(confirmation_request.get("summary_markdown") or "").strip()
    if not summary_markdown:
        return ""
    message = str(result.get("message") or "").strip()
    expected_user_action = str(confirmation_request.get("expected_user_action") or "").strip()
    reply_parts = [part for part in [message, summary_markdown, expected_user_action] if part]
    return "\n\n".join(reply_parts)


def _format_question_form_reply(result: Any) -> str:
    """Render a Web question-form request as one user-facing reply."""
    if not isinstance(result, dict):
        return ""
    if str(result.get("status") or "").strip().lower() != "needs_input":
        return ""
    message = str(result.get("message") or "").strip()
    if "<cc-question-form" not in message.lower():
        return ""
    return message


class _ReplyTextStreamExtractor:
    """Extract safe `reply_text` deltas from a streamed structured response."""

    def __init__(self, *, allow_plain_text: bool = True) -> None:
        self._raw_text = ""
        self._published_text = ""
        self._plain_text_mode = False
        self._allow_plain_text = allow_plain_text

    @property
    def published_text(self) -> str:
        """Return the user-visible text already published as deltas."""
        return self._published_text

    def append(self, text: str) -> str:
        """Append one partial model text chunk and return newly safe user-visible text."""
        chunk = str(text or "")
        if not chunk:
            return ""
        self._raw_text += chunk

        if self._plain_text_mode:
            self._published_text += chunk
            return chunk

        stripped = self._raw_text.lstrip()
        if stripped and not stripped.startswith("{") and '"reply_text"' not in stripped:
            if self._allow_plain_text:
                self._plain_text_mode = True
                self._published_text += self._raw_text
                return self._raw_text
            return ""

        reply_prefix = _extract_reply_text_prefix(self._raw_text)
        if not reply_prefix.startswith(self._published_text):
            return ""
        delta = reply_prefix[len(self._published_text) :]
        if delta:
            self._published_text = reply_prefix
        return delta


def _is_deepseek_model(model_name: str) -> bool:
    """Return whether one resolved model name belongs to the DeepSeek provider."""
    return str(model_name or "").strip().lower().startswith("deepseek/")


def _is_thought_part(part: Any) -> bool:
    """Return whether one ADK content part is marked as model thinking."""
    return getattr(part, "thought", None) is True


def _event_has_thought_text(event: Event) -> bool:
    """Return whether one model event contains thought text parts."""
    parts = getattr(getattr(event, "content", None), "parts", None) or []
    return any(_is_thought_part(part) and getattr(part, "text", None) for part in parts)


def _event_text(event: Event, *, skip_thought: bool = False) -> str:
    """Return concatenated text parts from one model event."""
    parts = getattr(getattr(event, "content", None), "parts", None) or []
    return "".join(
        str(text)
        for part in parts
        if not (skip_thought and _is_thought_part(part))
        if (text := getattr(part, "text", None))
    )


def _first_event_text(event: Event, *, skip_thought: bool = False) -> str:
    """Return the first text part from one model event."""
    parts = getattr(getattr(event, "content", None), "parts", None) or []
    for part in parts:
        if skip_thought and _is_thought_part(part):
            continue
        text = getattr(part, "text", None)
        if text:
            return str(text)
    return ""


def _extract_reply_text_prefix(text: str) -> str:
    """Return the currently available `reply_text` string prefix from JSON text."""
    key_index = text.find('"reply_text"')
    if key_index < 0:
        return ""
    colon_index = text.find(":", key_index + len('"reply_text"'))
    if colon_index < 0:
        return ""
    cursor = colon_index + 1
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor >= len(text) or text[cursor] != '"':
        return ""
    return _decode_json_string_prefix(text, cursor + 1)


def _decode_json_string_prefix(text: str, start: int) -> str:
    """Decode the available prefix of one JSON string without requiring closing JSON."""
    chars: list[str] = []
    cursor = start
    while cursor < len(text):
        char = text[cursor]
        if char == '"':
            return "".join(chars)
        if char != "\\":
            chars.append(char)
            cursor += 1
            continue

        cursor += 1
        if cursor >= len(text):
            break
        escaped = text[cursor]
        if escaped in {'"', "\\", "/"}:
            chars.append(escaped)
        elif escaped == "b":
            chars.append("\b")
        elif escaped == "f":
            chars.append("\f")
        elif escaped == "n":
            chars.append("\n")
        elif escaped == "r":
            chars.append("\r")
        elif escaped == "t":
            chars.append("\t")
        elif escaped == "u":
            hex_digits = text[cursor + 1 : cursor + 5]
            if len(hex_digits) < 4 or any(digit not in "0123456789abcdefABCDEF" for digit in hex_digits):
                break
            chars.append(chr(int(hex_digits, 16)))
            cursor += 4
        cursor += 1
    return "".join(chars)


def _should_stream_design_brief_form_placeholder(
    *,
    tool_name: str,
    state: Any,
) -> bool:
    """Return whether a design tool call should immediately show a form placeholder."""
    if tool_name != "run_design_product":
        return False
    if str(state.get("channel", "") or "").strip().lower() != "web":
        return False
    if state.get(DESIGN_BRIEF_FORM_ANSWERS_STATE_KEY):
        return False
    if state.get(DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY):
        return False
    return True


class Orchestrator:
    """Plan one workflow step at a time with skills and builtin tools."""

    def __init__(
        self,
        session_service: InMemorySessionService,
        artifact_service: InMemoryArtifactService,
        expert_agents: dict[str, BaseAgent],
        app_name: str = SYS_CONFIG.app_name,
        save_dir: str = "",
        llm_model: str = "",
    ) -> None:
        self.app_name = app_name
        self.session_service = session_service
        self.artifact_service = artifact_service
        self.expert_agents = expert_agents
        self.save_dir = save_dir
        self.uid = ""
        self.sid = ""
        self._last_run_streamed_reply_text = False
        self.skill_registry = get_skill_registry()
        self.toolbox = BuiltinToolbox()
        self.ppt_product_manager = PptProductManager()
        self.page_product_manager = PageProductManager()
        self.design_product_manager = DesignProductManager()

        model_name = resolve_llm_model_name(llm_model or SYS_CONFIG.llm_model)
        self.llm_model_name = model_name
        self._uses_deepseek_model = _is_deepseek_model(model_name)
        self.structured_output_mode = resolve_structured_output_mode(llm_model or SYS_CONFIG.llm_model)
        self.uses_native_structured_output = self.structured_output_mode == "native"
        logger.info(
            "OrchestratorAgent: using llm: {}, structured_output_mode={}",
            model_name,
            self.structured_output_mode,
        )

        agent_kwargs: dict[str, Any] = {
            "name": "CreativeClawOrchestrator",
            "model": build_llm(llm_model or SYS_CONFIG.llm_model),
            "instruction": self._build_instruction(),
            "before_model_callback": orchestrator_before_model_callback,
            "output_key": ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY,
            "tools": [
                self.list_skills,
                self.read_skill,
                self.list_dir,
                self.glob,
                self.grep,
                self.read_file,
                self.write_file,
                self.edit_file,
                self.image_crop,
                self.image_rotate,
                self.image_flip,
                self.image_info,
                self.image_resize,
                self.image_convert,
                self.video_info,
                self.video_extract_frame,
                self.video_trim,
                self.video_concat,
                self.video_convert,
                self.audio_info,
                self.audio_trim,
                self.audio_concat,
                self.audio_convert,
                self.exec_command,
                self.process_session,
                self.web_search,
                self.web_fetch,
                self.list_session_files,
                self.run_ppt_product,
                self.continue_ppt_product,
                self.run_page_product,
                self.run_design_product,
                self.invoke_agent,
            ],
        }
        if self.uses_native_structured_output:
            agent_kwargs["output_schema"] = OrchestratorFinalResponse
        self.agent = LlmAgent(**agent_kwargs)
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"\[EXPERIMENTAL\] ResumabilityConfig:.*",
                category=UserWarning,
            )
            self.app = App(
                name=self.app_name,
                root_agent=self.agent,
                resumability_config=ResumabilityConfig(is_resumable=True),
                plugins=[
                    CreativeClawStepEventPlugin(),
                    CreativeClawUsageLoggingPlugin(),
                    CreativeClawRuntimeTracePlugin(),
                ],
            )
        self.runner = Runner(
            app=self.app,
            app_name=self.app_name,
            session_service=self.session_service,
            artifact_service=self.artifact_service,
        )

    def _build_instruction(self) -> str:
        """Build the planner instruction for the orchestrator."""
        available_experts = "\n".join(
            str(expert) for expert in EXPERTS_LIST if expert.enable
        )
        skills_summary = self.skill_registry.build_summary()
        expert_contracts = build_expert_contract_summary()
        video_generation_routing_notes = build_video_generation_routing_notes()
        if self.uses_native_structured_output:
            final_response_mode_note = (
                "- Return the final delivery through the structured response fields provided by the runtime."
            )
        else:
            final_response_mode_note = (
                "- The current model is running in prompt-JSON compatibility mode. "
                "Your final output must be only a JSON object like "
                '{"reply_text":"...", "final_file_paths":[]}; do not wrap it in markdown.'
            )

        return f"""
You are Creative Claw's primary user-facing orchestrator.

Your job is to inspect the current state, use skills and tools when helpful, and directly complete the user's request in this invocation whenever possible.
Do not create a full upfront plan unless the user explicitly asks for one.
You can use built-in tools, skills, `invoke_agent`, and your own reasoning to complete the task.
You are the main agent, and expert agents are supporting capabilities invoked through `invoke_agent`.
Prefer completing the task directly instead of describing an internal workflow.

You can use seven kinds of capabilities:
1. Skills from local markdown files
2. Built-in local file tools inside the fixed workspace
3. Built-in shell and web tools
4. The PPT product-line tools `run_ppt_product` and `continue_ppt_product`
5. The Page product-line tool `run_page_product`
6. The Design product-line tool `run_design_product`
7. Existing expert agents through `invoke_agent(agent_name, prompt)`

Rules:
- Treat yourself as the main conversational agent. Reply to the user's actual request, not to an internal workflow.
- Product-line tools have priority over skills and lower-level experts. If a request belongs to PPT, Page, or Design product scope, call the product-line tool first; use skills only as supporting knowledge after the product path is chosen.
- Route by the requested final deliverable and workflow: PPTX/PowerPoint/editable slide deck goes to `run_ppt_product`; content-first HTML posters, long-image pages, visual articles, social posts, and marketing one-pagers go to `run_page_product`; UI/design artifacts such as dashboards, app prototypes, interactive tools, wireframes, and interface-heavy HTML go to `run_design_product`; standalone image deliverables stay with the orchestrator and should usually use `invoke_agent` with `ImageGenerationAgent`.
- If the current product lines cannot handle the requested deliverable or workflow, do not force the task into PPT or Design. Complete it yourself with skills, built-in tools, and existing expert agents.
- When a skill seems relevant, call `list_skills` first and then `read_skill`.
- Never invent skill content. Read the actual `SKILL.md` before using it deeply.
- Prefer direct execution over abstract planning.
- Use built-in tools for local workspace work: `list_dir`, `glob`, `grep`, `read_file`, `write_file`, `edit_file`, `image_crop`, `image_rotate`, `image_flip`, `image_info`, `image_resize`, `image_convert`, `video_info`, `video_extract_frame`, `video_trim`, `video_concat`, `video_convert`, `audio_info`, `audio_trim`, `audio_concat`, `audio_convert`, `exec_command`, `process_session`, `web_search`, `web_fetch`.
- Use `list_session_files(section=...)` when you need the exact normalized workspace paths already tracked in the current session state.
- All file paths must be relative to the fixed `workspace` directory unless the tool explicitly returns a workspace-relative path.
- Inspect local files with `list_dir`, `glob`, `grep`, and `read_file` before changing them when the path or contents are uncertain.
- Use local image, video, and audio tools for lightweight deterministic preprocessing, and keep the returned suffixed output path instead of overwriting the original by default.
- Keep changes small and reviewable, and re-check the latest state after each meaningful action.
- For coding, debugging, and file-editing tasks, prefer solving the task directly with built-in tools before delegating to an expert.
- For coding tasks, you may inspect files, write or edit code, run targeted commands with `exec_command`, inspect stdout and stderr, and iterate based on the results.
- Use `glob` to locate candidate files quickly and `grep` to find symbols, messages, and code snippets before reading full files.
- For long-running commands, start them with `exec_command(background=true, yield_ms=...)` and then use `process_session` to list, poll, inspect logs, write stdin, kill, or remove sessions.
- After writing or editing code, prefer running a small verification command with `exec_command` before finishing when verification is feasible.
- For ordinary conversation, explanations, brainstorming, lightweight analysis, and tasks that built-in tools can complete, finish directly instead of delegating.
- When planning expert parameters, pass workspace file paths with `input_path` or `input_paths` instead of artifact names.
- `input_name` is legacy and should not be used unless compatibility fallback is absolutely required.
- When using `ImageGenerationAgent`, you may pass optional `provider`, `aspect_ratio`, `resolution`, `model_name`, `size`, `negative_prompt`, `prompt_extend`, `watermark`, and `thinking_mode`.
- When using `ImageEditingAgent`, you may pass optional `provider`.
- When using `VideoGenerationAgent`, you may pass optional `prompt_rewrite`, `provider`, `mode`, `aspect_ratio`, `resolution`, `duration_seconds`, `generate_audio`, `watermark`, `negative_prompt`, `person_generation`, `seed`, `model_name`, `kling_mode`, `prompt_extend`, `image_url`, and `image_urls`.
- For exact dialogue or native generated audio with Seedance 2.0, set `provider="seedance"`, `generate_audio=true`, and `prompt_rewrite="off"` so quoted dialogue is preserved.
{video_generation_routing_notes}
- For provider `veo`, mode `video_extension` accepts one workspace video via `input_path` or `input_paths`.
- For provider `kling`, use only `prompt`, `first_frame`, `first_frame_and_last_frame`, or `multi_reference`. `multi_reference` expects 2-4 workspace images through `input_paths`. If Kling input images do not meet the documented limits, inspect them with `image_info` and decide whether to preprocess them with `image_resize` or other local image tools first. The Kling expert does not auto-resize or auto-crop inputs. Do not route Kling calls to `reference_asset`, `reference_style`, or `video_extension`.
- For cutout, local edit, inpaint-style masking, or region-targeted image workflows, prefer calling `ImageSegmentationAgent` first, then read `current_output.results[0].mask_path` from the expert result and reuse that workspace path in the next step.
- Default image provider is `nano_banana` unless the user or task clearly requires `seedream`, `gpt_image`, or DashScope image models. For DashScope image generation, set `provider="dashscope"` and choose `model_name="wan2.7-image-pro"`, `model_name="qwen-image-2.0-pro"`, or `model_name="z-image-turbo"`.
- When the user refers to a previously generated image or file without re-uploading it, inspect the workspace file history and use the most recent relevant workspace path.
- Prefer files already listed in the current session file history. Do not inspect or reuse files from unrelated session directories unless the user explicitly asks for cross-session access.
- Use `list_session_files(section="latest_output")` or the current session file history whenever you need the exact workspace-relative paths for the final response attachments.
- Do not invent attachment paths. Only return exact workspace-relative paths already known in the current session state.
- Only choose an expert agent when the task needs specialized image, search, or other expert capability that built-in tools and direct reasoning cannot handle well.
- When calling `invoke_agent`, pass a complete expert brief.
- For experts that need several parameters, encode the `prompt` argument as a JSON object string that contains the exact expert parameters.
- Prefer workspace paths in that JSON object, such as `input_path` or `input_paths`.
- `invoke_agent` returns structured data including status, message, optional output_text, and output_files.
- Keep the language of any user-facing summary or reply aligned with the user's language.
- If the user primarily writes in Chinese, reply in Chinese. If the user primarily writes in English, reply in English.
- If the user mixes languages, follow the primary language of the user's latest message.
- Use the current delivery channel context when it helps adapt formatting or tone for the reply.
- Do not expose raw routing identifiers such as `chat_id` or `sender_id` unless the user explicitly asks for them.

Creative media workflow hints:
- Do not assume video or media planning skills are globally available. Only read skills that appear in the Available skills list.
- If the user has a topic, campaign brief, rough idea, script, narration, reference image, or clip but still needs scenes, storyboard structure, prompt derivation, or QC, reason directly and prepare the smallest useful plan before invoking generation experts.
- If the user already gave a clear final generation request, execute directly with the smallest suitable expert call.
- For image/video generation, pass exact expert parameters as a JSON object string to `invoke_agent`.
- Prefer current session workspace file paths for source images, videos, audio, and generated assets.

PPT workflow routing hints:
- If session state shows a pending PPT confirmation, call `continue_ppt_product` with the user's latest response. Do not start a new PPT workflow unless the user explicitly asks to discard the current one.
- If runtime context says `Product line: ppt`, call `run_ppt_product` as the primary execution path before considering lower-level tools.
- PPT product line does its own requirement normalization, content planning, and page planning. Pass the user's PPT task description directly into `run_ppt_product`; do not rewrite it into a slide outline, chapter plan, or inferred page list.
- `run_ppt_product` intentionally pauses twice: after requirement normalization, and after content planning. Present those confirmation summaries to the user and wait for the user's confirmation or edits.
- Use `continue_ppt_product` for "确认", "继续", "可以", "改成...", or similar replies to an active PPT confirmation.
- Pass `inputs` only for real user-provided task documents or assets, such as uploaded files, workspace paths, URLs, or reference images. Do not put your own inferred outline, slides, chapters, or page content into `inputs`.
- Pass `output` only for explicit delivery constraints from the user, such as format, route, language, slide count, aspect ratio, template id, or editability.
- If the requested final deliverable is `.pptx`, PowerPoint, PPT, an editable slide deck, PPT template application, or PPTX editing, prefer `run_ppt_product` unless the user explicitly asks for a non-PPTX HTML deck or design prototype.
- PPT is an independent product line optimized for slide generation. Do not route PPTX delivery through DesignProductManager.
- Do not default `output.route` to HTML/SVG/XML. Only pass a route when the user explicitly selected it; otherwise let PptProductManager choose from uploaded inputs and task fit.
- When the user uploads or references a PowerPoint file, pass it through `inputs` and let PptProductManager decide whether to use the PPTX/XML private-skill workflow.
- PptProductManager owns PPT requirement normalization, route dispatch, route artifacts, PPTX validation, and delivery manifest registration.
- PptProductManager has private product-ppt skills under `skills/product-ppt-skills` and decides whether to use a private PPT skill workflow or the built-in HTML route. Do not read or choose those skills from the orchestrator.
- Do not call HTML, SVG, or OOXML route-internal tools directly from the orchestrator.

Page workflow routing hints:
- If the user asks for 公众号文章, 小红书文章, 朋友圈长图, HTML poster, marketing poster, visual article, content-led social creative, campaign one-pager, or long-image page, prefer `run_page_product`.
- If runtime context says `Product line: design` but the actual request is content-first poster/article/long-image work, call `run_page_product`. The current frontend may still display the returned HTML in the existing design preview surface.
- PageProductManager owns content draft, page private skills, image/content asset planning, final standalone HTML generation, lightweight validation, and delivery.
- When `run_page_product` returns `status="success"` with `final_file_paths`, treat that product result as complete. Do not call `write_file`, `edit_file`, `invoke_agent`, or another code-generation path to rewrite the returned page.
- PageProductManager has private product-page skills under `skills/product-page-skills`. Do not read or choose those skills from the orchestrator.
- Pass the user's content/page task directly into `run_page_product`; do not rewrite it into a UI design brief.
- Do not route dashboards, app screens, admin consoles, wireframes, or interaction-heavy prototypes to PageProductManager.

Design workflow routing hints:
- If runtime context says `Product line: design` and the request is not a content-first Page task, call `run_design_product` as the primary execution path before considering lower-level tools.
- If the user task is a `[cc-form-answers ...]` block and runtime context shows an active Design brief form, call `run_design_product` with the exact answer block as `task`.
- When `Product line options` includes a `design` object, pass only the concise task, relevant inputs, and explicit output request into `run_design_product`.
- If the user asks for UI design, product design, dashboard, landing page, mobile app, deck, greeting card, holiday card, invitation card, visual prototype, website mockup, or interface-heavy HTML design artifact, prefer `run_design_product` only when the requested final deliverable is not a PPTX/PowerPoint file and the task is not a content-first Page request.
- Do not route a Design product request to a standalone skill or expert just because a skill trigger matches; product first, skills second.
- DesignProductManager owns design skills, design decisions, artifact generation, progress, status, validation, and delivery.
- DesignProductManager has private product-design skills. Do not read or choose those skills from the orchestrator.
- Do not choose scenario, design system, task skill, private skill, or device frame for DesignProductManager; it decides these internally.
- Do not call lower-level code generation experts for design artifacts unless the user explicitly asks for non-design code generation.
- Do not use image/video/audio experts for code-backed design artifacts unless the user explicitly needs generated media assets.

Response Requirements:
- Put the complete user-facing natural-language reply into `reply_text` in the structured final response.
- Put any final attachments into `final_file_paths` as exact workspace-relative paths, or return `[]` when no attachments are needed.
- {final_response_mode_note}
- The final structured response is the only final delivery for the current user turn.
- Do not output internal workflow JSON beyond the required final response object when compatibility mode is active.
- Do not expose internal bookkeeping such as `current_output`, `workflow_status`, or private planning notes.
- If the task is unfinished because a tool or expert failed, explain the blocker directly and say what remains.

Available skills:
{skills_summary}

Available expert agents:
{available_experts}

Expert parameter contracts:
{expert_contracts}
"""

    @staticmethod
    def _append_step_event(
        state: dict[str, Any],
        *,
        title: str,
        detail: str,
        stage: str = "orchestrating",
        session_id: str = "",
    ) -> None:
        """Append one structured orchestrator step event into session state."""
        append_orchestration_step_event(
            state,
            title=title,
            detail=detail,
            stage=stage,
            session_id=session_id,
        )

    @staticmethod
    def _stringify_value(value: Any, max_chars: int = 180) -> str:
        """Render one tool argument or result into a compact display string."""
        return stringify_value(value, max_chars=max_chars)

    @classmethod
    def _format_tool_args(cls, args: dict[str, Any]) -> str:
        """Format tool arguments for progress display."""
        return format_tool_args(args)

    @classmethod
    def _summarize_tool_result(cls, tool_name: str, result: Any) -> tuple[str, str]:
        """Summarize one tool result into status plus short preview."""
        return summarize_tool_result(tool_name, result)

    def _record_tool_started(
        self,
        state: dict[str, Any],
        *,
        tool_name: str,
        args: dict[str, Any],
        stage: str,
    ) -> None:
        """Record one tool-call start event."""
        self._append_step_event(
            state,
            title=_DISPLAY_TOOL_TITLES.get(tool_name, tool_name),
            detail=f"Status: started\nArgs: {self._format_tool_args(args)}",
            stage=stage,
        )

    @staticmethod
    def _resolve_tool_context_session_id(tool_context: ToolContext | None) -> str:
        """Safely extract one session id from a tool context-like object."""
        session = getattr(tool_context, "session", None)
        return str(getattr(session, "id", "") or "").strip()

    def _record_tool_finished(
        self,
        state: dict[str, Any],
        *,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        stage: str,
    ) -> None:
        """Record one tool-call completion event."""
        status, summary = self._summarize_tool_result(tool_name, result)
        self._append_step_event(
            state,
            title=_DISPLAY_TOOL_TITLES.get(tool_name, tool_name),
            detail=(
                f"Status: {'success' if status == 'success' else 'error'}\n"
                f"Args: {self._format_tool_args(args)}\n"
                f"Result: {summary}"
            ),
            stage=stage,
        )

    @staticmethod
    def _record_workspace_files(
        state: dict[str, Any],
        *,
        paths: list[str],
        description: str,
        source: str,
    ) -> None:
        """Persist tool-produced workspace files into session state."""
        if not paths:
            return
        current_turn = int(state.get("turn_index", 0) or 0)
        current_step = int(state.get("step", 0) or 0)
        current_expert_step = int(state.get("expert_step", 0) or 0)
        file_records = [
            build_workspace_file_record(
                path,
                description=description,
                source=source,
                turn=current_turn,
                step=current_step,
                expert_step=current_expert_step if source == "expert" else None,
            )
            for path in paths
        ]
        generated = list(state.get("generated") or [])
        generated.extend(file_records)
        state["generated"] = generated
        history = list(state.get("files_history", []))
        history.append(file_records)
        state["new_files"] = file_records
        state["files_history"] = history

    def _snapshot_workspace_files(self) -> _WorkspaceFileSnapshot:
        """Return file metadata for files currently visible in the toolbox workspace."""
        snapshot: _WorkspaceFileSnapshot = {}
        workspace = self.toolbox.workspace_root
        if not workspace.exists():
            return snapshot

        for path in workspace.rglob("*"):
            try:
                if not path.is_file():
                    continue
                resolved = path.resolve()
                relative_path = str(resolved.relative_to(workspace))
                stat = resolved.stat()
            except (OSError, ValueError):
                continue
            snapshot[relative_path] = (stat.st_mtime_ns, stat.st_size)
        return snapshot

    def _changed_workspace_files_since(self, before_snapshot: _WorkspaceFileSnapshot) -> list[str]:
        """Return workspace-relative files created or changed since one prior snapshot."""
        after_snapshot = self._snapshot_workspace_files()
        changed_paths = [
            path
            for path, metadata in after_snapshot.items()
            if before_snapshot.get(path) != metadata
        ]
        return sorted(changed_paths)

    @staticmethod
    def _exec_command_finished_successfully(result: Any) -> bool:
        """Return whether one foreground exec command result represents a successful finish."""
        if not isinstance(result, str):
            return False
        stripped = result.strip()
        if not stripped or stripped.startswith("Error"):
            return False
        if stripped.startswith("Command still running"):
            return False
        lines = stripped.splitlines()
        return not bool(lines and lines[-1].startswith("Exit code:"))

    def _record_exec_command_files(
        self,
        state: dict[str, Any],
        *,
        before_snapshot: _WorkspaceFileSnapshot | None,
        result: Any,
    ) -> None:
        """Record files created or modified by a successful foreground exec command."""
        if before_snapshot is None or not self._exec_command_finished_successfully(result):
            return
        changed_paths = self._changed_workspace_files_since(before_snapshot)
        if not changed_paths:
            return
        self._record_workspace_files(
            state,
            paths=changed_paths,
            description="Workspace file created or updated by builtin tool `exec_command`.",
            source="builtin_tool",
        )

    @staticmethod
    def _advance_tool_counters(state: dict[str, Any], *, tool_name: str) -> None:
        """Advance session counters for one top-level tool or expert call."""
        state["step"] = int(state.get("step", 0) or 0) + 1
        if tool_name == "invoke_agent":
            state["expert_step"] = int(state.get("expert_step", 0) or 0) + 1

    @staticmethod
    def _normalize_generated_tool_result(
        state: dict[str, Any],
        *,
        tool_name: str,
        result: Any,
    ) -> Any:
        """Move one auto-generated builtin tool output into the standardized generated directory."""
        if tool_name not in _AUTO_OUTPUT_TOOL_NAMES or not isinstance(result, str) or result.startswith("Error"):
            return result
        try:
            relocated_path = relocate_generated_output(
                result,
                session_id=str(state.get("sid", "")).strip(),
                turn_index=int(state.get("turn_index", 0) or 0),
                step=int(state.get("step", 0) or 0),
                output_type=tool_name,
                index=0,
            )
        except Exception as exc:
            logger.warning("Failed to relocate builtin tool output for {}: {}", tool_name, exc)
            return result
        return workspace_relative_path(relocated_path)

    def _maybe_record_tool_files(
        self,
        state: dict[str, Any],
        *,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        before_snapshot: _WorkspaceFileSnapshot | None = None,
    ) -> None:
        """Persist workspace file outputs produced by builtin tools."""
        if isinstance(result, str) and result.startswith("Error"):
            return
        if tool_name in {
            "image_crop",
            "image_rotate",
            "image_flip",
            "image_resize",
            "image_convert",
            "video_extract_frame",
            "video_trim",
            "video_concat",
            "video_convert",
            "audio_trim",
            "audio_concat",
            "audio_convert",
        } and isinstance(result, str):
            self._record_workspace_files(
                state,
                paths=[result],
                description=f"Workspace file generated by builtin tool `{tool_name}`.",
                source="builtin_tool",
            )
        elif tool_name in {"write_file", "edit_file"}:
            path = str(args.get("path", "")).strip()
            if path:
                self._record_workspace_files(
                    state,
                    paths=[path],
                    description=f"Workspace file updated by builtin tool `{tool_name}`.",
                    source="builtin_tool",
                )
        elif tool_name == "exec_command" and not bool(args.get("background")):
            self._record_exec_command_files(
                state,
                before_snapshot=before_snapshot,
                result=result,
            )

    def _run_tool_with_events(
        self,
        *,
        tool_context: ToolContext | None,
        tool_name: str,
        stage: str,
        args: dict[str, Any],
        runner,
    ):
        """Execute one tool and record its start and finish events when context exists."""
        if tool_context is None:
            return runner()
        session_id = self._resolve_tool_context_session_id(tool_context)
        if session_id:
            get_cancellation_manager().raise_if_cancelled(session_id)
        self._advance_tool_counters(tool_context.state, tool_name=tool_name)
        should_record_manually = (not step_event_streaming_active()) or tool_name not in _PLUGIN_MANAGED_TOOL_NAMES
        if should_record_manually:
            self._record_tool_started(tool_context.state, tool_name=tool_name, args=args, stage=stage)
        before_snapshot = (
            self._snapshot_workspace_files()
            if tool_name == "exec_command" and not bool(args.get("background"))
            else None
        )
        with builtin_tool_scope(session_id):
            result = runner()
        if session_id:
            get_cancellation_manager().raise_if_cancelled(session_id)
        result = self._normalize_generated_tool_result(
            tool_context.state,
            tool_name=tool_name,
            result=result,
        )
        self._maybe_record_tool_files(
            tool_context.state,
            tool_name=tool_name,
            args=args,
            result=result,
            before_snapshot=before_snapshot,
        )
        if should_record_manually:
            self._record_tool_finished(
                tool_context.state,
                tool_name=tool_name,
                args=args,
                result=result,
                stage=stage,
            )
        return result

    def _run_builtin_tool(
        self,
        *,
        tool_context: ToolContext | None,
        tool_name: str,
        stage: str,
        args: dict[str, Any],
    ):
        """Run one BuiltinToolbox method with standard orchestrator event handling."""
        method = getattr(self.toolbox, tool_name)
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name=tool_name,
            stage=stage,
            args=args,
            runner=lambda: method(**args),
        )

    async def _run_async_tool_with_events(
        self,
        *,
        tool_context: ToolContext | None,
        tool_name: str,
        stage: str,
        args: dict[str, Any],
        runner,
    ):
        """Execute one async tool and record its lifecycle events when context exists."""
        if tool_context is None:
            return await runner()
        session_id = self._resolve_tool_context_session_id(tool_context)
        if session_id:
            get_cancellation_manager().raise_if_cancelled(session_id)
        self._advance_tool_counters(tool_context.state, tool_name=tool_name)
        should_record_manually = (not step_event_streaming_active()) or tool_name not in _PLUGIN_MANAGED_TOOL_NAMES
        if should_record_manually:
            self._record_tool_started(tool_context.state, tool_name=tool_name, args=args, stage=stage)
        if session_id and _should_stream_design_brief_form_placeholder(
            tool_name=tool_name,
            state=tool_context.state,
        ):
            published = await publish_assistant_delta(
                session_id=session_id,
                turn_index=int(tool_context.state.get("turn_index", 0) or 0),
                delta="正在准备需求确认表单...",
            )
            if published:
                self._last_run_streamed_reply_text = True
        result = await runner()
        if session_id:
            get_cancellation_manager().raise_if_cancelled(session_id)
        if should_record_manually:
            self._record_tool_finished(
                tool_context.state,
                tool_name=tool_name,
                args=args,
                result=result,
                stage=stage,
            )
        _mark_tool_result_skip_summarization(tool_context, result)
        return result

    @staticmethod
    def build_runner_message(instruction: str) -> Content:
        """Create an ADK-compatible user message for one orchestrator turn."""
        return Content(role="user", parts=[Part(text=instruction)])

    def list_skills(self, tool_context: ToolContext | None = None) -> str:
        """List available skills in JSON format."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="list_skills",
            stage="planning",
            args={},
            runner=lambda: json.dumps(
                [
                    {
                        "name": info.name,
                        "description": info.description,
                        "source": info.source,
                    }
                    for info in self.skill_registry.list_skills()
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )

    def read_skill(self, name: str, tool_context: ToolContext | None = None) -> str:
        """Read the full markdown content of one skill."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="read_skill",
            stage="planning",
            args={"name": name},
            runner=lambda: self.skill_registry.read_skill(name),
        )

    def list_dir(self, path: str = ".", tool_context: ToolContext | None = None) -> str:
        """List one directory and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="list_dir",
            stage="inspection",
            args={"path": path},
        )

    def glob(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 200,
        entry_type: str = "files",
        tool_context: ToolContext | None = None,
    ) -> str:
        """Find workspace paths matching one glob pattern."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="glob",
            stage="inspection",
            args={
                "pattern": pattern,
                "path": path,
                "max_results": max_results,
                "entry_type": entry_type,
            },
        )

    def grep(
        self,
        pattern: str,
        path: str = ".",
        glob_pattern: str | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "files_with_matches",
        context_before: int = 0,
        context_after: int = 0,
        max_results: int = 100,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Search workspace file contents with regex or fixed-string matching."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="grep",
            stage="inspection",
            args={
                "pattern": pattern,
                "path": path,
                "glob_pattern": glob_pattern,
                "case_insensitive": case_insensitive,
                "fixed_strings": fixed_strings,
                "output_mode": output_mode,
                "context_before": context_before,
                "context_after": context_after,
                "max_results": max_results,
            },
        )

    def read_file(self, path: str, tool_context: ToolContext | None = None) -> str:
        """Read one file and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="read_file",
            stage="inspection",
            args={"path": path},
        )

    def write_file(self, path: str, content: str, tool_context: ToolContext | None = None) -> str:
        """Write one file and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="write_file",
            stage="editing",
            args={"path": path, "content": content},
        )

    def edit_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Edit one file and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="edit_file",
            stage="editing",
            args={"path": path, "old_text": old_text, "new_text": new_text},
        )

    def image_crop(
        self,
        path: str,
        left: int,
        top: int,
        right: int,
        bottom: int,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Crop one image and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="image_crop",
            stage="image_processing",
            args={"path": path, "left": left, "top": top, "right": right, "bottom": bottom},
        )

    def image_rotate(
        self,
        path: str,
        degrees: float,
        expand: bool = True,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Rotate one image and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="image_rotate",
            stage="image_processing",
            args={"path": path, "degrees": degrees, "expand": expand},
        )

    def image_flip(self, path: str, direction: str, tool_context: ToolContext | None = None) -> str:
        """Flip one image and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="image_flip",
            stage="image_processing",
            args={"path": path, "direction": direction},
        )

    def image_info(self, path: str, tool_context: ToolContext | None = None) -> str:
        """Read one image metadata payload and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="image_info",
            stage="image_processing",
            args={"path": path},
        )

    def image_resize(
        self,
        path: str,
        width: int | None = None,
        height: int | None = None,
        keep_aspect_ratio: bool = True,
        resample: str = "lanczos",
        tool_context: ToolContext | None = None,
    ) -> str:
        """Resize one image and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="image_resize",
            stage="image_processing",
            args={
                "path": path,
                "width": width,
                "height": height,
                "keep_aspect_ratio": keep_aspect_ratio,
                "resample": resample,
            },
        )

    def image_convert(
        self,
        path: str,
        output_format: str,
        mode: str | None = None,
        quality: int | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Convert one image and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="image_convert",
            stage="image_processing",
            args={"path": path, "output_format": output_format, "mode": mode, "quality": quality},
        )

    def video_info(self, path: str, tool_context: ToolContext | None = None) -> str:
        """Read one video metadata payload and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="video_info",
            stage="video_processing",
            args={"path": path},
        )

    def video_extract_frame(
        self,
        path: str,
        timestamp: str,
        output_format: str = "png",
        tool_context: ToolContext | None = None,
    ) -> str:
        """Extract one frame from one video and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="video_extract_frame",
            stage="video_processing",
            args={"path": path, "timestamp": timestamp, "output_format": output_format},
        )

    def video_trim(
        self,
        path: str,
        start_time: str,
        end_time: str | None = None,
        duration: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Trim one video and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="video_trim",
            stage="video_processing",
            args={"path": path, "start_time": start_time, "end_time": end_time, "duration": duration},
        )

    def video_concat(
        self,
        paths: list[str],
        output_format: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Concatenate videos and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="video_concat",
            stage="video_processing",
            args={"paths": paths, "output_format": output_format},
        )

    def video_convert(
        self,
        path: str,
        output_format: str,
        video_codec: str | None = None,
        audio_codec: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Convert one video and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="video_convert",
            stage="video_processing",
            args={
                "path": path,
                "output_format": output_format,
                "video_codec": video_codec,
                "audio_codec": audio_codec,
            },
        )

    def audio_info(self, path: str, tool_context: ToolContext | None = None) -> str:
        """Read one audio metadata payload and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="audio_info",
            stage="audio_processing",
            args={"path": path},
        )

    def audio_trim(
        self,
        path: str,
        start_time: str,
        end_time: str | None = None,
        duration: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Trim one audio clip and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="audio_trim",
            stage="audio_processing",
            args={"path": path, "start_time": start_time, "end_time": end_time, "duration": duration},
        )

    def audio_concat(
        self,
        paths: list[str],
        output_format: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Concatenate audio clips and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="audio_concat",
            stage="audio_processing",
            args={"paths": paths, "output_format": output_format},
        )

    def audio_convert(
        self,
        path: str,
        output_format: str,
        sample_rate: int | None = None,
        bitrate: str | None = None,
        channels: int | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Convert one audio clip and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="audio_convert",
            stage="audio_processing",
            args={
                "path": path,
                "output_format": output_format,
                "sample_rate": sample_rate,
                "bitrate": bitrate,
                "channels": channels,
            },
        )

    def exec_command(
        self,
        command: str,
        working_dir: str | None = None,
        timeout: int = 60,
        background: bool = False,
        yield_ms: int = 1000,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Execute one command and record the step."""
        scope_key = self._resolve_tool_context_session_id(tool_context) if tool_context is not None else None
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="exec_command",
            stage="execution",
            args={
                "command": command,
                "working_dir": working_dir,
                "timeout": timeout,
                "background": background,
                "yield_ms": yield_ms,
            },
            runner=lambda: self.toolbox.exec_command(
                command,
                working_dir=working_dir,
                timeout=timeout,
                background=background,
                yield_ms=yield_ms,
                scope_key=scope_key,
            ),
        )

    def process_session(
        self,
        action: str = "list",
        session_id: str | None = None,
        input_text: str = "",
        timeout_ms: int = 0,
        offset: int = 0,
        limit: int = 200,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Manage background command sessions and inspect their outputs."""
        scope_key = self._resolve_tool_context_session_id(tool_context) if tool_context is not None else None
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="process_session",
            stage="execution",
            args={
                "action": action,
                "session_id": session_id,
                "input_text": input_text,
                "timeout_ms": timeout_ms,
                "offset": offset,
                "limit": limit,
            },
            runner=lambda: self.toolbox.process_session(
                action=action,
                session_id=session_id,
                input_text=input_text,
                timeout_ms=timeout_ms,
                offset=offset,
                limit=limit,
                scope_key=scope_key,
            ),
        )

    def web_search(self, query: str, count: int = 5, tool_context: ToolContext | None = None) -> str:
        """Search the web and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="web_search",
            stage="research",
            args={"query": query, "count": count},
        )

    def web_fetch(self, url: str, max_chars: int = 50000, tool_context: ToolContext | None = None) -> str:
        """Fetch one webpage and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="web_fetch",
            stage="research",
            args={"url": url, "max_chars": max_chars},
        )

    def list_session_files(self, section: str = "all", tool_context: ToolContext | None = None) -> str:
        """List normalized workspace file records already known in the current session."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="list_session_files",
            stage="inspection",
            args={"section": section},
            runner=lambda: self._list_session_files(section, tool_context=tool_context),
        )

    @staticmethod
    def _list_session_files(section: str, *, tool_context: ToolContext | None) -> str:
        """Return session-tracked file records in JSON form for one requested section."""
        if tool_context is None:
            return "Error: tool context is required to inspect session files."

        normalized_section = str(section or "all").strip().lower() or "all"
        state = tool_context.state
        uploaded = list(state.get("uploaded") or state.get("input_files") or [])
        uploaded_history = list(state.get("uploaded_history") or [])
        generated = list(state.get("generated") or [])
        generated_history = list(state.get("generated_history") or [])
        files_history = list(state.get("files_history") or [])
        latest_output_files = _latest_generated_files(state)
        payload_by_section = {
            "uploaded": {"uploaded": uploaded},
            "uploaded_history": {"uploaded_history": uploaded_history},
            "generated": {"generated": generated},
            "generated_history": {"generated_history": generated_history},
            "input": {"input_files": uploaded},
            "new": {"new_files": list(state.get("new_files") or [])},
            "latest_output": {"latest_output_files": latest_output_files},
            "history": {"files_history": files_history},
            "all": {
                "uploaded": uploaded,
                "uploaded_history": uploaded_history,
                "generated": generated,
                "generated_history": generated_history,
                "input_files": uploaded,
                "new_files": list(state.get("new_files") or []),
                "latest_output_files": latest_output_files,
                "files_history": files_history,
            },
        }
        if normalized_section not in payload_by_section:
            allowed = ", ".join(payload_by_section.keys())
            return f"Error: Unsupported section `{section}`. Allowed: {allowed}"
        return json.dumps(payload_by_section[normalized_section], ensure_ascii=False, indent=2)

    async def run_ppt_product(
        self,
        task: str,
        inputs: Any | None = None,
        output: dict[str, Any] | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Hand the user's PPT task and real document inputs to PptProductManager."""
        request = ProductToolRequest(
            product_line="ppt",
            task=task,
            inputs=inputs,
            output=_with_runtime_ppt_adk_hitl_options(
                output,
                getattr(tool_context, "state", None) if tool_context is not None else None,
            ),
        )
        manager_kwargs = request.to_manager_kwargs()

        async def _runner() -> dict[str, Any]:
            result = await self.ppt_product_manager.run_product_request(
                task=manager_kwargs["task"],
                inputs=manager_kwargs["inputs"],
                output=manager_kwargs["output"],
                tool_context=tool_context,
                expert_agents=self.expert_agents,
                app_name=self.app_name,
                artifact_service=self.artifact_service,
            )
            return slim_product_result(result)

        return await self._run_async_tool_with_events(
            tool_context=tool_context,
            tool_name="run_ppt_product",
            stage="ppt_product_planning",
            args=request.to_event_args(),
            runner=_runner,
        )

    async def run_design_product(
        self,
        task: str,
        inputs: list[dict[str, Any]] | None = None,
        output: dict[str, Any] | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Hand one concise design product task to DesignProductManager."""
        request = ProductToolRequest(
            product_line="design",
            task=task,
            inputs=inputs,
            output=output,
        )
        manager_kwargs = request.to_manager_kwargs()

        async def _runner() -> dict[str, Any]:
            result = await self.design_product_manager.run_product_request(
                task=manager_kwargs["task"],
                inputs=manager_kwargs["inputs"],
                output=manager_kwargs["output"],
                tool_context=tool_context,
                expert_agents=self.expert_agents,
                app_name=self.app_name,
                artifact_service=self.artifact_service,
            )
            return slim_product_result(result)

        return await self._run_async_tool_with_events(
            tool_context=tool_context,
            tool_name="run_design_product",
            stage="design_planning",
            args=request.to_event_args(),
            runner=_runner,
        )

    async def run_page_product(
        self,
        task: str,
        inputs: list[dict[str, Any]] | None = None,
        output: dict[str, Any] | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Hand one concise content-first page task to PageProductManager."""
        request = ProductToolRequest(
            product_line="page",
            task=task,
            inputs=inputs,
            output=output,
        )
        manager_kwargs = request.to_manager_kwargs()

        async def _runner() -> dict[str, Any]:
            result = await self.page_product_manager.run_product_request(
                task=manager_kwargs["task"],
                inputs=manager_kwargs["inputs"],
                output=manager_kwargs["output"],
                tool_context=tool_context,
                expert_agents=self.expert_agents,
                app_name=self.app_name,
                artifact_service=self.artifact_service,
            )
            return slim_product_result(result)

        return await self._run_async_tool_with_events(
            tool_context=tool_context,
            tool_name="run_page_product",
            stage="page_planning",
            args=request.to_event_args(),
            runner=_runner,
        )

    async def continue_ppt_product(
        self,
        user_response: str,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Continue an active PPT product workflow after user confirmation or edits."""

        async def _runner() -> dict[str, Any]:
            if tool_context is None:
                return {
                    "status": "error",
                    "message": "continue_ppt_product requires tool context.",
                }
            tool_confirmation = getattr(tool_context, "tool_confirmation", None)
            if _ppt_adk_hitl_enabled(tool_context.state) or tool_confirmation is not None:
                # ADK 2.1 reliably resumes the initial product tool confirmation. For
                # continuation tools, keep later gates on the existing plain-text loop.
                output_options = (
                    _with_runtime_ppt_adk_hitl_options({}, tool_context.state)
                    if tool_confirmation is not None
                    else {}
                )
                result = await self.ppt_product_manager.run_product_request(
                    task=user_response,
                    inputs=[],
                    output=output_options,
                    tool_context=tool_context,
                    expert_agents=self.expert_agents,
                    app_name=self.app_name,
                    artifact_service=self.artifact_service,
                )
            else:
                result = await self.ppt_product_manager.continue_product_request(
                    user_response=user_response,
                    tool_context=tool_context,
                    expert_agents=self.expert_agents,
                    app_name=self.app_name,
                    artifact_service=self.artifact_service,
                )
            return slim_product_result(result)

        return await self._run_async_tool_with_events(
            tool_context=tool_context,
            tool_name="continue_ppt_product",
            stage="ppt_product_confirmation",
            args={"user_response": user_response},
            runner=_runner,
        )

    async def invoke_agent(
        self,
        agent_name: str,
        prompt: str,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Invoke one expert agent through the shared dispatcher."""
        invocation = await self._run_async_tool_with_events(
            tool_context=tool_context,
            tool_name="invoke_agent",
            stage="expert_execution",
            args={"agent_name": agent_name, "prompt": prompt},
            runner=lambda: dispatch_expert_request(
                ExpertInvocationRequest(
                    agent_name=agent_name,
                    prompt=prompt,
                    tool_context=tool_context,
                    expert_agents=self.expert_agents,
                )
            ),
        )
        if invocation.assistant_text_streamed:
            self._last_run_streamed_reply_text = True
        return invocation.tool_result

    async def run_agent_and_log_events(
        self,
        user_id: str,
        session_id: str,
        new_message: Optional[Content] = None,
        invocation_id: str | None = None,
    ) -> str:
        """Run one orchestrator turn and collect the final text response."""
        final_response_text = ""
        pending_final_reply: str | None = None
        self._last_run_streamed_reply_text = False
        current_session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        turn_index = int((current_session.state if current_session else {}).get("turn_index", 0) or 0)
        reply_stream = _ReplyTextStreamExtractor(allow_plain_text=True)
        stream_reply_text = assistant_delta_streaming_active()
        deepseek_thinking_placeholder_sent = False
        run_config_kwargs: dict[str, Any] = {
            "max_llm_calls": SYS_CONFIG.max_iterations_orchestrator,
        }
        if stream_reply_text:
            run_config_kwargs["streaming_mode"] = StreamingMode.SSE
        async for event in self.runner.run_async(
            user_id=user_id,
            session_id=session_id,
            invocation_id=invocation_id,
            new_message=new_message,
            run_config=RunConfig(**run_config_kwargs),
        ):
            logger.trace(
                "uid: {}, sid: {}, Event: {}",
                user_id,
                session_id,
                event.model_dump_json(indent=2, exclude_none=True),
            )
            if (
                pending_final_reply is None
                and stream_reply_text
                and getattr(event, "partial", False)
                and event.content
                and event.content.parts
            ):
                if self._uses_deepseek_model and _event_has_thought_text(event):
                    if not deepseek_thinking_placeholder_sent:
                        published = await publish_assistant_delta(
                            session_id=session_id,
                            turn_index=turn_index,
                            delta=_DEEPSEEK_THINKING_PLACEHOLDER,
                            delta_kind=ASSISTANT_DELTA_KIND_THINKING_PLACEHOLDER,
                        )
                        if published:
                            self._last_run_streamed_reply_text = True
                            deepseek_thinking_placeholder_sent = True
                text_delta = _event_text(event, skip_thought=self._uses_deepseek_model)
                safe_delta = reply_stream.append(text_delta)
                if safe_delta:
                    published = await publish_assistant_delta(
                        session_id=session_id,
                        turn_index=turn_index,
                        delta=safe_delta,
                    )
                    if published:
                        self._last_run_streamed_reply_text = True
            if pending_final_reply is not None:
                continue
            adk_confirmation_request = _extract_adk_ppt_confirmation_request(event)
            if adk_confirmation_request is not None:
                final_reply = _format_adk_ppt_confirmation_reply(adk_confirmation_request)
                await self._persist_adk_ppt_confirmation_request(
                    user_id=user_id,
                    session_id=session_id,
                    request=adk_confirmation_request,
                )
                await self._persist_confirmation_final_response(
                    user_id=user_id,
                    session_id=session_id,
                    reply_text=final_reply,
                )
                pending_final_reply = final_reply
                continue
            confirmation_result = _extract_confirmation_tool_result(event)
            if confirmation_result is not None:
                final_reply = _format_confirmation_reply(confirmation_result)
                await self._persist_confirmation_final_response(
                    user_id=user_id,
                    session_id=session_id,
                    reply_text=final_reply,
                )
                pending_final_reply = final_reply
                continue
            question_form_result = _extract_question_form_tool_result(event)
            if question_form_result is not None:
                final_reply = _format_question_form_reply(question_form_result)
                await self._persist_confirmation_final_response(
                    user_id=user_id,
                    session_id=session_id,
                    reply_text=final_reply,
                )
                pending_final_reply = final_reply
                continue
            product_confirmation_result = _extract_product_confirmation_tool_result(event)
            if product_confirmation_result is not None:
                final_reply = str(product_confirmation_result.get("message") or "").strip()
                await self._persist_structured_final_response(
                    user_id=user_id,
                    session_id=session_id,
                    reply_text=final_reply,
                    final_file_paths=[],
                )
                pending_final_reply = final_reply
                continue
            completed_product_result = _extract_completed_product_tool_result(event)
            if completed_product_result is not None:
                final_reply = str(completed_product_result.get("message") or "").strip()
                final_file_paths = list(completed_product_result.get("final_file_paths") or [])
                await self._persist_structured_final_response(
                    user_id=user_id,
                    session_id=session_id,
                    reply_text=final_reply,
                    final_file_paths=final_file_paths,
                )
                pending_final_reply = final_reply
                continue
            terminal_product_result = _extract_terminal_product_tool_result(event)
            if terminal_product_result is not None:
                final_reply = str(terminal_product_result.get("message") or "").strip()
                await self._persist_structured_final_response(
                    user_id=user_id,
                    session_id=session_id,
                    reply_text=final_reply,
                    final_file_paths=[],
                )
                pending_final_reply = final_reply
                continue
            if (
                event.is_final_response()
                and not getattr(event, "partial", False)
                and event.content
                and event.content.parts
            ):
                text_part = _first_event_text(event, skip_thought=self._uses_deepseek_model)
                if text_part:
                    final_response_text = text_part
        if pending_final_reply is not None:
            return pending_final_reply
        if not final_response_text and reply_stream.published_text:
            final_response_text = reply_stream.published_text
        return final_response_text

    async def _persist_confirmation_final_response(
        self,
        *,
        user_id: str,
        session_id: str,
        reply_text: str,
    ) -> None:
        """Persist a tool-requested confirmation as the final user reply for this turn."""
        await self._persist_structured_final_response(
            user_id=user_id,
            session_id=session_id,
            reply_text=reply_text,
            final_file_paths=[],
        )

    async def _persist_adk_ppt_confirmation_request(
        self,
        *,
        user_id: str,
        session_id: str,
        request: dict[str, Any],
    ) -> None:
        """Persist one ADK-native PPT confirmation request for the next user turn."""
        current_session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if current_session is None:
            return
        await self.session_service.append_event(
            current_session,
            Event(
                author="api_server",
                actions=EventActions(
                    state_delta={
                        PPT_ADK_PENDING_CONFIRMATION_STATE_KEY: request,
                        PPT_ADK_HITL_ENABLED_STATE_KEY: True,
                    }
                ),
            ),
        )

    async def _clear_adk_ppt_confirmation_request(
        self,
        current_session,
    ) -> None:
        """Clear the stored ADK PPT confirmation request before resuming it."""
        await self.session_service.append_event(
            current_session,
            Event(
                author="api_server",
                actions=EventActions(
                    state_delta={PPT_ADK_PENDING_CONFIRMATION_STATE_KEY: None}
                ),
            ),
        )

    async def _persist_structured_final_response(
        self,
        *,
        user_id: str,
        session_id: str,
        reply_text: str,
        final_file_paths: list[str],
    ) -> None:
        """Persist a structured final response requested by deterministic tool handling."""
        current_session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if current_session is None:
            return
        structured_response = OrchestratorFinalResponse(
            reply_text=reply_text,
            final_file_paths=final_file_paths,
        )
        await self.session_service.append_event(
            current_session,
            Event(
                author="api_server",
                actions=EventActions(
                    state_delta={
                        ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY: structured_response.model_dump(mode="json")
                    }
                ),
            ),
        )

    async def run_until_done(self) -> dict[str, Any]:
        """Run one orchestrator invocation and persist the structured final response."""
        current_session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=self.uid,
            session_id=self.sid,
        )
        current_session.state["last_output_message"] = ""
        current_session.state["last_orchestrator_response"] = ""
        previous_event_count = len(current_session.state.get("orchestration_events", []))
        pending_adk_confirmation = current_session.state.get(PPT_ADK_PENDING_CONFIRMATION_STATE_KEY)
        resume_invocation_id = ""
        resume_message: Content | None = None
        resumed_adk_ppt_confirmation = False
        if isinstance(pending_adk_confirmation, dict):
            resume_invocation_id = str(pending_adk_confirmation.get("invocation_id") or "").strip()
            function_call_id = str(pending_adk_confirmation.get("function_call_id") or "").strip()
            if resume_invocation_id and function_call_id:
                resumed_adk_ppt_confirmation = True
                response_payload = _build_ppt_adk_confirmation_response_payload(
                    user_response=str(current_session.state.get("user_prompt") or ""),
                    pending_request=pending_adk_confirmation,
                )
                resume_message = Content(
                    role="user",
                    parts=[
                        Part(
                            function_response=FunctionResponse(
                                id=function_call_id,
                                name=ADK_REQUEST_CONFIRMATION_FUNCTION_NAME,
                                response={
                                    "confirmed": True,
                                    "payload": response_payload,
                                },
                            )
                        )
                    ],
                )
                await self._clear_adk_ppt_confirmation_request(current_session)

        raw_final_response = await self.run_agent_and_log_events(
            user_id=self.uid,
            session_id=self.sid,
            invocation_id=resume_invocation_id or None,
            new_message=resume_message
            or self.build_runner_message(
                "Review the current state, use built-in tools or invoke_agent when helpful, and answer the user directly once the task is complete."
            ),
        )

        current_session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=self.uid,
            session_id=self.sid,
        )
        if current_session is None:
            raise ValueError(f"Session {self.sid} not found for user {self.uid}")

        state = current_session.state
        if resumed_adk_ppt_confirmation:
            confirmation_result = _select_ppt_product_confirmation_state_result(state)
            if confirmation_result is not None:
                confirmation_reply = str(confirmation_result.get("message") or "").strip()
                if confirmation_reply:
                    await self._persist_structured_final_response(
                        user_id=self.uid,
                        session_id=self.sid,
                        reply_text=confirmation_reply,
                        final_file_paths=[],
                    )
                    raw_final_response = confirmation_reply
                    current_session = await self.session_service.get_session(
                        app_name=self.app_name,
                        user_id=self.uid,
                        session_id=self.sid,
                    )
                    if current_session is None:
                        raise ValueError(f"Session {self.sid} not found for user {self.uid}")
                    state = current_session.state
        structured_response_payload = state.get(ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY)
        synthesized_structured_response = False
        structured_response: OrchestratorFinalResponse | None = None
        parse_errors: list[Exception] = []
        parse_candidates = []
        if structured_response_payload is not None:
            parse_candidates.append(structured_response_payload)
        if raw_final_response:
            parse_candidates.append(raw_final_response)
        for candidate in parse_candidates:
            try:
                structured_response = _coerce_structured_final_response(candidate)
                break
            except Exception as exc:
                parse_errors.append(exc)

        if structured_response is None:
            structured_response = _build_missing_structured_final_response_fallback(
                state,
                raw_final_response=raw_final_response,
            )
            if structured_response is None:
                if parse_errors:
                    raise ValueError(
                        "Invalid structured final response payload stored in session state."
                    ) from parse_errors[0]
                raise ValueError(
                    "Missing structured final response in session state. "
                    f"Last observed final text: {raw_final_response!r}"
                )
            synthesized_structured_response = True
            logger.warning(
                "Synthesized structured final response for session {} from tracked output paths: {}",
                self.sid,
                structured_response.final_file_paths,
            )

        normalized_response = structured_response.reply_text.strip()
        if not normalized_response:
            raise ValueError("Structured final response must include a non-empty `reply_text`.")
        try:
            normalized_final_paths = _normalize_final_response_paths(
                structured_response.final_file_paths,
                state=state,
            )
        except ValueError as exc:
            fallback_final_paths = _select_fallback_final_response_paths(state)
            if not fallback_final_paths:
                raise
            logger.warning(
                "Structured final response used invalid attachment paths for session {}; "
                "falling back to tracked session outputs. error={}",
                self.sid,
                exc,
            )
            normalized_final_paths = fallback_final_paths

        self._append_step_event(
            state,
            title="Finalize Result",
            detail="Preparing the final reply.",
            stage="finalizing",
            session_id=self.sid,
        )
        state_delta = {
            "workflow_status": "finished",
            "final_summary": normalized_response,
            "final_response": normalized_response,
            "final_file_paths": normalized_final_paths,
            "last_output_message": normalized_response,
            "last_orchestrator_response": normalized_response,
            "orchestration_events": list(state.get("orchestration_events", [])),
        }
        if synthesized_structured_response:
            state_delta[ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY] = structured_response.model_dump(mode="json")
        await self.session_service.append_event(
            current_session,
            Event(author="api_server", actions=EventActions(state_delta=state_delta)),
        )

        orchestration_events = list(state_delta["orchestration_events"])
        return {
            "workflow_status": "finished",
            "final_summary": normalized_response,
            "final_response": normalized_response,
            "final_file_paths": normalized_final_paths,
            "last_output_message": normalized_response,
            "assistant_text_streamed": self._last_run_streamed_reply_text,
            "new_orchestration_events": orchestration_events[previous_event_count:],
        }
