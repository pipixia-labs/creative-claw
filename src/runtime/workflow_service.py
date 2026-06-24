"""Workflow runtime for channel-driven Creative Claw execution."""

from __future__ import annotations

import asyncio
import json
import uuid

from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from conf.system import SYS_CONFIG
from src.agents.orchestrator.orchestrator_agent import (
    Orchestrator,
    PPT_ADK_HITL_ENABLED_STATE_KEY,
    PPT_ADK_PENDING_CONFIRMATION_STATE_KEY,
)
from src.logger import logger
from src.productions.design.design_product_manager.brief_form import (
    DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY,
    DESIGN_BRIEF_FORM_STATE_KEY,
)
from src.productions.ppt.schemas import PptAdkConfirmationRequest
from src.runtime.cancellation import TaskCancelledError, get_cancellation_manager
from src.runtime.expert_registry import build_expert_agents
from src.runtime.interaction_language import (
    INTERACTION_LANGUAGE_STATE_KEY,
    LANGUAGE_ZH,
    localized_copy,
    resolve_interaction_language,
)
from src.runtime.models import InboundMessage, WorkflowEvent
from src.runtime.product_results import is_product_confirmation_result
from src.runtime.progress_events import build_progress_metadata, progress_text_from_metadata
from src.runtime.runtime_trace import trace_runtime_event
from src.runtime.step_events import reset_step_event_history, step_event_streaming_active
from src.runtime.workspace import (
    build_workspace_file_record,
    generated_root,
    stage_attachment_into_workspace,
    workspace_relative_path,
    resolve_workspace_path,
    workspace_root,
)

_HELP_TEXT = (
    "CreativeClaw commands:\n"
    "/new - Start a new conversation session\n"
    "/help - Show available commands"
)

_PENDING_PPT_WORKFLOW_STAGES = {
    "awaiting_requirement_confirmation",
    "awaiting_content_plan_confirmation",
}


