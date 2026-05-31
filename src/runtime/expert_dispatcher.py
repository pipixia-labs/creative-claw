"""Helpers for orchestrator-driven expert invocation."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.tools.tool_context import ToolContext

from src.runtime.agent_tool_transport import run_agent_tool
from src.runtime.expert_registry import (
    build_fallback_parameters,
    normalize_expert_output,
    validate_expert_parameters,
)
from src.runtime.runtime_trace import trace_runtime_event
from src.runtime.workspace import (
    build_workspace_file_record,
    normalize_file_references,
    resolve_workspace_path,
)

_MISSING = object()

_NON_FORWARDABLE_STATE_KEYS = {
    "app_name",
    "uid",
    "sid",
    "user_prompt",
    "turn_index",
    "step",
    "expert_step",
    "input_files",
    "uploaded",
    "uploaded_history",
    "generated",
    "generated_history",
    "current_parameters",
    "current_output",
    "files_history",
    "new_files",
    "text_history",
    "message_history",
    "summary_history",
    "last_output_message",
    "last_expert_result",
    "expert_history",
    "workflow_status",
    "final_summary",
    "final_response",
    "last_orchestrator_response",
    "orchestration_events",
}


@dataclass(slots=True)
class ExpertInvocationRequest:
    """Inputs required to invoke one expert through Creative Claw dispatch."""

    agent_name: str
    prompt: str
    tool_context: ToolContext
    expert_agents: dict[str, BaseAgent]


@dataclass(slots=True)
class ExpertInvocationResult:
    """Normalized result returned from one expert invocation."""

    agent_name: str
    normalized_parameters: dict[str, Any]
    current_output: dict[str, Any]
    state_delta: dict[str, Any]
    tool_result: dict[str, Any]
    assistant_text_streamed: bool = False


@dataclass(slots=True)
class _ExpertAgentRunResult:
    """Raw ADK AgentTool run result before parent-state merge."""

    current_output: dict[str, Any]
    forwarded_state_delta: dict[str, Any]
    assistant_text_streamed: bool = False


@dataclass(slots=True)
class _ExpertToolResult:
    """Structured tool result before conversion to the public dict contract."""

    agent_name: str
    status: str
    message: str
    output_text: str
    output_files: list[dict[str, Any]]
    structured_data: dict[str, Any]
    parameters: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return the stable dict payload exposed to agents and product code."""
        return {
            "agent_name": self.agent_name,
            "status": self.status,
            "message": self.message,
            "output_text": self.output_text,
            "output_files": copy.deepcopy(self.output_files),
            "structured_data": copy.deepcopy(self.structured_data),
            "parameters": copy.deepcopy(self.parameters),
        }


def _strip_code_fence(text: str) -> str:
    """Remove a surrounding markdown code fence when present."""
    stripped = str(text or "").strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_prompt_as_parameters(agent_name: str, prompt: str) -> dict[str, Any]:
    """Parse one `invoke_agent` prompt into expert parameters."""
    stripped = _strip_code_fence(prompt)
    if stripped:
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            nested = payload.get("parameters")
            if isinstance(nested, dict):
                return dict(nested)
            return dict(payload)

    return build_fallback_parameters(agent_name, stripped)


def _resolve_named_files(state: dict[str, Any], names: list[str]) -> list[str]:
    """Resolve legacy file names against recorded workspace file history."""
    available_files = list(state.get("uploaded", []) or state.get("input_files", []))
    available_files.extend(list(state.get("generated", []) or []))
    for history_entry in state.get("uploaded_history", []):
        available_files.extend(list(history_entry.get("files") or []))
    for history_entry in state.get("generated_history", []):
        available_files.extend(list(history_entry.get("files") or []))
    for file_group in state.get("files_history", []):
        available_files.extend(file_group)

    resolved_paths: list[str] = []
    for raw_name in names:
        target_name = str(raw_name or "").strip()
        if not target_name:
            continue
        matched = next(
            (
                str(file_info.get("path", "")).strip()
                for file_info in reversed(available_files)
                if str(file_info.get("name", "")).strip() == target_name
            ),
            "",
        )
        if not matched:
            raise ValueError(f"invoke_agent got an unknown workspace file name: '{target_name}'.")
        resolved_paths.append(matched)
    return resolved_paths


