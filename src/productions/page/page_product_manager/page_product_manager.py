"""ADK-native PageProductManager for content-first HTML page tasks."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent
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
from src.productions.page.page_product_manager.page_code_generation_agent import (
    PageCodeGenerationAgent,
)
from src.productions.page.page_product_manager.page_artifact_visual_validation import (
    validate_page_visual_artifact,
)
from src.productions.page.page_product_manager.page_product_experts import (
    PAGE_PRODUCT_EXPERT_ALLOWLIST,
    build_page_expert_listing,
    is_page_product_expert,
)
from src.productions.page.page_product_manager.product_page_skills import (
    ProductPageSkillRegistry,
)
from src.runtime.expert_dispatcher import dispatch_expert_call
from src.runtime.step_events import append_orchestration_step_event
from src.runtime.tool_context_artifact_service import ToolContextArtifactService
from src.runtime.workspace import (
    build_generated_output_path,
    build_workspace_file_record,
    resolve_workspace_path,
    workspace_relative_path,
)

PAGE_PRODUCT_RESULT_SCHEMA_VERSION = "page-product-result-v1"
PAGE_PRODUCT_RESULT_STATE_KEY = "page_product_result"
PAGE_PRODUCT_REQUEST_STATE_KEY = "page_product_request"
PAGE_PRODUCT_PROGRESS_STATE_KEY = "page_product_progress"
PAGE_PRODUCT_SKILLS_STATE_KEY = "product_page_skills"
PAGE_PRODUCT_ACTIVE_SKILL_STATE_KEY = "active_product_page_skill"
PAGE_PRODUCT_EXPERTS_STATE_KEY = "page_product_experts"
PAGE_PRODUCT_EXPERT_HISTORY_STATE_KEY = "page_product_expert_history"
PAGE_PRODUCT_LAST_EXPERT_RESULT_STATE_KEY = "page_product_last_expert_result"
PAGE_PRODUCT_CODE_GENERATION_HISTORY_STATE_KEY = "page_product_code_generation_history"
PAGE_PRODUCT_LAST_CODE_GENERATION_RESULT_STATE_KEY = "page_product_last_code_generation_result"


class PageProductManager(LlmAgent):
    """ADK LlmAgent that owns content-first page product tasks."""

    _project_root: Path = PrivateAttr()
    _skill_registry: ProductPageSkillRegistry = PrivateAttr()
    _expert_agents: dict[str, BaseAgent] = PrivateAttr(default_factory=dict)
    _page_code_generation_agent: PageCodeGenerationAgent = PrivateAttr()
    _app_name: str = PrivateAttr(default="creative_claw")
    _artifact_service: BaseArtifactService | None = PrivateAttr(default=None)

    def __init__(
        self,
        project_root: str | Path | None = None,
        skills_dir: str | Path | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the PageProductManager as the page product-line agent."""
        provided_tools = kwargs.pop("tools", None)
        super().__init__(
            name=kwargs.pop("name", "PageProductManager"),
            model=kwargs.pop("model", build_llm()),
            description=kwargs.pop("description", "Owns Creative Claw content-first page tasks."),
            instruction=kwargs.pop("instruction", type(self).build_instruction()),
            tools=provided_tools or [],
            include_contents=kwargs.pop("include_contents", "none"),
            **kwargs,
        )
        self._project_root = Path(project_root or PROJECT_PATH).resolve()
        self._skill_registry = ProductPageSkillRegistry(
            project_root=self._project_root,
            skills_dir=skills_dir,
        )
        self._page_code_generation_agent = PageCodeGenerationAgent()
        if provided_tools is None:
            self.tools = [
                self.list_product_page_skills,
                self.read_product_page_skill,
                self.list_page_experts,
                self.invoke_page_expert,
                self.invoke_page_code_generation,
                self.emit_page_progress,
                self.save_page_artifact,
                self.validate_page_artifact,
                self.register_page_delivery,
            ]

    @property
    def project_root(self) -> Path:
        """Return the project root used by this product manager."""
        return self._project_root

    @property
    def skill_registry(self) -> ProductPageSkillRegistry:
        """Return the private product-page skill registry."""
        return self._skill_registry

    @staticmethod
    def build_instruction() -> str:
        """Return the PageProductManager agent instruction."""
        return """
You are Creative Claw's PageProductManager.

# Role
Own content-first HTML page tasks end to end. This product line is for posters, long-image pages, visual articles, social content, and marketing one-pagers where the user cares more about copy, message, image content, and reading sequence than UI design details.

# Scope
- Use PageProductManager for 公众号文章, 小红书文章, 朋友圈长图, marketing posters, HTML posters, visual announcement pages, content-led social cards, and one-page campaign content.
- Do not act like a UI designer. If the task is a dashboard, app screen, product prototype, wireframe, admin console, or interaction-heavy interface, it belongs to DesignProductManager.
- For now, final HTML files can still be displayed by the existing HTML/design preview surface. Do not optimize or change frontend tab behavior.

# Private skills
- Use only your private product-page skills, exposed through `list_product_page_skills` and `read_product_page_skill`.
- Private skills live under `skills/product-page-skills/<skill-name>/SKILL.md`.
- The first MVP skill is `poster-page-designer`.
- Select and read the best matching skill yourself. If no private skill fits, proceed with a simple content-first page workflow and record that assumption.

# Private experts
- Use `list_page_experts` before invoking a private expert.
- Allowed private experts: ImageGenerationAgent, CodeGenerationExpert, ImageUnderstandingAgent, AnythingToMD, SearchAgent.
- Use `invoke_page_code_generation` for the final standalone HTML page.
- Use ImageGenerationAgent for original final bitmap assets. Prefer `provider="nano_banana"` unless the user explicitly asks for another provider or a task-specific constraint requires it.
- Use SearchAgent for text facts, platform examples, or visual references. Search images are references unless explicitly approved for direct final use.
- Use ImageUnderstandingAgent for uploaded/reference images that need OCR, style analysis, or reverse-prompt extraction.
- Use AnythingToMD for user-provided documents or web pages that should become Markdown source material.

# Workflow
1. Call `emit_page_progress` when you start.
2. Call `list_product_page_skills`, then read `poster-page-designer` when useful.
3. Call `list_page_experts` before invoking private experts.
4. Normalize the request into a content-first scope: platform, target reader, message, content blocks, CTA, target aspect/length, assets, and assumptions.
5. Create a Markdown content draft before generating final HTML. Save it with `save_page_artifact` as an auxiliary `.md` file.
6. Resolve only the assets needed for a first usable version. Prefer Nano Banana image generation for public-facing illustrations and poster visuals.
7. Generate the final standalone HTML with `invoke_page_code_generation`.
8. Validate the final HTML with `validate_page_artifact`.
   Treat visual warnings as revision signals: if the page appears to be a review board, has text overflow, broken images, weak first-screen focus, or horizontal overflow, revise once before delivery.
9. Finish by calling `register_page_delivery`.

# Content policy
- The Markdown draft is the alignment artifact. It should contain the title, hook, body copy, captions, CTA, visual asset plan, and code-generation handoff.
- Keep text editable in HTML/CSS. Do not bake readable copy into generated images unless requested.
- Do not ask the user UI-style questions unless the request is impossible to complete without clarification.
- Prefer a concrete first draft over a broad style exploration.

# Final result
Always call `register_page_delivery` before finishing. It must contain a user-facing reply, product status, final HTML paths, and any supporting draft paths.
""".strip()

    async def run_product_request(
        self,
        *,
        task: str,
        inputs: list[Any] | None = None,
        output: dict[str, Any] | None = None,
        tool_context: ToolContext | None = None,
        expert_agents: dict[str, BaseAgent] | None = None,
        app_name: str = "creative_claw",
        artifact_service: BaseArtifactService | None = None,
    ) -> dict[str, Any]:
        """Run one page product request through this LlmAgent."""
        if tool_context is None:
            return _error_result("PageProductManager requires tool context.")

        clean_task = str(task or "").strip()
        if not clean_task:
            return _error_result("PageProductManager requires a non-empty task.")
        if not hasattr(tool_context, "_invocation_context"):
            return _error_result("PageProductManager requires an ADK invocation context.")

        append_orchestration_step_event(
            tool_context.state,
            title="Page Product",
            detail="Status: in_progress\nPageProductManager is working on the content-first page request.",
            stage="page_product",
        )

        self._expert_agents = _filter_page_expert_agents(expert_agents or self._expert_agents)
        self._app_name = app_name
        self._artifact_service = artifact_service

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
        child_state[PAGE_PRODUCT_REQUEST_STATE_KEY] = {
            "task": clean_task,
            "inputs": list(inputs or []),
            "output": dict(output or {}),
        }
        child_state[PAGE_PRODUCT_EXPERTS_STATE_KEY] = build_page_expert_listing(
            self._available_page_expert_agents()
        )
        child_state["app_name"] = app_name

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
                            text=_build_page_product_user_message(
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
            result = final_state.get(PAGE_PRODUCT_RESULT_STATE_KEY)
            if not isinstance(result, dict):
                result = _error_result(
                    "PageProductManager finished without registering a page delivery."
                )
            _copy_page_state_back(source=final_state, target=tool_context.state)
            return result
        except Exception as exc:
            result = _error_result(f"PageProductManager failed: {type(exc).__name__}: {exc}")
            tool_context.state[PAGE_PRODUCT_RESULT_STATE_KEY] = result
            tool_context.state["current_output"] = result
            return result
        finally:
            await child_runner.close()

    def list_product_page_skills(self, tool_context: ToolContext) -> dict[str, Any]:
        """List private product-page skills available to this product manager."""
        skills = [skill.to_dict() for skill in self.skill_registry.list_skills()]
        tool_context.state[PAGE_PRODUCT_SKILLS_STATE_KEY] = skills
        return {
            "status": "success",
            "skills": skills,
            "count": len(skills),
        }

    def read_product_page_skill(
        self,
        name: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Read one private product-page skill."""
        content = self.skill_registry.read_skill(name)
        payload = {
            "name": str(name or "").strip(),
            "content": content,
        }
        tool_context.state[PAGE_PRODUCT_ACTIVE_SKILL_STATE_KEY] = payload
        return {
            "status": "success",
            **payload,
        }

    def list_page_experts(self, tool_context: ToolContext) -> dict[str, Any]:
        """List PageProductManager-private experts available in this runtime."""
        experts = build_page_expert_listing(self._available_page_expert_agents())
        tool_context.state[PAGE_PRODUCT_EXPERTS_STATE_KEY] = experts
        return {
            "status": "success",
            "experts": experts,
            "allowlist": list(PAGE_PRODUCT_EXPERT_ALLOWLIST),
            "count": len(experts),
        }

    async def invoke_page_expert(
        self,
        agent_name: str,
        prompt: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Invoke one PageProductManager-private expert through the shared dispatcher."""
        clean_agent_name = str(agent_name or "").strip()
        if not is_page_product_expert(clean_agent_name):
            return {
                "status": "error",
                "message": (
                    f"PageProductManager cannot invoke expert '{clean_agent_name}'. "
                    f"Allowed experts: {', '.join(PAGE_PRODUCT_EXPERT_ALLOWLIST)}."
                ),
                "allowed_experts": list(PAGE_PRODUCT_EXPERT_ALLOWLIST),
            }

        page_expert_agents = self._available_page_expert_agents()
        if clean_agent_name not in page_expert_agents:
            return {
                "status": "error",
                "message": (
                    f"Page expert '{clean_agent_name}' is allowed but not available "
                    "in the current runtime."
                ),
                "allowed_experts": list(PAGE_PRODUCT_EXPERT_ALLOWLIST),
            }

        invocation = await dispatch_expert_call(
            agent_name=clean_agent_name,
            prompt=str(prompt or "").strip(),
            tool_context=tool_context,
            expert_agents=page_expert_agents,
            app_name=self._app_name,
            artifact_service=self._artifact_service or InMemoryArtifactService(),
        )
        history = list(tool_context.state.get(PAGE_PRODUCT_EXPERT_HISTORY_STATE_KEY) or [])
        history.append(invocation.tool_result)
        tool_context.state[PAGE_PRODUCT_EXPERT_HISTORY_STATE_KEY] = history
        tool_context.state[PAGE_PRODUCT_LAST_EXPERT_RESULT_STATE_KEY] = invocation.tool_result
        if invocation.tool_result.get("output_files"):
            tool_context.state["page_product_generation"] = invocation.tool_result
        return invocation.tool_result

    async def invoke_page_code_generation(
        self,
        prompt: str,
        tool_context: ToolContext,
        language: str = "html",
        output_path: str = "",
        context_files: list[str] | str | None = None,
        constraints: list[str] | str | None = None,
    ) -> dict[str, Any]:
        """Invoke the PageProductManager-private code generation agent."""
        current_output = await self._page_code_generation_agent.run_generation(
            tool_context,
            prompt=str(prompt or "").strip(),
            language=str(language or "html").strip() or "html",
            output_path=str(output_path or "").strip(),
            context_files=_coerce_string_list(context_files),
            constraints=_coerce_string_list(constraints),
        )
        if current_output.get("output_files"):
            current_output = {
                **current_output,
                "output_files": _record_output_files(
                    tool_context.state,
                    list(current_output.get("output_files") or []),
                ),
            }
            tool_context.state["page_product_generation"] = current_output

        history = list(tool_context.state.get(PAGE_PRODUCT_CODE_GENERATION_HISTORY_STATE_KEY) or [])
        history.append(current_output)
        tool_context.state[PAGE_PRODUCT_CODE_GENERATION_HISTORY_STATE_KEY] = history
        tool_context.state[PAGE_PRODUCT_LAST_CODE_GENERATION_RESULT_STATE_KEY] = current_output
        tool_context.state["current_output"] = current_output
        return current_output

    def emit_page_progress(
        self,
        stage: str,
        status: str,
        message: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Record one page-product progress event."""
        clean_stage = str(stage or "page_product").strip() or "page_product"
        clean_status = str(status or "in_progress").strip() or "in_progress"
        clean_message = str(message or "").strip() or "Page product manager is working."
        progress_item = {
            "stage": clean_stage,
            "status": clean_status,
            "message": clean_message,
        }
        progress = list(tool_context.state.get(PAGE_PRODUCT_PROGRESS_STATE_KEY) or [])
        progress.append(progress_item)
        tool_context.state[PAGE_PRODUCT_PROGRESS_STATE_KEY] = progress
        append_orchestration_step_event(
            tool_context.state,
            title="Page Product",
            detail=f"Status: {clean_status}\n{clean_message}",
            stage=clean_stage,
        )
        return {
            "status": "success",
            "progress": progress_item,
        }

    def save_page_artifact(
        self,
        file_name: str,
        content: str,
        description: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Save one complete page artifact file into the workspace."""
        language_extension = Path(str(file_name or "")).suffix or ".html"
        output_path = build_generated_output_path(
            session_id=str(tool_context.state.get("sid") or "page"),
            turn_index=int(tool_context.state.get("turn_index", 0) or 0),
            step=int(tool_context.state.get("step", 0) or 0) + 1,
            output_type="page",
            index=len(list(tool_context.state.get("generated") or [])),
            extension=language_extension,
        )
        output_path.write_text(str(content or "").rstrip() + "\n", encoding="utf-8")
        record = build_workspace_file_record(
            output_path,
            description=str(description or "Page artifact generated by PageProductManager.").strip(),
            source="page_product_manager",
            turn=int(tool_context.state.get("turn_index", 0) or 0),
            step=int(tool_context.state.get("step", 0) or 0),
            expert_step=int(tool_context.state.get("expert_step", 0) or 0),
        )
        output_files = _record_output_files(tool_context.state, [record])
        result = {
            "status": "success",
            "message": f"Saved page artifact at {record['path']}.",
            "output_path": record["path"],
            "output_files": output_files,
            "language": language_extension.lstrip(".") or "html",
        }
        tool_context.state["page_product_generation"] = result
        return result

    def validate_page_artifact(
        self,
        paths: list[str],
        tool_context: ToolContext,
        browser_preview: bool = True,
    ) -> dict[str, Any]:
        """Validate generated page artifact files with lightweight HTML checks."""
        validations = [
            _validate_one_page_artifact(str(path), browser_preview=browser_preview)
            for path in paths or []
        ]
        tool_context.state["page_product_validation"] = validations
        has_error = any(str(item.get("status", "")).lower() == "error" for item in validations)
        return {
            "status": "error" if has_error else "success",
            "validations": validations,
        }

    def register_page_delivery(
        self,
        status: str,
        reply_text: str,
        final_file_paths: list[str],
        supporting_file_paths: list[str] | None,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Register the final page product result for orchestrator delivery."""
        normalized_paths = _normalize_final_paths(final_file_paths)
        supporting_paths = _normalize_final_paths(supporting_file_paths or [])
        result = {
            "result_schema_version": PAGE_PRODUCT_RESULT_SCHEMA_VERSION,
            "status": str(status or "success").strip() or "success",
            "product_line": "page",
            "message": str(reply_text or "").strip() or "Page product task completed.",
            "final_file_paths": normalized_paths,
            "supporting_file_paths": supporting_paths,
            "progress": list(tool_context.state.get(PAGE_PRODUCT_PROGRESS_STATE_KEY) or []),
            "active_skill": tool_context.state.get(PAGE_PRODUCT_ACTIVE_SKILL_STATE_KEY) or {},
            "experts": tool_context.state.get(PAGE_PRODUCT_EXPERTS_STATE_KEY) or [],
            "expert_history": list(tool_context.state.get(PAGE_PRODUCT_EXPERT_HISTORY_STATE_KEY) or []),
            "last_expert_result": tool_context.state.get(PAGE_PRODUCT_LAST_EXPERT_RESULT_STATE_KEY) or {},
            "code_generation_history": list(
                tool_context.state.get(PAGE_PRODUCT_CODE_GENERATION_HISTORY_STATE_KEY) or []
            ),
            "last_code_generation_result": tool_context.state.get(
                PAGE_PRODUCT_LAST_CODE_GENERATION_RESULT_STATE_KEY
            )
            or {},
            "generation": tool_context.state.get("page_product_generation") or {},
            "validation": tool_context.state.get("page_product_validation") or [],
            "output_files": _file_records_for_paths(normalized_paths, state=tool_context.state),
            "supporting_files": _file_records_for_paths(supporting_paths, state=tool_context.state),
        }
        tool_context.state[PAGE_PRODUCT_RESULT_STATE_KEY] = result
        tool_context.state["product_line"] = "page"
        tool_context.state["current_output"] = result
        tool_context.state["last_product_result"] = result
        tool_context.state["final_response"] = result["message"]
        tool_context.state["final_file_paths"] = normalized_paths
        tool_context.state["last_output_message"] = result["message"]
        append_orchestration_step_event(
            tool_context.state,
            title="Page Product",
            detail=f"Status: {result['status']}\n{result['message']}",
            stage="finalizing",
        )
        return result

    def _available_page_expert_agents(self) -> dict[str, BaseAgent]:
        """Return runtime experts that PageProductManager is allowed to invoke."""
        return _filter_page_expert_agents(self._expert_agents)


def _build_page_product_user_message(
    *,
    task: str,
    inputs: list[Any],
    output: dict[str, Any],
) -> str:
    """Build the explicit user message for a page product run."""
    return "\n".join(
        [
            "Handle this Creative Claw page product request end to end.",
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
            "You own content drafting, private skill usage, supporting asset generation, final HTML generation, validation, progress, and final registration.",
            "Always call register_page_delivery before your final response.",
        ]
    )


def _error_result(message: str) -> dict[str, Any]:
    """Build a JSON-safe page product error result."""
    return {
        "result_schema_version": PAGE_PRODUCT_RESULT_SCHEMA_VERSION,
        "status": "error",
        "product_line": "page",
        "message": message,
        "final_file_paths": [],
        "supporting_file_paths": [],
        "progress": [],
        "active_skill": {},
        "experts": [],
        "expert_history": [],
        "last_expert_result": {},
        "code_generation_history": [],
        "last_code_generation_result": {},
        "generation": {},
        "validation": [],
        "output_files": [],
        "supporting_files": [],
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
    """Pick the artifact service for the internal PageProductManager runner."""
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
    """Create a child ADK runner for the page product manager."""
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


def _copy_page_state_back(*, source: Any, target: Any) -> None:
    """Copy page-product state from a child runner back to the parent state."""
    source_state = dict(source)
    for key, value in source_state.items():
        if (
            key.startswith("page_product")
            or key.startswith("product_page")
            or key in {"current_output", "last_product_result", "final_response", "final_file_paths", "new_files", "generated", "files_history"}
        ):
            target[key] = value


def _filter_page_expert_agents(
    expert_agents: dict[str, BaseAgent] | None,
) -> dict[str, BaseAgent]:
    """Return only runtime experts exposed to PageProductManager."""
    return {
        name: agent
        for name, agent in dict(expert_agents or {}).items()
        if is_page_product_expert(name)
    }


def _record_output_files(
    state: Any,
    output_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Record PageProductManager output files in session state."""
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
                    source=str(file_info.get("source", "page_product_manager") or "page_product_manager"),
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


def _coerce_string_list(value: list[str] | str | None) -> list[str]:
    """Normalize a scalar or sequence value into a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(item) for item in value if str(item).strip()]


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


def _validate_one_page_artifact(path: str, *, browser_preview: bool = True) -> dict[str, Any]:
    """Run lightweight local checks for one generated page artifact."""
    clean_path = str(path or "").strip()
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {
        "exists": False,
        "is_html": False,
        "has_html_tag": False,
        "has_body_tag": False,
        "no_local_absolute_paths": False,
    }
    relative_path = clean_path
    visual_validation: dict[str, Any] = {}

    try:
        resolved = resolve_workspace_path(clean_path)
        relative_path = workspace_relative_path(resolved)
    except Exception as exc:
        return {
            "status": "error",
            "path": relative_path,
            "errors": [f"Invalid workspace path: {type(exc).__name__}: {exc}"],
            "warnings": warnings,
            "checks": checks,
        }

    checks["exists"] = resolved.is_file()
    checks["is_html"] = resolved.suffix.lower() in {".html", ".htm"}
    if not checks["exists"]:
        errors.append("File does not exist.")
    if not checks["is_html"]:
        errors.append("Final page artifact must be an HTML file.")

    content = ""
    if checks["exists"]:
        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append("HTML file is not valid UTF-8 text.")

    if content:
        lowered = content.lower()
        checks["has_html_tag"] = "<html" in lowered
        checks["has_body_tag"] = "<body" in lowered
        checks["no_local_absolute_paths"] = "/" + "Users/" not in content
        if not checks["has_html_tag"]:
            errors.append("HTML file is missing an <html> tag.")
        if not checks["has_body_tag"]:
            warnings.append("HTML file is missing an explicit <body> tag.")
        if not checks["no_local_absolute_paths"]:
            errors.append("HTML file contains local absolute paths.")
        if checks["is_html"] and checks["has_html_tag"]:
            visual_validation = validate_page_visual_artifact(
                resolved,
                content=content,
                browser_preview=browser_preview,
            )
            checks.update(dict(visual_validation.get("checks") or {}))
            errors.extend(str(message) for message in visual_validation.get("errors", []) or [])
            warnings.extend(str(message) for message in visual_validation.get("warnings", []) or [])

    return {
        "status": "error" if errors else "warning" if warnings else "success",
        "path": relative_path,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "visual_validation": visual_validation,
    }


__all__ = [
    "PAGE_PRODUCT_RESULT_SCHEMA_VERSION",
    "PageProductManager",
]