def _append_turn_file_history(
    history: list[dict[str, object]],
    *,
    turn: int,
    files: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Append one turn-scoped file batch to history when files exist."""
    if not files:
        return history
    updated_history = list(history)
    updated_history.append({"turn": turn, "files": list(files)})
    return updated_history


def _should_preserve_ppt_product_state(state: dict[str, object]) -> bool:
    """Return whether a PPT workflow is paused and should survive the next user turn."""
    workflow_state = state.get("ppt_workflow_state")
    if not isinstance(workflow_state, dict):
        return isinstance(state.get(PPT_ADK_PENDING_CONFIRMATION_STATE_KEY), dict)
    stage = str(workflow_state.get("stage") or "").strip()
    return stage in _PENDING_PPT_WORKFLOW_STAGES or isinstance(
        state.get(PPT_ADK_PENDING_CONFIRMATION_STATE_KEY),
        dict,
    )


def _should_preserve_design_product_state(state: dict[str, object]) -> bool:
    """Return whether a Design brief form is waiting for submitted answers."""
    pending_task = str(state.get(DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY) or "").strip()
    return bool(pending_task)


def _collect_persistent_product_state(state: dict[str, object]) -> dict[str, object]:
    """Collect product-owned state that must survive a channel message reset."""
    persistent_state: dict[str, object] = {}

    if _should_preserve_ppt_product_state(state):
        persistent_state.update(
            {
                key: value
                for key, value in state.items()
                if key.startswith("ppt_") and value is not None
            }
        )
        if state.get(INTERACTION_LANGUAGE_STATE_KEY) is not None:
            persistent_state[INTERACTION_LANGUAGE_STATE_KEY] = state[INTERACTION_LANGUAGE_STATE_KEY]

    if _should_preserve_design_product_state(state):
        persistent_state.update(
            {
                key: value
                for key, value in state.items()
                if key.startswith("design_product") and value is not None
            }
        )
        if state.get(DESIGN_BRIEF_FORM_STATE_KEY) is not None:
            persistent_state[DESIGN_BRIEF_FORM_STATE_KEY] = state[DESIGN_BRIEF_FORM_STATE_KEY]
        if state.get(INTERACTION_LANGUAGE_STATE_KEY) is not None:
            persistent_state[INTERACTION_LANGUAGE_STATE_KEY] = state[INTERACTION_LANGUAGE_STATE_KEY]
        persistent_state["product_line"] = "design"

    if persistent_state and state.get("last_product_result") is not None:
        persistent_state["last_product_result"] = state["last_product_result"]
    return persistent_state


def _format_exception_summary(exc: Exception) -> str:
    """Return a concise exception summary that always includes the exception type."""
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def _contains_form_answers(text: str) -> bool:
    """Return whether the inbound text contains a submitted Web question form."""
    normalized = str(text or "").lower()
    return "[cc-form-answers" in normalized and "[/cc-form-answers]" in normalized


def _build_progress_event(
    debug_detail: str,
    *,
    session_id: str,
    stage: str,
    stage_title: str | None = None,
    user_title: str | None = None,
    user_detail: str | None = None,
    turn_index: int | None = None,
    activity_sequence: int | None = None,
    interaction_language: str = "",
) -> WorkflowEvent:
    """Build one progress event with user-facing copy and debug details."""
    metadata = build_progress_metadata(
        session_id=session_id,
        stage=stage,
        debug_title=stage_title or "",
        debug_detail=debug_detail,
        user_title=user_title,
        user_detail=user_detail,
        turn_index=turn_index,
        activity_sequence=activity_sequence,
        interaction_language=interaction_language,
    )
    return WorkflowEvent(event_type="status", text=progress_text_from_metadata(metadata), metadata=metadata)


def _build_orchestration_progress_event(
    step_event: dict[str, str],
    *,
    session_id: str,
    turn_index: int | None = None,
    activity_sequence: int | None = None,
    interaction_language: str = "",
) -> WorkflowEvent:
    """Convert one structured orchestrator step event into a progress event."""
    stage = str(step_event.get("stage", "")).strip() or "in_progress"
    title = str(step_event.get("debug_title") or step_event.get("title") or "").strip()
    detail = str(step_event.get("debug_detail") or step_event.get("detail") or "").strip()
    return _build_progress_event(
        detail,
        session_id=session_id,
        stage=stage,
        stage_title=title,
        user_title=str(step_event.get("user_title") or "").strip() or None,
        user_detail=str(step_event.get("user_detail") or "").strip() or None,
        turn_index=turn_index,
        activity_sequence=activity_sequence,
        interaction_language=interaction_language,
    )


class CreativeClawRuntime:
    """Run Creative Claw workflow for normalized channel messages."""

    def __init__(self, *, llm_model: str = "") -> None:
        self.session_service = InMemorySessionService()
        self.artifact_service = InMemoryArtifactService()
        self._session_keys: dict[str, str] = {}
        self.expert_agents = build_expert_agents(app_name=SYS_CONFIG.app_name)
        self.llm_model = str(llm_model or "").strip()

        self.workspace_root = workspace_root()
        self.generated_dir = generated_root()

    async def run_message(self, inbound: InboundMessage):
        """Execute one inbound message and yield workflow events."""
        command = inbound.text.strip().lower()
        if command == "/help":
            yield WorkflowEvent(
                event_type="final",
                text=_HELP_TEXT,
                metadata={"user_id": inbound.sender_id or SYS_CONFIG.user_id_default},
            )
            return
        if command == "/new":
            user_id, session_id = await self.reset_session(inbound)
            yield WorkflowEvent(
                event_type="final",
                text="Started a new conversation session.",
                metadata={
                    "session_id": session_id,
                    "user_id": user_id,
                    "display_style": "final",
                },
            )
            return

        user_id, session_id = await self._ensure_session(inbound)
        current_session = await self.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        interaction_language = resolve_interaction_language(
            explicit=inbound.metadata.get("interaction_language", inbound.metadata.get("language", "")),
            state=current_session.state if current_session is not None else None,
            texts=(inbound.text,),
            default=LANGUAGE_ZH if _contains_form_answers(inbound.text) else "en",
        )
        run_id = str(inbound.metadata.get("run_id") or "").strip()
        current_turn: int | None = None
        try:
            if run_id:
                cancellation = get_cancellation_manager()
                record = cancellation.bind_runtime_session(run_id, session_id)
                if record is not None and record.requested:
                    cancellation.request_cancel_by_session(session_id, reason=record.reason)
                    logger.info(
                        "Pending cancellation applied before workflow start: run_id={} session_id={}",
                        run_id,
                        session_id,
                    )
                    raise TaskCancelledError(session_id, reason=record.reason)

            current_turn = await self._next_turn_index(user_id, session_id)
            trace_runtime_event(
                "workflow.user_task",
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "turn_index": current_turn,
                    "channel": inbound.channel,
                    "chat_id": inbound.chat_id,
                    "sender_id": inbound.sender_id,
                    "text": inbound.text,
                    "attachments": [
                        {
                            "name": attachment.name,
                            "mime_type": attachment.mime_type,
                            "description": attachment.description,
                        }
                        for attachment in inbound.attachments
                    ],
                    "metadata": inbound.metadata,
                },
            )

            yield _build_progress_event(
                "Workflow started.",
                session_id=session_id,
                stage="started",
                turn_index=current_turn,
                activity_sequence=1,
                interaction_language=interaction_language,
            )
            reset_step_event_history(session_id=session_id, turn_index=current_turn)
            activity_sequence = 1
            for attachment in inbound.attachments:
                activity_sequence += 1
                yield _build_progress_event(
                    f"Received attachment: {attachment.name}",
                    session_id=session_id,
                    stage="attachment_received",
                    turn_index=current_turn,
                    activity_sequence=activity_sequence,
                    interaction_language=interaction_language,
                )
            if _contains_form_answers(inbound.text):
                activity_sequence += 1
                form_language = interaction_language or LANGUAGE_ZH
                current_session = await self.session_service.get_session(
                    app_name=SYS_CONFIG.app_name,
                    user_id=user_id,
                    session_id=session_id,
                )
                if current_session is not None:
                    form_language = str(
                        current_session.state.get(INTERACTION_LANGUAGE_STATE_KEY) or LANGUAGE_ZH
                    )
                form_answer_detail = localized_copy(
                    form_language,
                    en="Received the requirements form and is continuing the design generation.",
                    zh="已收到需求确认表单，正在继续生成设计方案。",
                )
                yield _build_progress_event(
                    form_answer_detail,
                    session_id=session_id,
                    stage="design_planning",
                    user_title=localized_copy(
                        form_language,
                        en="Reviewing your answers",
                        zh="正在检查你的回答",
                    ),
                    user_detail=form_answer_detail,
                    turn_index=current_turn,
                    activity_sequence=activity_sequence,
                    interaction_language=interaction_language,
                )

            try:
                await self._set_initial_state(user_id, session_id, inbound, turn_index=current_turn)
            except Exception as exc:
                error_summary = _format_exception_summary(exc)
                error_text = f"Init state failed (session_id={session_id}): {error_summary}"
                logger.opt(exception=exc).error(
                    "Init state failed: session_id={} channel={} sender_id={} error_summary={}",
                    session_id,
                    inbound.channel,
                    inbound.sender_id or SYS_CONFIG.user_id_default,
                    error_summary,
                )
                yield WorkflowEvent(
                    event_type="error",
                    text=error_text,
                    metadata={"session_id": session_id, "turn_index": current_turn},
                )
                return

            orchestrator_agent = Orchestrator(
                session_service=self.session_service,
                artifact_service=self.artifact_service,
                expert_agents=self.expert_agents,
                app_name=SYS_CONFIG.app_name,
                save_dir=str(self.generated_dir),
                llm_model=self.llm_model,
            )
            orchestrator_agent.uid = user_id
            orchestrator_agent.sid = session_id

            final_summary = "task workflow has started."
            current_session = await self.session_service.get_session(
                app_name=SYS_CONFIG.app_name,
                user_id=user_id,
                session_id=session_id,
            )
            logger.trace(
                "session.state (Orchestrator): {}",
                json.dumps(current_session.state, indent=2, ensure_ascii=False),
            )

            step_result = await orchestrator_agent.run_until_done()
            final_response = str(step_result.get("final_response", "") or "").strip()
            orchestration_events = list(step_result.get("new_orchestration_events", []))
            if step_event_streaming_active():
                orchestration_events = []
            final_summary = (
                step_result.get("final_summary")
                or step_result.get("last_output_message")
                or final_summary
            )

            for step_event in orchestration_events:
                activity_sequence += 1
                yield _build_orchestration_progress_event(
                    step_event,
                    session_id=session_id,
                    turn_index=current_turn,
                    activity_sequence=activity_sequence,
                    interaction_language=interaction_language,
                )

            final_event = await self._build_final_event(
                user_id,
                session_id,
                final_response or final_summary,
                turn_index=current_turn,
            )
            if step_result.get("assistant_text_streamed"):
                final_event.metadata["disable_stream"] = True
            yield final_event
        except TaskCancelledError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_summary = _format_exception_summary(exc)
            error_text = f"Workflow failed (session_id={session_id}): {error_summary}"
            logger.opt(exception=exc).error(
                "Workflow failed: session_id={} error_summary={}",
                session_id,
                error_summary,
            )
            error_metadata: dict[str, object] = {"session_id": session_id}
            if current_turn is not None:
                error_metadata["turn_index"] = current_turn
            yield WorkflowEvent(
                event_type="error",
                text=error_text,
                metadata=error_metadata,
            )
        finally:
            if run_id:
                get_cancellation_manager().complete_run(run_id)

    async def reset_session(self, inbound: InboundMessage) -> tuple[str, str]:
        """Force-create a fresh ADK session for the current channel conversation."""
        user_id = inbound.sender_id or SYS_CONFIG.user_id_default
        session_key = inbound.session_key
        session_id = f"{SYS_CONFIG.session_id_default_prefix}{uuid.uuid4()}"
        await self.session_service.create_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
            state={},
        )
        self._session_keys[session_key] = session_id
        logger.info("Reset session for {} -> {}", session_key, session_id)
        return user_id, session_id

    async def _ensure_session(self, inbound: InboundMessage) -> tuple[str, str]:
        """Create or reuse one ADK session for a logical channel conversation."""
        user_id = inbound.sender_id or SYS_CONFIG.user_id_default
        session_key = inbound.session_key
        session_id = self._session_keys.get(session_key)

        if session_id:
            existing_session = await self.session_service.get_session(
                app_name=SYS_CONFIG.app_name,
                user_id=user_id,
                session_id=session_id,
            )
            if existing_session is not None:
                return user_id, session_id

        session_id = f"{SYS_CONFIG.session_id_default_prefix}{uuid.uuid4()}"
        await self.session_service.create_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
            state={},
        )
        self._session_keys[session_key] = session_id
        logger.info("Created session for {} -> {}", session_key, session_id)
        return user_id, session_id

    async def _next_turn_index(self, user_id: str, session_id: str) -> int:
        """Return the next turn index for one ADK session without mutating state."""
        current_session = await self.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if current_session is None:
            return 1
        previous_turn = int(current_session.state.get("turn_index", 0) or 0)
        return previous_turn + 1

    async def _set_initial_state(
        self,
        user_id: str,
        session_id: str,
        inbound: InboundMessage,
        *,
        turn_index: int | None = None,
    ) -> None:
        """Append the normalized user message and attachments to session state."""
        current_session = await self.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if current_session is None:
            raise ValueError(f"Session {session_id} not found for user {user_id}")

        persistent_product_state = _collect_persistent_product_state(current_session.state)
        state_delta = {key: None for key in current_session.state.keys()}
        previous_turn = int(current_session.state.get("turn_index", 0) or 0)
        current_turn = turn_index if turn_index is not None else previous_turn + 1
        previous_uploaded = list(current_session.state.get("uploaded") or current_session.state.get("input_files") or [])
        previous_generated = list(current_session.state.get("generated") or [])
        uploaded_history = _append_turn_file_history(
            list(current_session.state.get("uploaded_history") or []),
            turn=previous_turn,
            files=previous_uploaded,
        )
        generated_history = _append_turn_file_history(
            list(current_session.state.get("generated_history") or []),
            turn=previous_turn,
            files=previous_generated,
        )

        state_delta["app_name"] = SYS_CONFIG.app_name
        state_delta["uid"] = user_id
        state_delta["sid"] = session_id
        state_delta["channel"] = inbound.channel
        state_delta["chat_id"] = inbound.chat_id
        state_delta["sender_id"] = inbound.sender_id or user_id
        requested_product_line = str(inbound.metadata.get("product_line", "") or "").strip()
        state_delta["product_line"] = requested_product_line or (
            str(current_session.state.get("product_line", "") or "").strip()
            if persistent_product_state
            else ""
        )
        state_delta["product_line_options"] = inbound.metadata
        state_delta["user_prompt"] = inbound.text
        state_delta[INTERACTION_LANGUAGE_STATE_KEY] = resolve_interaction_language(
            explicit=inbound.metadata.get("interaction_language", inbound.metadata.get("language", "")),
            state=current_session.state,
            texts=(inbound.text,),
        )
        state_delta["step"] = current_session.state.get("step", 0)
        state_delta["expert_step"] = current_session.state.get("expert_step", 0)
        state_delta["turn_index"] = current_turn
        state_delta["input_files"] = []
        state_delta["uploaded"] = []
        state_delta["uploaded_history"] = uploaded_history
        state_delta["generated"] = []
        state_delta["generated_history"] = generated_history
        state_delta["workflow_status"] = "running"
        state_delta["final_summary"] = ""
        state_delta["final_response"] = ""
        state_delta["final_file_paths"] = []
        state_delta["last_output_message"] = ""
        state_delta["last_orchestrator_response"] = ""
        state_delta["current_parameters"] = {}
        state_delta["current_output"] = None
        state_delta["last_expert_result"] = None
        state_delta["expert_history"] = []
        raw_adk_hitl_enabled = inbound.metadata.get("adk_hitl_enabled", inbound.metadata.get("adk_hitl", True))
        state_delta[PPT_ADK_HITL_ENABLED_STATE_KEY] = (
            raw_adk_hitl_enabled
            if isinstance(raw_adk_hitl_enabled, bool)
            else str(raw_adk_hitl_enabled or "").strip().lower() not in {"0", "false", "no", "off"}
        )

        for index, attachment in enumerate(inbound.attachments, start=1):
            saved_path = stage_attachment_into_workspace(
                attachment.path,
                channel=inbound.channel,
                session_id=session_id,
                turn_index=current_turn,
                attachment_index=index,
                preferred_name=attachment.name,
            )
            file_name = attachment.name or saved_path.name
            description = attachment.description or f"user input attachment {index}"
            record = build_workspace_file_record(
                saved_path,
                description=description,
                source="channel",
                name=file_name,
                turn=current_turn,
            )
            state_delta["input_files"].append(record)
            state_delta["uploaded"].append(record)
            state_delta["user_prompt"] += (
                f"\nInput file {index}: name={file_name}, "
                f"path={workspace_relative_path(saved_path)}"
            )

        existing_files_history = current_session.state.get("files_history", [])
        state_delta["files_history"] = (
            existing_files_history + [state_delta["uploaded"]]
            if state_delta["uploaded"]
            else existing_files_history
        )
        state_delta["summary_history"] = current_session.state.get("summary_history", [])
        state_delta["text_history"] = current_session.state.get("text_history", [])
        state_delta["message_history"] = current_session.state.get("message_history", [])
        state_delta["new_files"] = state_delta["uploaded"]
        state_delta["orchestration_events"] = current_session.state.get("orchestration_events", [])
        state_delta.update(persistent_product_state)
        structured_ppt_confirmation_response = inbound.metadata.get("ppt_confirmation_response")
        state_delta["ppt_confirmation_response"] = (
            dict(structured_ppt_confirmation_response)
            if isinstance(structured_ppt_confirmation_response, dict)
            else None
        )

        event = Event(
            author="channel_gateway",
            content=Content(
                role="user",
                parts=[
                    Part(
                        text=(
                            f"New user input task: {state_delta['user_prompt']}, "
                            "you can start to analyze."
                        )
                    )
                ],
            ),
            actions=EventActions(state_delta=state_delta),
        )
        await self.session_service.append_event(current_session, event)


    async def _build_final_event(
        self,
        user_id: str,
        session_id: str,
        final_summary: str,
        *,
        turn_index: int | None = None,
    ) -> WorkflowEvent:
        """Build the final workflow event from the current session state."""
        final_session = await self.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if final_session is None:
            return WorkflowEvent(
                event_type="error",
                text="Workflow ended without a valid session.",
                metadata={"session_id": session_id},
            )

        explicit_final_file_paths = final_session.state.get("final_file_paths")
        artifact_paths = _resolve_final_artifact_paths(explicit_final_file_paths)

        state_response = final_session.state.get("final_response")
        if state_response:
            final_summary = state_response
        else:
            text_history = final_session.state.get("text_history") or []
            if text_history and text_history[-1]:
                final_summary = text_history[-1]
            else:
                state_summary = final_session.state.get("final_summary")
                if state_summary:
                    final_summary = state_summary
                summary_history = final_session.state.get("summary_history") or []
                if summary_history and not state_summary:
                    history_text = "\n".join(f"- {summary}" for summary in summary_history)
                    final_summary = f"{final_summary}\nExecution history:\n{history_text}"

        final_turn_index = turn_index if turn_index is not None else int(final_session.state.get("turn_index", 0) or 0)
        metadata: dict[str, object] = {
            "session_id": session_id,
            "turn_index": final_turn_index,
            "display_style": "final",
        }
        ppt_confirmation_request = _build_ppt_confirmation_request_metadata(final_session.state)
        if ppt_confirmation_request:
            metadata["ppt_confirmation_request"] = ppt_confirmation_request
        return WorkflowEvent(
            event_type="final",
            text=final_summary,
            artifact_paths=artifact_paths,
            metadata=metadata,
        )


def _resolve_final_artifact_paths(selected_paths: object) -> list[str]:
    """Resolve one explicit final-file selection into absolute artifact paths."""
    if selected_paths is None:
        return []

    artifact_paths: list[str] = []
    seen_paths: set[str] = set()
    if not isinstance(selected_paths, list):
        return artifact_paths

    for path_value in selected_paths:
        if not isinstance(path_value, str):
            continue
        raw_path = path_value.strip()
        if not raw_path:
            continue
        try:
            resolved = resolve_workspace_path(raw_path).resolve()
        except Exception:
            continue
        if not resolved.exists() or not resolved.is_file():
            continue
        resolved_text = str(resolved)
        if resolved_text in seen_paths:
            continue
        seen_paths.add(resolved_text)
        artifact_paths.append(resolved_text)
    return artifact_paths


def _build_ppt_confirmation_request_metadata(state: dict[str, object]) -> dict[str, object] | None:
    """Build browser-facing PPT confirmation metadata from runtime state."""
    pending_adk_request = state.get(PPT_ADK_PENDING_CONFIRMATION_STATE_KEY)
    if isinstance(pending_adk_request, dict):
        payload = pending_adk_request.get("payload")
        if isinstance(payload, dict):
            return dict(payload)

    product_result = _select_ppt_confirmation_product_result(state)
    if product_result is None:
        return None

    workflow_state = state.get("ppt_workflow_state")
    workflow_state = workflow_state if isinstance(workflow_state, dict) else {}
    confirmation_request = product_result.get("confirmation_request")
    confirmation_request = confirmation_request if isinstance(confirmation_request, dict) else {}
    stage = str(product_result.get("status") or workflow_state.get("stage") or "").strip()
    confirmation_type = str(confirmation_request.get("type") or "").strip()
    if not confirmation_type:
        confirmation_type = "content_plan" if "content_plan" in stage else "requirement"

    try:
        return PptAdkConfirmationRequest(
            workflow_id=str(workflow_state.get("workflow_id") or confirmation_request.get("workflow_id") or ""),
            confirmation_id=str(workflow_state.get("confirmation_id") or ""),
            stage=stage,
            confirmation_type=confirmation_type,
            message=str(product_result.get("message") or ""),
            summary_markdown=str(confirmation_request.get("summary_markdown") or ""),
            expected_user_action=str(confirmation_request.get("expected_user_action") or ""),
        ).model_dump(mode="json")
    except Exception:
        return None


def _select_ppt_confirmation_product_result(state: dict[str, object]) -> dict[str, object] | None:
    """Return the current PPT product confirmation result, if one is active."""
    for key in ("ppt_product_result", "last_product_result", "current_output"):
        candidate = state.get(key)
        if is_product_confirmation_result(candidate):
            return candidate
    return None