def _all_workspace_paths_exist(paths: list[str]) -> bool:
    """Return whether every normalized workspace path currently exists."""
    try:
        return all(resolve_workspace_path(path).exists() for path in paths)
    except Exception:
        return False


def normalize_invoke_agent_parameters(
    *,
    agent_name: str,
    prompt: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Normalize an `invoke_agent` prompt into expert parameters."""
    normalized = _parse_prompt_as_parameters(agent_name, prompt)
    if "input_path" in normalized or "input_paths" in normalized:
        input_paths = normalize_file_references(
            normalized.get("input_paths", normalized.get("input_path"))
        )
    elif "input_name" in normalized:
        raw_value = normalized.get("input_name")
        if isinstance(raw_value, str):
            input_names = [raw_value]
        elif isinstance(raw_value, list):
            input_names = [str(item) for item in raw_value]
        else:
            raise ValueError("invoke_agent parameters['input_name'] must be a string or a list")
        direct_paths = normalize_file_references(input_names)
        if direct_paths and _all_workspace_paths_exist(direct_paths):
            input_paths = direct_paths
        else:
            input_paths = _resolve_named_files(state, input_names)
        normalized.pop("input_name", None)
    else:
        input_paths = []

    if input_paths:
        for path in input_paths:
            resolved = resolve_workspace_path(path)
            if not resolved.exists():
                raise ValueError(f"invoke_agent got a missing workspace file: '{path}'.")
        normalized["input_paths"] = input_paths
        if len(input_paths) == 1:
            normalized["input_path"] = input_paths[0]

    return validate_expert_parameters(agent_name, normalized)


def _normalize_output_files(
    output_files: list[dict[str, Any]],
    *,
    turn: int | None,
    step: int | None,
    expert_step: int | None,
) -> list[dict[str, Any]]:
    """Normalize expert output file records before saving them into parent state."""
    normalized_files: list[dict[str, Any]] = []
    for file_info in output_files:
        path = str(file_info.get("path", "")).strip()
        if not path:
            continue
        normalized_files.append(
            build_workspace_file_record(
                path,
                description=str(file_info.get("description", "")).strip(),
                source=str(file_info.get("source", "expert")).strip() or "expert",
                name=str(file_info.get("name", "")).strip() or None,
                turn=int(file_info.get("turn", turn)) if file_info.get("turn", turn) is not None else None,
                step=int(file_info.get("step", step)) if file_info.get("step", step) is not None else None,
                expert_step=(
                    int(file_info.get("expert_step", expert_step))
                    if file_info.get("expert_step", expert_step) is not None
                    else None
                ),
            )
        )
    return normalized_files


def _extract_forwardable_state_delta(state_delta: dict[str, Any]) -> dict[str, Any]:
    """Keep only child state keys that are safe to merge back into the parent."""
    return {
        key: value
        for key, value in state_delta.items()
        if not str(key).startswith("_adk") and key not in _NON_FORWARDABLE_STATE_KEYS
    }


def _state_delta_between(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Return keys whose values changed after an ADK AgentTool child run."""
    changed: dict[str, Any] = {}
    for key, value in after.items():
        if before.get(key, _MISSING) != value:
            changed[key] = copy.deepcopy(value)
    return changed


def _restore_non_forwardable_parent_state(
    *,
    tool_context: ToolContext,
    parent_state: dict[str, Any],
    changed_delta: dict[str, Any],
) -> None:
    """Undo direct AgentTool writes that Creative Claw merges explicitly later."""
    restore_delta: dict[str, Any] = {}
    for key in changed_delta:
        if str(key).startswith("_adk") or key in _NON_FORWARDABLE_STATE_KEYS:
            restore_delta[key] = copy.deepcopy(parent_state.get(key))
    if restore_delta:
        tool_context.state.update(restore_delta)


async def _run_agent_tool_expert_session(
    *,
    agent: BaseAgent,
    agent_name: str,
    normalized_parameters: dict[str, Any],
    parent_state: dict[str, Any],
    tool_context: ToolContext,
) -> _ExpertAgentRunResult:
    """Run one expert via ADK AgentTool and collect filtered child state."""
    forwarded_state_delta: dict[str, Any] = {}
    changed_delta: dict[str, Any] = {}
    try:
        tool_context.state.update({"current_parameters": normalized_parameters})
        agent_tool_result = await run_agent_tool(
            agent=agent,
            request=(
                f"Execute delegated expert task for {agent_name}. "
                "Use the parameters stored in session current_parameters."
            ),
            tool_context=tool_context,
        )
        state_after = tool_context.state.to_dict()
        changed_delta = _state_delta_between(parent_state, state_after)
        forwarded_state_delta = _extract_forwardable_state_delta(changed_delta)
        current_output = state_after.get("current_output")
        if not current_output:
            output_text = str(agent_tool_result or "").strip()
            current_output = (
                {
                    "status": "success",
                    "message": f"{agent_name} finished.",
                    "output_text": output_text,
                }
                if output_text
                else {
                    "status": "error",
                    "message": f"{agent_name} did not produce current_output.",
                }
            )
    except Exception as exc:
        changed_delta = _state_delta_between(parent_state, tool_context.state.to_dict())
        current_output = {
            "status": "error",
            "message": f"{agent_name} execution failed: {type(exc).__name__}: {exc}",
        }
    finally:
        _restore_non_forwardable_parent_state(
            tool_context=tool_context,
            parent_state=parent_state,
            changed_delta=changed_delta,
        )
    return _ExpertAgentRunResult(
        current_output=current_output,
        forwarded_state_delta=forwarded_state_delta,
    )


def _build_tool_result(
    *,
    agent_name: str,
    current_output: dict[str, Any],
    forwarded_state_delta: dict[str, Any],
    normalized_parameters: dict[str, Any],
    normalized_files: list[dict[str, Any]],
) -> _ExpertToolResult:
    """Build the tool result returned to the orchestrator model."""
    normalized_output = normalize_expert_output(
        agent_name,
        current_output,
        forwarded_state_delta=forwarded_state_delta,
    )
    structured_data = {
        key: value
        for key, value in normalized_output.items()
        if key not in {"status", "message", "output_text", "output_files"}
    }
    for key, value in forwarded_state_delta.items():
        if key not in _NON_FORWARDABLE_STATE_KEYS and key not in structured_data:
            structured_data[key] = value
    return _ExpertToolResult(
        agent_name=agent_name,
        status=str(normalized_output["status"]),
        message=str(normalized_output["message"]),
        output_text=str(normalized_output.get("output_text", "") or ""),
        output_files=normalized_files,
        structured_data=structured_data,
        parameters=normalized_parameters,
    )


def _build_state_delta(
    *,
    parent_state: dict[str, Any],
    forwarded_state_delta: dict[str, Any],
    agent_name: str,
    normalized_parameters: dict[str, Any],
    current_output: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge one child expert result back into the parent session state."""
    current_output = normalize_expert_output(
        agent_name,
        current_output,
        forwarded_state_delta=forwarded_state_delta,
    )
    current_turn = int(parent_state.get("turn_index", 0) or 0)
    current_step = int(parent_state.get("step", 0) or 0)
    current_expert_step = int(parent_state.get("expert_step", 0) or 0)
    normalized_files = _normalize_output_files(
        current_output.get("output_files", []),
        turn=current_turn,
        step=current_step,
        expert_step=current_expert_step,
    )
    message = str(current_output.get("message", "")).strip()
    if not message:
        message = f"{agent_name} finished without a message."

    inherited_delta = dict(forwarded_state_delta)
    summary_history = list(parent_state.get("summary_history") or [])
    text_history = list(parent_state.get("text_history") or [])
    message_history = list(parent_state.get("message_history") or [])
    files_history = list(parent_state.get("files_history") or [])
    generated = list(parent_state.get("generated") or [])
    expert_history = list(parent_state.get("expert_history") or [])

    tool_result = _build_tool_result(
        agent_name=agent_name,
        current_output=current_output,
        forwarded_state_delta=forwarded_state_delta,
        normalized_parameters=normalized_parameters,
        normalized_files=normalized_files,
    )
    tool_result_payload = tool_result.to_dict()

    state_delta = dict(inherited_delta)
    state_delta.update(
        {
            "current_parameters": normalized_parameters,
            "current_output": current_output,
            "last_output_message": message,
            "last_expert_result": tool_result_payload,
            "expert_history": expert_history + [tool_result_payload],
            "summary_history": summary_history + [f"{agent_name}: {message}"],
            "message_history": message_history + [message],
        }
    )

    output_text = current_output.get("output_text")
    state_delta["text_history"] = text_history + [output_text if output_text else None]

    if normalized_files:
        state_delta["generated"] = generated + normalized_files
        state_delta["new_files"] = normalized_files
        state_delta["files_history"] = files_history + [normalized_files]
    else:
        state_delta["generated"] = generated
        state_delta["new_files"] = []
        state_delta["files_history"] = files_history + [[]]

    return state_delta, tool_result_payload


async def dispatch_expert_call(
    *,
    agent_name: str,
    prompt: str,
    tool_context: ToolContext,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: InMemoryArtifactService,
) -> ExpertInvocationResult:
    """Run one expert and merge the result into the parent state."""
    del app_name, artifact_service
    return await dispatch_expert_request(
        ExpertInvocationRequest(
            agent_name=agent_name,
            prompt=prompt,
            tool_context=tool_context,
            expert_agents=expert_agents,
        )
    )


async def dispatch_expert_request(request: ExpertInvocationRequest) -> ExpertInvocationResult:
    """Run one expert request and merge the result into the parent state."""
    agent_name = request.agent_name
    prompt = request.prompt
    tool_context = request.tool_context
    expert_agents = request.expert_agents
    if agent_name not in expert_agents:
        trace_runtime_event(
            "expert.error",
            {
                "agent_name": agent_name,
                "error": f"Unknown expert. Available experts: {sorted(expert_agents)}",
            },
        )
        raise ValueError(f"invoke_agent got an unknown expert: '{agent_name}'.")

    parent_state = tool_context.state.to_dict()
    normalized_parameters = normalize_invoke_agent_parameters(
        agent_name=agent_name,
        prompt=prompt,
        state=parent_state,
    )
    trace_runtime_event(
        "expert.start",
        {
            "agent_name": agent_name,
            "prompt": prompt,
            "normalized_parameters": normalized_parameters,
            "parent_session": {
                "sid": parent_state.get("sid"),
                "turn_index": parent_state.get("turn_index"),
                "step": parent_state.get("step"),
                "expert_step": parent_state.get("expert_step"),
            },
        },
    )

    agent_run = await _run_agent_tool_expert_session(
        agent=expert_agents[agent_name],
        agent_name=agent_name,
        normalized_parameters=normalized_parameters,
        parent_state=parent_state,
        tool_context=tool_context,
    )

    state_delta, tool_result = _build_state_delta(
        parent_state=parent_state,
        forwarded_state_delta=agent_run.forwarded_state_delta,
        agent_name=agent_name,
        normalized_parameters=normalized_parameters,
        current_output=agent_run.current_output,
    )
    tool_context.state.update(state_delta)
    trace_runtime_event(
        "expert.finish",
        {
            "agent_name": agent_name,
            "status": tool_result.get("status"),
            "message": tool_result.get("message"),
            "output_files": tool_result.get("output_files"),
            "tool_result": tool_result,
        },
    )

    return ExpertInvocationResult(
        agent_name=agent_name,
        normalized_parameters=normalized_parameters,
        current_output=agent_run.current_output,
        state_delta=state_delta,
        tool_result=tool_result,
        assistant_text_streamed=agent_run.assistant_text_streamed,
    )
