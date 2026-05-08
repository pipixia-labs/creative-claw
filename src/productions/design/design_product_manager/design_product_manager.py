"""ADK-native DesignProductManager for Creative Claw design tasks."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.artifacts import BaseArtifactService, InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai.types import Content, Part
from pydantic import PrivateAttr

from conf.llm import build_llm
from conf.path import PROJECT_PATH
from src.productions.design.design_product_manager.product_design_skills import (
    ProductDesignSkillRegistry,
)
from src.productions.design.design_product_manager.validation import validate_design_artifacts
from src.runtime.code_artifacts import generate_code_artifact
from src.runtime.step_events import append_orchestration_step_event
from src.runtime.tool_context_artifact_service import ToolContextArtifactService
from src.runtime.workspace import (
    build_generated_output_path,
    build_workspace_file_record,
    resolve_workspace_path,
    workspace_relative_path,
)

DESIGN_PRODUCT_RESULT_SCHEMA_VERSION = "design-product-result-v2"
DESIGN_PRODUCT_RESULT_STATE_KEY = "design_product_result"
DESIGN_PRODUCT_REQUEST_STATE_KEY = "design_product_request"
DESIGN_PRODUCT_PROGRESS_STATE_KEY = "design_product_progress"
DESIGN_PRODUCT_SKILLS_STATE_KEY = "product_design_skills"
DESIGN_PRODUCT_ACTIVE_SKILL_STATE_KEY = "active_product_design_skill"


class DesignProductManager(LlmAgent):
    """ADK LlmAgent that owns design product-line tasks."""

    _project_root: Path = PrivateAttr()
    _skill_registry: ProductDesignSkillRegistry = PrivateAttr()

    def __init__(
        self,
        project_root: str | Path | None = None,
        skills_dir: str | Path | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the DesignProductManager as the design product-line agent."""
        provided_tools = kwargs.pop("tools", None)
        super().__init__(
            name=kwargs.pop("name", "DesignProductManager"),
            model=kwargs.pop("model", build_llm()),
            description=kwargs.pop("description", "Owns Creative Claw design product tasks."),
            instruction=kwargs.pop("instruction", type(self).build_instruction()),
            tools=provided_tools or [],
            **kwargs,
        )
        self._project_root = Path(project_root or PROJECT_PATH).resolve()
        self._skill_registry = ProductDesignSkillRegistry(
            project_root=self._project_root,
            skills_dir=skills_dir,
        )
        if provided_tools is None:
            self.tools = [
                self.list_product_design_skills,
                self.read_product_design_skill,
                self.emit_design_progress,
                self.generate_design_artifact,
                self.save_design_artifact,
                self.validate_design_artifact,
                self.register_design_delivery,
            ]

    @property
    def project_root(self) -> Path:
        """Return the project root used by this product manager."""
        return self._project_root

    @property
    def skill_registry(self) -> ProductDesignSkillRegistry:
        """Return the private product-design skill registry."""
        return self._skill_registry

    @staticmethod
    def build_instruction() -> str:
        """Return the DesignProductManager agent instruction."""
        return """
You are Creative Claw's DesignProductManager.

# Role
Own design product tasks end to end. You are not a thin wrapper around the orchestrator. The orchestrator only hands you the user's design request and relays your progress, status, and final result.

# Private skills
- Use only your private product-design skills, exposed through `list_product_design_skills` and `read_product_design_skill`.
- Private skills live under `skills/product-design-skills/<skill-name>/SKILL.md`.
- Do not ask the orchestrator to read design skills for you.
- Select the most relevant private skill yourself. If no private skill fits, proceed with your own design judgment and record that assumption.

# Workflow
1. Call `emit_design_progress` when you start.
2. Call `list_product_design_skills`.
3. Read the best matching skill with `read_product_design_skill` when a skill is useful.
4. Decide whether the task has enough information. If it does not, return a clarification result through `register_design_delivery` without generating a file.
5. Generate or save one design artifact. Prefer `generate_design_artifact` for HTML/code-backed artifacts; use `save_design_artifact` only when you already have complete file content.
6. Validate generated files with `validate_design_artifact`.
7. Finish by calling `register_design_delivery`.

# Design scope
You own websites, dashboards, landing pages, app screens, posters, cards, HTML decks, interactive tools, and other code-backed design artifacts when the user asks for a design deliverable. Do not route PPTX delivery here; PPTX belongs to the PPT product line.

# Progress and status
- Write progress at major stages: skill discovery, skill read, brief decision, generation, validation, delivery.
- Use concise status messages that the orchestrator can show directly to the user.

# Final result
Always call `register_design_delivery` before finishing. It must contain a user-facing reply, the product status, and any final file paths.
""".strip()

    async def run_product_request(
        self,
        *,
        task: str,
        inputs: list[Any] | None = None,
        output: dict[str, Any] | None = None,
        tool_context: ToolContext | None = None,
        app_name: str = "creative_claw",
        artifact_service: BaseArtifactService | None = None,
    ) -> dict[str, Any]:
        """Run one design product request through this LlmAgent."""
        if tool_context is None:
            return _error_result("DesignProductManager requires tool context.")

        clean_task = str(task or "").strip()
        if not clean_task:
            return _error_result("DesignProductManager requires a non-empty task.")
        if not hasattr(tool_context, "_invocation_context"):
            return _error_result("DesignProductManager requires an ADK invocation context.")

        invocation_context = tool_context._invocation_context
        child_session_service = InMemorySessionService()
        child_artifact_service = _resolve_child_artifact_service(
            tool_context=tool_context,
            fallback_service=artifact_service or InMemoryArtifactService(),
        )
        child_runner = _build_child_runner(
            agent=self,
            app_name=app_name,
            session_service=child_session_service,
            artifact_service=child_artifact_service,
            invocation_context=invocation_context,
        )
        child_state = _copy_state(tool_context.state)
        child_state[DESIGN_PRODUCT_REQUEST_STATE_KEY] = {
            "task": clean_task,
            "inputs": list(inputs or []),
            "output": dict(output or {}),
        }

        try:
            child_session = await child_session_service.create_session(
                app_name=app_name,
                user_id=invocation_context.user_id,
                state=child_state,
            )
            async for _event in child_runner.run_async(
                user_id=child_session.user_id,
                session_id=child_session.id,
                new_message=Content(
                    role="user",
                    parts=[
                        Part(
                            text=_build_design_product_user_message(
                                task=clean_task,
                                inputs=list(inputs or []),
                                output=dict(output or {}),
                            )
                        )
                    ],
                ),
            ):
                pass
            final_session = await child_session_service.get_session(
                app_name=app_name,
                user_id=child_session.user_id,
                session_id=child_session.id,
            )
            final_state = final_session.state if final_session is not None else child_state
            result = final_state.get(DESIGN_PRODUCT_RESULT_STATE_KEY)
            if not isinstance(result, dict):
                result = _error_result(
                    "DesignProductManager finished without registering a design delivery."
                )
            _copy_design_state_back(source=final_state, target=tool_context.state)
            return result
        except Exception as exc:
            result = _error_result(f"DesignProductManager failed: {type(exc).__name__}: {exc}")
            tool_context.state[DESIGN_PRODUCT_RESULT_STATE_KEY] = result
            tool_context.state["current_output"] = result
            return result
        finally:
            await child_runner.close()

    def list_product_design_skills(self, tool_context: ToolContext) -> dict[str, Any]:
        """List private product-design skills available to this product manager."""
        skills = [skill.to_dict() for skill in self.skill_registry.list_skills()]
        tool_context.state[DESIGN_PRODUCT_SKILLS_STATE_KEY] = skills
        return {
            "status": "success",
            "skills": skills,
            "count": len(skills),
        }

    def read_product_design_skill(
        self,
        name: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Read one private product-design skill."""
        content = self.skill_registry.read_skill(name)
        payload = {
            "name": str(name or "").strip(),
            "content": content,
        }
        tool_context.state[DESIGN_PRODUCT_ACTIVE_SKILL_STATE_KEY] = payload
        return {
            "status": "success",
            **payload,
        }

    def emit_design_progress(
        self,
        stage: str,
        status: str,
        message: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Record one design-product progress event."""
        clean_stage = str(stage or "design_product").strip() or "design_product"
        clean_status = str(status or "in_progress").strip() or "in_progress"
        clean_message = str(message or "").strip() or "Design product manager is working."
        progress_item = {
            "stage": clean_stage,
            "status": clean_status,
            "message": clean_message,
        }
        progress = list(tool_context.state.get(DESIGN_PRODUCT_PROGRESS_STATE_KEY) or [])
        progress.append(progress_item)
        tool_context.state[DESIGN_PRODUCT_PROGRESS_STATE_KEY] = progress
        append_orchestration_step_event(
            tool_context.state,
            title="Design Product",
            detail=f"Status: {clean_status}\n{clean_message}",
            stage=clean_stage,
        )
        return {
            "status": "success",
            "progress": progress_item,
        }

    async def generate_design_artifact(
        self,
        prompt: str,
        language: str,
        output_path: str,
        context_files: list[str],
        constraints: list[str],
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Generate one code-backed design artifact."""
        result = await generate_code_artifact(
            tool_context,
            prompt=str(prompt or "").strip(),
            language=str(language or "html").strip() or "html",
            output_path=str(output_path or "").strip(),
            context_files=[str(item) for item in context_files or []],
            constraints=[str(item) for item in constraints or []],
            output_type="design",
            output_description="Design artifact generated by DesignProductManager.",
            output_source="design_product_manager",
        )
        if str(result.get("status", "")).strip().lower() == "success":
            result["output_files"] = _record_output_files(
                tool_context.state,
                list(result.get("output_files") or []),
            )
        tool_context.state["design_product_generation"] = result
        return result

    def save_design_artifact(
        self,
        file_name: str,
        content: str,
        description: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Save one complete design artifact file into the workspace."""
        language_extension = Path(str(file_name or "")).suffix or ".html"
        output_path = build_generated_output_path(
            session_id=str(tool_context.state.get("sid") or "design"),
            turn_index=int(tool_context.state.get("turn_index", 0) or 0),
            step=int(tool_context.state.get("step", 0) or 0) + 1,
            output_type="design",
            index=len(list(tool_context.state.get("generated") or [])),
            extension=language_extension,
        )
        output_path.write_text(str(content or "").rstrip() + "\n", encoding="utf-8")
        record = build_workspace_file_record(
            output_path,
            description=str(description or "Design artifact generated by DesignProductManager.").strip(),
            source="design_product_manager",
            turn=int(tool_context.state.get("turn_index", 0) or 0),
            step=int(tool_context.state.get("step", 0) or 0),
            expert_step=int(tool_context.state.get("expert_step", 0) or 0),
        )
        output_files = _record_output_files(tool_context.state, [record])
        result = {
            "status": "success",
            "message": f"Saved design artifact at {record['path']}.",
            "output_path": record["path"],
            "output_files": output_files,
            "language": language_extension.lstrip(".") or "html",
        }
        tool_context.state["design_product_generation"] = result
        return result

    def validate_design_artifact(
        self,
        paths: list[str],
        browser_preview: bool,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Validate generated design artifact files."""
        validations = validate_design_artifacts(
            [str(path) for path in paths or []],
            browser_preview=bool(browser_preview),
        )
        tool_context.state["design_product_validation"] = validations
        has_error = any(str(item.get("status", "")).lower() == "error" for item in validations)
        return {
            "status": "error" if has_error else "success",
            "validations": validations,
        }

    def register_design_delivery(
        self,
        status: str,
        reply_text: str,
        final_file_paths: list[str],
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Register the final design product result for orchestrator delivery."""
        normalized_paths = _normalize_final_paths(final_file_paths)
        result = {
            "result_schema_version": DESIGN_PRODUCT_RESULT_SCHEMA_VERSION,
            "status": str(status or "success").strip() or "success",
            "product_line": "design",
            "message": str(reply_text or "").strip() or "Design product task completed.",
            "final_file_paths": normalized_paths,
            "progress": list(tool_context.state.get(DESIGN_PRODUCT_PROGRESS_STATE_KEY) or []),
            "active_skill": tool_context.state.get(DESIGN_PRODUCT_ACTIVE_SKILL_STATE_KEY) or {},
            "generation": tool_context.state.get("design_product_generation") or {},
            "validation": tool_context.state.get("design_product_validation") or [],
            "output_files": _file_records_for_paths(normalized_paths, state=tool_context.state),
        }
        tool_context.state[DESIGN_PRODUCT_RESULT_STATE_KEY] = result
        tool_context.state["current_output"] = result
        tool_context.state["last_product_result"] = result
        tool_context.state["final_response"] = result["message"]
        tool_context.state["final_file_paths"] = normalized_paths
        tool_context.state["last_output_message"] = result["message"]
        append_orchestration_step_event(
            tool_context.state,
            title="Design Product",
            detail=f"Status: {result['status']}\n{result['message']}",
            stage="finalizing",
        )
        return result


def _build_design_product_user_message(
    *,
    task: str,
    inputs: list[Any],
    output: dict[str, Any],
) -> str:
    """Build the explicit user message for a design product run."""
    return "\n".join(
        [
            "Handle this Creative Claw design product request end to end.",
            "",
            "# User task",
            task,
            "",
            "# Inputs",
            repr(inputs),
            "",
            "# Output request",
            repr(output),
            "",
            "You own skill selection, design decisions, generation, validation, progress, and final registration.",
            "Always call register_design_delivery before your final response.",
        ]
    )


def _error_result(message: str) -> dict[str, Any]:
    """Build a JSON-safe design product error result."""
    return {
        "result_schema_version": DESIGN_PRODUCT_RESULT_SCHEMA_VERSION,
        "status": "error",
        "product_line": "design",
        "message": message,
        "final_file_paths": [],
        "progress": [],
        "active_skill": {},
        "generation": {},
        "validation": [],
        "output_files": [],
    }


def _copy_state(state: Any) -> dict[str, Any]:
    """Return a deep copy of an ADK state object or plain dict."""
    if hasattr(state, "to_dict"):
        return copy.deepcopy(state.to_dict())
    return copy.deepcopy(dict(state))


def _resolve_child_artifact_service(
    *,
    tool_context: ToolContext,
    fallback_service: BaseArtifactService,
) -> BaseArtifactService:
    """Pick the artifact service for the internal DesignProductManager runner."""
    required_methods = ("save_artifact", "load_artifact", "list_artifacts")
    if all(hasattr(tool_context, method_name) for method_name in required_methods):
        return ToolContextArtifactService(tool_context)
    return fallback_service


def _build_child_runner(
    *,
    agent: LlmAgent,
    app_name: str,
    session_service: InMemorySessionService,
    artifact_service: BaseArtifactService,
    invocation_context: Any,
) -> Runner:
    """Create a child ADK runner for the design product manager."""
    child_plugins = getattr(getattr(invocation_context, "plugin_manager", None), "plugins", None)
    runner_kwargs = {
        "app_name": app_name,
        "session_service": session_service,
        "artifact_service": artifact_service,
        "memory_service": InMemoryMemoryService(),
        "credential_service": getattr(invocation_context, "credential_service", None),
    }
    if child_plugins:
        runner_kwargs["app"] = App(
            name=app_name,
            root_agent=agent,
            plugins=list(child_plugins),
        )
    else:
        runner_kwargs["agent"] = agent
    return Runner(**runner_kwargs)


def _copy_design_state_back(*, source: Any, target: Any) -> None:
    """Copy design-product state from a child runner back to the parent state."""
    source_state = dict(source)
    for key, value in source_state.items():
        if (
            key.startswith("design_product")
            or key.startswith("product_design")
            or key in {"current_output", "last_product_result", "final_response", "final_file_paths", "new_files", "generated", "files_history"}
        ):
            target[key] = value


def _record_output_files(
    state: Any,
    output_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Record DesignProductManager output files in session state."""
    file_records: list[dict[str, Any]] = []
    current_turn = int(state.get("turn_index", 0) or 0)
    current_step = int(state.get("step", 0) or 0)
    current_expert_step = int(state.get("expert_step", 0) or 0)
    for file_info in output_files:
        path = str(file_info.get("path", "") or "").strip()
        if not path:
            continue
        try:
            file_records.append(
                build_workspace_file_record(
                    path,
                    description=str(file_info.get("description", "") or "").strip(),
                    source=str(file_info.get("source", "design_product_manager") or "design_product_manager"),
                    turn=current_turn,
                    step=current_step,
                    expert_step=current_expert_step,
                )
            )
        except Exception:
            continue
    if not file_records:
        return []

    generated = list(state.get("generated") or [])
    generated.extend(file_records)
    state["generated"] = generated
    history = list(state.get("files_history", []) or [])
    history.append(file_records)
    state["new_files"] = file_records
    state["files_history"] = history
    return file_records


def _normalize_final_paths(paths: list[str]) -> list[str]:
    """Validate and normalize final workspace-relative paths."""
    normalized: list[str] = []
    seen: set[str] = set()
    for path in paths or []:
        clean_path = str(path or "").strip()
        if not clean_path:
            continue
        relative_path = workspace_relative_path(resolve_workspace_path(clean_path))
        if relative_path in seen:
            continue
        seen.add(relative_path)
        normalized.append(relative_path)
    return normalized


def _file_records_for_paths(paths: list[str], *, state: Any) -> list[dict[str, Any]]:
    """Return known file records for final file paths."""
    known_records: dict[str, dict[str, Any]] = {}

    def _index(files: list[dict[str, Any]]) -> None:
        for file_info in files:
            if not isinstance(file_info, dict):
                continue
            path = str(file_info.get("path", "") or "").strip()
            if path:
                known_records[path] = file_info

    _index(list(state.get("generated") or []))
    _index(list(state.get("new_files") or []))
    for group in list(state.get("files_history") or []):
        if isinstance(group, list):
            _index(group)
    return [known_records[path] for path in paths if path in known_records]


__all__ = [
    "DESIGN_PRODUCT_RESULT_SCHEMA_VERSION",
    "DesignProductManager",
]
