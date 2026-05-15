"""ADK-native PageProductManager for content-first HTML page tasks."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent, LlmAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.apps import App
from google.adk.artifacts import BaseArtifactService, InMemoryArtifactService
from google.adk.events import Event
from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai.types import Content, Part
from pydantic import PrivateAttr

from conf.llm import build_llm
from conf.path import PROJECT_PATH
from src.agents.experts.base import CreativeExpert
from src.logger import logger
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
from src.productions.page.page_product_manager.templates import select_page_template_match
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
PAGE_PRODUCT_TEMPLATE_SELECTION_STATE_KEY = "page_template_selection"
PAGE_PRODUCT_DRAFT_STATE_KEY = "page_content_draft"
PAGE_PRODUCT_MATERIALS_STATE_KEY = "page_materials"
PAGE_PRODUCT_FINAL_DRAFT_STATE_KEY = "page_final_draft"
PAGE_PRODUCT_HTML_GENERATION_STATE_KEY = "page_html_generation"


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

# Built-in page templates
- Final HTML generation has built-in tagged templates loaded from the Page `templates-html` directory, including data reports, social cards, landing pages, posters, documents, dashboards, decks, and frame-style pages.
- Let `invoke_page_code_generation` select a template automatically from the generation brief by default.
- If the user explicitly asks for a known template style, pass its `template_id` to `invoke_page_code_generation`.

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
        page_pipeline_agent = _build_page_pipeline_agent(
            page_code_generation_agent=self._page_code_generation_agent,
            expert_agents=self._available_page_expert_agents(),
        )
        child_runner = _build_child_runner(
            agent=page_pipeline_agent,
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
        template_id: str = "",
        allow_auto_template: bool = True,
    ) -> dict[str, Any]:
        """Invoke the PageProductManager-private code generation agent."""
        current_output = await self._page_code_generation_agent.run_generation(
            tool_context,
            prompt=str(prompt or "").strip(),
            language=str(language or "html").strip() or "html",
            output_path=str(output_path or "").strip(),
            context_files=_coerce_string_list(context_files),
            constraints=_coerce_string_list(constraints),
            template_id=str(template_id or "").strip(),
            allow_auto_template=allow_auto_template,
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
    agent: BaseAgent,
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
            or key
            in {
                PAGE_PRODUCT_TEMPLATE_SELECTION_STATE_KEY,
                PAGE_PRODUCT_DRAFT_STATE_KEY,
                PAGE_PRODUCT_MATERIALS_STATE_KEY,
                PAGE_PRODUCT_FINAL_DRAFT_STATE_KEY,
                PAGE_PRODUCT_HTML_GENERATION_STATE_KEY,
            }
            or key
            in {
                "current_output",
                "last_product_result",
                "product_line",
                "final_response",
                "final_file_paths",
                "last_output_message",
                "new_files",
                "generated",
                "files_history",
                "orchestration_events",
            }
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


class _PageTemplateSelectionAgent(CreativeExpert):
    """Select the built-in Page template before drafting begins."""

    def __init__(self) -> None:
        """Initialize the deterministic template-selection stage."""
        super().__init__(
            name="PageTemplateSelectionAgent",
            description="Selects a Page template or a default Page layout direction.",
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Select a template and write the selection to session state."""
        state = _copy_state(ctx.session.state)
        request = _page_request_from_state(state)
        explicit_template_id = _explicit_template_id(request.get("output"))
        match = select_page_template_match(
            str(request.get("task") or ""),
            template_id=explicit_template_id,
        )
        selected_template = match.template
        use_template = selected_template is not None
        template_id = selected_template.id if selected_template is not None else ""
        selection_mode = "explicit" if explicit_template_id and use_template else "automatic"
        if not use_template:
            selection_mode = "freeform"
        payload = {
            "status": "success",
            "use_template": use_template,
            "template_id": template_id,
            "template": selected_template.to_dict() if selected_template is not None else {},
            "score": match.score,
            "reasons": list(match.reasons),
            "explicit_template_id": explicit_template_id,
            "selection_mode": selection_mode,
        }
        logger.info(
            "Page template selection: use_template={} mode={} template_id={} score={} explicit_template_id={} reasons={}",
            use_template,
            selection_mode,
            template_id,
            match.score,
            explicit_template_id,
            list(match.reasons),
        )
        progress_message = (
            f"Selected Page template: {template_id}."
            if use_template
            else "No suitable Page template selected; using free-form HTML generation."
        )
        _append_page_progress(
            state,
            stage="template_selection",
            status="success",
            message=progress_message,
        )
        state[PAGE_PRODUCT_TEMPLATE_SELECTION_STATE_KEY] = payload
        state["current_output"] = payload
        yield self.format_event(
            progress_message,
            _state_delta(
                state,
                PAGE_PRODUCT_TEMPLATE_SELECTION_STATE_KEY,
                PAGE_PRODUCT_PROGRESS_STATE_KEY,
                "orchestration_events",
                "current_output",
            ),
        )


class _PageDraftAgent(CreativeExpert):
    """Create and persist the first Markdown page draft."""

    def __init__(self) -> None:
        """Initialize the deterministic draft stage."""
        super().__init__(
            name="PageDraftAgent",
            description="Creates the first content-first Markdown draft for a Page request.",
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Build the first draft and save it as a supporting artifact."""
        state = _copy_state(ctx.session.state)
        request = _page_request_from_state(state)
        selection = dict(state.get(PAGE_PRODUCT_TEMPLATE_SELECTION_STATE_KEY) or {})
        draft_markdown = _build_page_draft_markdown(request=request, selection=selection)
        visual_asset_plan = _build_page_visual_asset_plan(
            request=request,
            selection=selection,
        )
        artifact = _save_page_pipeline_artifact(
            state,
            file_name="page-draft.md",
            content=draft_markdown,
            description="Page content draft generated by PageDraftAgent.",
            source="page_draft_agent",
        )
        payload = {
            "status": "success",
            "draft_markdown": draft_markdown,
            "visual_asset_plan": visual_asset_plan,
            "draft_file_path": artifact["output_path"],
            "output_files": artifact["output_files"],
        }
        _append_page_progress(
            state,
            stage="content_draft",
            status="success",
            message="Created the first Page content draft.",
        )
        state[PAGE_PRODUCT_DRAFT_STATE_KEY] = payload
        state["current_output"] = payload
        yield self.format_event(
            "Created the first Page content draft.",
            _state_delta(
                state,
                PAGE_PRODUCT_DRAFT_STATE_KEY,
                PAGE_PRODUCT_PROGRESS_STATE_KEY,
                "orchestration_events",
                "current_output",
                PAGE_PRODUCT_EXPERT_HISTORY_STATE_KEY,
                PAGE_PRODUCT_LAST_EXPERT_RESULT_STATE_KEY,
                "generated",
                "new_files",
                "files_history",
            ),
        )


class _PageMaterialPreparationAgent(CreativeExpert):
    """Prepare material records for the final Page brief."""

    _expert_agents: dict[str, BaseAgent] = PrivateAttr(default_factory=dict)

    def __init__(self, *, expert_agents: dict[str, BaseAgent] | None = None) -> None:
        """Initialize the material-preparation stage."""
        super().__init__(
            name="PageMaterialPreparationAgent",
            description="Resolves existing Page materials and generated image assets.",
        )
        self._expert_agents = dict(expert_agents or {})

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Resolve current input materials and record the material strategy."""
        state = _copy_state(ctx.session.state)
        request = _page_request_from_state(state)
        draft = dict(state.get(PAGE_PRODUCT_DRAFT_STATE_KEY) or {})
        materials = _extract_input_materials(request.get("inputs"))
        visual_asset_plan = list(draft.get("visual_asset_plan") or [])
        generated_assets: list[dict[str, Any]] = []
        unresolved_materials: list[dict[str, Any]] = []

        image_agent = self._expert_agents.get("ImageGenerationAgent")
        for asset in visual_asset_plan:
            if not isinstance(asset, dict):
                continue
            if str(asset.get("source_kind") or "image_generation") != "image_generation":
                unresolved_materials.append(
                    {
                        **asset,
                        "status": "skipped",
                        "message": "Only image_generation assets are supported in Page material preparation.",
                    }
                )
                continue
            if image_agent is None:
                unresolved_materials.append(
                    {
                        **asset,
                        "status": "failed",
                        "message": "ImageGenerationAgent is not available in the current runtime.",
                    }
                )
                continue

            generated_asset = await _generate_page_visual_asset(
                ctx,
                image_agent=image_agent,
                state=state,
                asset=asset,
            )
            if generated_asset.get("status") == "ready":
                generated_assets.append(generated_asset)
            else:
                unresolved_materials.append(generated_asset)

        if generated_assets:
            strategy = f"Generated {len(generated_assets)} Page image asset(s)."
        elif materials:
            strategy = "Use provided workspace materials."
        elif visual_asset_plan:
            strategy = "Image assets were requested but could not be generated; continue with CSS/SVG visuals."
        else:
            strategy = "No external material required for the first usable Page version."

        payload = {
            "status": "success",
            "materials": materials,
            "visual_asset_plan": visual_asset_plan,
            "generated_assets": generated_assets,
            "unresolved_materials": unresolved_materials,
            "strategy": strategy,
            "requires_external_generation": bool(visual_asset_plan),
        }
        _append_page_progress(
            state,
            stage="material_preparation",
            status="success",
            message=payload["strategy"],
        )
        state[PAGE_PRODUCT_MATERIALS_STATE_KEY] = payload
        state["current_output"] = payload
        yield self.format_event(
            payload["strategy"],
            _state_delta(
                state,
                PAGE_PRODUCT_MATERIALS_STATE_KEY,
                PAGE_PRODUCT_PROGRESS_STATE_KEY,
                "orchestration_events",
                "current_output",
                "generated",
                "new_files",
                "files_history",
            ),
        )


class _PageFinalDraftAgent(CreativeExpert):
    """Merge template, draft, and materials into the HTML generation brief."""

    def __init__(self) -> None:
        """Initialize the final-draft stage."""
        super().__init__(
            name="PageFinalDraftAgent",
            description="Builds the final Page brief used by HTML generation.",
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Persist the final generation brief."""
        state = _copy_state(ctx.session.state)
        request = _page_request_from_state(state)
        selection = dict(state.get(PAGE_PRODUCT_TEMPLATE_SELECTION_STATE_KEY) or {})
        draft = dict(state.get(PAGE_PRODUCT_DRAFT_STATE_KEY) or {})
        materials = dict(state.get(PAGE_PRODUCT_MATERIALS_STATE_KEY) or {})
        html_output_path = _expected_page_html_output_path(ctx, state)
        final_markdown = _build_page_final_draft_markdown(
            request=request,
            selection=selection,
            draft=draft,
            materials=materials,
            html_output_path=html_output_path,
        )
        artifact = _save_page_pipeline_artifact(
            state,
            file_name="page-final-brief.md",
            content=final_markdown,
            description="Final Page HTML generation brief generated by PageFinalDraftAgent.",
            source="page_final_draft_agent",
        )
        payload = {
            "status": "success",
            "final_markdown": final_markdown,
            "html_generation_brief": final_markdown,
            "html_output_path": html_output_path,
            "final_draft_file_path": artifact["output_path"],
            "output_files": artifact["output_files"],
        }
        _append_page_progress(
            state,
            stage="final_draft",
            status="success",
            message="Merged template, draft, and materials into the final HTML brief.",
        )
        state[PAGE_PRODUCT_FINAL_DRAFT_STATE_KEY] = payload
        state["current_output"] = payload
        yield self.format_event(
            "Prepared the final Page HTML brief.",
            _state_delta(
                state,
                PAGE_PRODUCT_FINAL_DRAFT_STATE_KEY,
                PAGE_PRODUCT_PROGRESS_STATE_KEY,
                "orchestration_events",
                "current_output",
                "generated",
                "new_files",
                "files_history",
            ),
        )


class _PageHtmlGenerationAgent(CreativeExpert):
    """Generate the final standalone HTML file."""

    _page_code_generation_agent: PageCodeGenerationAgent = PrivateAttr()

    def __init__(self, page_code_generation_agent: PageCodeGenerationAgent) -> None:
        """Initialize the HTML generation stage with the existing generator."""
        super().__init__(
            name="PageHtmlGenerationAgent",
            description="Generates the final standalone HTML Page artifact.",
        )
        self._page_code_generation_agent = page_code_generation_agent

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Generate HTML from the final draft and record output files."""
        state = _copy_state(ctx.session.state)
        final_draft = dict(state.get(PAGE_PRODUCT_FINAL_DRAFT_STATE_KEY) or {})
        selection = dict(state.get(PAGE_PRODUCT_TEMPLATE_SELECTION_STATE_KEY) or {})
        prompt = str(final_draft.get("html_generation_brief") or "").strip()
        output_path = str(final_draft.get("html_output_path") or "").strip()
        if not prompt:
            current_output = {
                "status": "error",
                "message": "Page HTML generation requires a final draft brief.",
                "output_files": [],
            }
        else:
            current_output = await self._page_code_generation_agent.run_generation(
                ctx,
                prompt=prompt,
                language="html",
                output_path=output_path,
                context_files=[],
                constraints=[],
                template_id=str(selection.get("template_id") or "").strip(),
                allow_auto_template=bool(selection.get("use_template")),
            )

        if current_output.get("output_files"):
            current_output = {
                **current_output,
                "output_files": _record_output_files(
                    state,
                    list(current_output.get("output_files") or []),
                ),
            }
            state["page_product_generation"] = current_output

        history = list(state.get(PAGE_PRODUCT_CODE_GENERATION_HISTORY_STATE_KEY) or [])
        history.append(current_output)
        state[PAGE_PRODUCT_CODE_GENERATION_HISTORY_STATE_KEY] = history
        state[PAGE_PRODUCT_LAST_CODE_GENERATION_RESULT_STATE_KEY] = current_output
        state[PAGE_PRODUCT_HTML_GENERATION_STATE_KEY] = current_output
        state["current_output"] = current_output
        _append_page_progress(
            state,
            stage="html_generation",
            status=str(current_output.get("status") or "error"),
            message=str(current_output.get("message") or "Page HTML generation finished."),
        )
        yield self.format_event(
            str(current_output.get("message") or "Page HTML generation finished."),
            _state_delta(
                state,
                PAGE_PRODUCT_HTML_GENERATION_STATE_KEY,
                PAGE_PRODUCT_CODE_GENERATION_HISTORY_STATE_KEY,
                PAGE_PRODUCT_LAST_CODE_GENERATION_RESULT_STATE_KEY,
                PAGE_PRODUCT_PROGRESS_STATE_KEY,
                "orchestration_events",
                "current_output",
                "page_product_generation",
                "generated",
                "new_files",
                "files_history",
            ),
        )


class _PageDeliveryAgent(CreativeExpert):
    """Register the final Page product result without visual validation."""

    def __init__(self) -> None:
        """Initialize the deterministic delivery stage."""
        super().__init__(
            name="PageDeliveryAgent",
            description="Registers the final Page product delivery.",
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Register a success or terminal error Page product result."""
        state = _copy_state(ctx.session.state)
        html_generation = dict(state.get(PAGE_PRODUCT_HTML_GENERATION_STATE_KEY) or {})
        output_path = str(html_generation.get("output_path") or "").strip()
        output_status = str(html_generation.get("status") or "").strip().lower()
        supporting_paths = _supporting_page_paths(state)

        if output_status != "success" or not output_path:
            message = str(html_generation.get("message") or "").strip()
            if not message:
                message = "Page HTML generation did not produce a final HTML file."
            result = _register_page_pipeline_delivery(
                state,
                status="error",
                reply_text=message,
                final_file_paths=[],
                supporting_file_paths=supporting_paths,
            )
        else:
            result = _register_page_pipeline_delivery(
                state,
                status="success",
                reply_text=f"页面已生成：{output_path}",
                final_file_paths=[output_path],
                supporting_file_paths=supporting_paths,
            )

        yield self.format_event(
            str(result.get("message") or "Page product delivery registered."),
            _state_delta(
                state,
                PAGE_PRODUCT_RESULT_STATE_KEY,
                PAGE_PRODUCT_PROGRESS_STATE_KEY,
                "orchestration_events",
                "current_output",
                "last_product_result",
                "product_line",
                "final_response",
                "final_file_paths",
                "last_output_message",
            ),
        )


def _build_page_pipeline_agent(
    *,
    page_code_generation_agent: PageCodeGenerationAgent,
    expert_agents: dict[str, BaseAgent] | None = None,
) -> SequentialAgent:
    """Build the fixed-order Page product pipeline."""
    return SequentialAgent(
        name="PageProductPipeline",
        description="Runs the Page product workflow in a fixed ADK sequence.",
        sub_agents=[
            _PageTemplateSelectionAgent(),
            _PageDraftAgent(),
            _PageMaterialPreparationAgent(expert_agents=expert_agents),
            _PageFinalDraftAgent(),
            _PageHtmlGenerationAgent(page_code_generation_agent),
            _PageDeliveryAgent(),
        ],
    )


def _page_request_from_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized Page request payload stored in session state."""
    request = dict(state.get(PAGE_PRODUCT_REQUEST_STATE_KEY) or {})
    return {
        "task": str(request.get("task") or "").strip(),
        "inputs": list(request.get("inputs") or []),
        "output": dict(request.get("output") or {}),
    }


def _explicit_template_id(output: Any) -> str:
    """Return an explicit template id from an output request when present."""
    if not isinstance(output, dict):
        return ""
    for key in ("template_id", "page_template_id", "template"):
        value = str(output.get(key) or "").strip()
        if value:
            return value
    return ""


def _append_page_progress(
    state: dict[str, Any],
    *,
    stage: str,
    status: str,
    message: str,
) -> None:
    """Append one Page pipeline progress item and orchestration event."""
    clean_stage = str(stage or "page_product").strip() or "page_product"
    clean_status = str(status or "in_progress").strip() or "in_progress"
    clean_message = str(message or "").strip() or "Page product pipeline is working."
    progress = list(state.get(PAGE_PRODUCT_PROGRESS_STATE_KEY) or [])
    progress.append(
        {
            "stage": clean_stage,
            "status": clean_status,
            "message": clean_message,
        }
    )
    state[PAGE_PRODUCT_PROGRESS_STATE_KEY] = progress
    append_orchestration_step_event(
        state,
        title="Page Product",
        detail=f"Status: {clean_status}\n{clean_message}",
        stage=clean_stage,
    )


def _state_delta(state: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Return a state delta for the requested keys that are present."""
    return {key: state[key] for key in keys if key in state}


def _save_page_pipeline_artifact(
    state: dict[str, Any],
    *,
    file_name: str,
    content: str,
    description: str,
    source: str,
) -> dict[str, Any]:
    """Save one Page pipeline support artifact and track it in workspace state."""
    language_extension = Path(str(file_name or "")).suffix or ".md"
    output_path = build_generated_output_path(
        session_id=str(state.get("sid") or "page"),
        turn_index=int(state.get("turn_index", 0) or 0),
        step=int(state.get("step", 0) or 0) + 1,
        output_type="page",
        index=len(list(state.get("generated") or [])),
        extension=language_extension,
    )
    output_path.write_text(str(content or "").rstrip() + "\n", encoding="utf-8")
    record = build_workspace_file_record(
        output_path,
        description=str(description or "Page artifact generated by Page pipeline.").strip(),
        source=str(source or "page_product_pipeline").strip(),
        turn=int(state.get("turn_index", 0) or 0),
        step=int(state.get("step", 0) or 0),
        expert_step=int(state.get("expert_step", 0) or 0),
    )
    output_files = _record_output_files(state, [record])
    return {
        "status": "success",
        "message": f"Saved page artifact at {record['path']}.",
        "output_path": record["path"],
        "output_files": output_files,
        "language": language_extension.lstrip(".") or "md",
    }


def _build_page_draft_markdown(
    *,
    request: dict[str, Any],
    selection: dict[str, Any],
) -> str:
    """Build the first Page Markdown draft from the request and template choice."""
    task = str(request.get("task") or "").strip()
    output = dict(request.get("output") or {})
    use_template = bool(selection.get("use_template"))
    template_id = str(selection.get("template_id") or "").strip()
    template = dict(selection.get("template") or {})
    template_name = str(template.get("name") or template_id).strip()
    template_lines = (
        [
            f"- Template ID: {template_id}",
            f"- Template name: {template_name}",
            f"- Selection reasons: {', '.join(selection.get('reasons') or [])}",
        ]
        if use_template and template_id
        else [
            "- No built-in template selected.",
            f"- Selection reasons: {', '.join(selection.get('reasons') or [])}",
            "- Generate freely from the source request and Page artifact rules.",
        ]
    )
    handoff_template_rule = (
        "- Use the selected template as structural and visual guidance."
        if use_template and template_id
        else "- No template guidance is required; generate a custom layout that fits the content."
    )
    return "\n".join(
        [
            "# Page Draft",
            "",
            "## Source Request",
            task,
            "",
            "## Output Request",
            repr(output),
            "",
            "## Selected Template",
            *template_lines,
            "",
            "## Content Scope",
            "- Preserve all user-provided facts, numbers, claims, and dates.",
            "- Turn structured data into grounded charts, tables, or visual callouts when useful.",
            "- Keep readable copy in HTML/CSS/SVG text, not baked into images.",
            "",
            "## Material Plan",
            "- Use provided workspace-relative assets when present.",
            "- Generate image assets only when the user explicitly requests a new image, illustration, or hero visual.",
            "- If no generated image is needed, use CSS/SVG/data visualization for the first usable version.",
            "",
            "## HTML Generation Handoff",
            "- Generate one complete standalone HTML file.",
            handoff_template_rule,
            "- When the final brief lists local material sources, prefer HTML-relative image paths.",
            "- Make desktop preview and mobile long-image reading both usable.",
        ]
    )


def _build_page_visual_asset_plan(
    *,
    request: dict[str, Any],
    selection: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return image-generation asset requests implied by the Page task."""
    output = dict(request.get("output") or {})
    explicit_assets = _explicit_page_visual_assets(output)
    if explicit_assets:
        return [
            _normalize_page_visual_asset(asset, index=index, request=request, selection=selection)
            for index, asset in enumerate(explicit_assets, start=1)
        ]

    if _extract_input_materials(request.get("inputs")):
        return []
    if not _page_task_requests_generated_visual(request=request, selection=selection):
        return []

    return [
        _normalize_page_visual_asset(
            {
                "asset_id": "page_hero_visual",
                "prompt": _build_default_page_image_prompt(request=request, selection=selection),
                "usage": "Hero/supporting visual for the final HTML page.",
            },
            index=1,
            request=request,
            selection=selection,
        )
    ]


def _explicit_page_visual_assets(output: dict[str, Any]) -> list[Any]:
    """Return explicitly requested Page image assets from the output contract."""
    for key in ("page_image_assets", "image_assets", "visual_assets", "generated_assets"):
        value = output.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, (dict, str)):
            return [value]

    image_prompt = str(output.get("image_prompt") or output.get("visual_prompt") or "").strip()
    if image_prompt:
        return [{"asset_id": "page_hero_visual", "prompt": image_prompt}]
    if output.get("generate_image") is True or output.get("generate_images") is True:
        return [{"asset_id": "page_hero_visual"}]
    return []


def _normalize_page_visual_asset(
    asset: Any,
    *,
    index: int,
    request: dict[str, Any],
    selection: dict[str, Any],
) -> dict[str, Any]:
    """Normalize one Page visual asset request for ImageGenerationAgent."""
    if isinstance(asset, str):
        asset = {"prompt": asset}
    asset = dict(asset or {})
    output = dict(request.get("output") or {})
    template_id = str(selection.get("template_id") or "").strip()
    prompt = str(asset.get("prompt") or "").strip()
    if not prompt:
        prompt = _build_default_page_image_prompt(request=request, selection=selection)
    return {
        "asset_id": str(asset.get("asset_id") or asset.get("id") or f"page_visual_{index:02d}"),
        "source_kind": "image_generation",
        "status": "pending",
        "prompt": prompt,
        "usage": str(asset.get("usage") or "Supporting visual for the final HTML page."),
        "provider": str(asset.get("provider") or output.get("image_provider") or "nano_banana"),
        "aspect_ratio": str(
            asset.get("aspect_ratio")
            or output.get("image_aspect_ratio")
            or output.get("aspect_ratio")
            or _default_page_image_aspect_ratio(template_id)
        ),
        "resolution": str(asset.get("resolution") or output.get("image_resolution") or "1K"),
    }


def _page_task_requests_generated_visual(
    *,
    request: dict[str, Any],
    selection: dict[str, Any],
) -> bool:
    """Return whether the request strongly implies a generated image asset."""
    output = dict(request.get("output") or {})
    if output.get("skip_image_generation") is True or output.get("generate_images") is False:
        return False

    task = str(request.get("task") or "").lower()
    negative_terms = (
        "不要配图",
        "不用配图",
        "无需配图",
        "不需要配图",
        "不要图片",
        "不用图片",
        "无需图片",
        "不需要图片",
        "不要插图",
        "不用插图",
        "无需插图",
        "不需要插图",
        "不要生成图片",
        "不用生成图片",
        "不生成图片",
        "without images",
        "no images",
        "no image generation",
    )
    if any(term in task for term in negative_terms):
        return False

    explicit_visual_terms = (
        "生成配图",
        "生成图片",
        "生成插图",
        "生成主视觉",
        "需要配图",
        "需要图片",
        "需要插图",
        "需要主视觉",
        "加一张图",
        "加图片",
        "加插图",
        "配一张图",
        "主视觉插图",
        "hero image",
        "generate image",
        "generate an image",
        "create image",
        "create an image",
        "illustration",
        "product visual",
    )
    if any(term in task for term in explicit_visual_terms):
        return True

    return False


def _default_page_image_aspect_ratio(template_id: str) -> str:
    """Return a conservative generated-image aspect ratio for a Page template."""
    if template_id in {
        "poster-hero",
        "card-xiaohongshu",
        "magazine-poster",
        "deck-xhs-post",
        "deck-xhs-pastel",
        "deck-xhs-white",
        "social-carousel",
    }:
        return "4:5"
    return "16:9"


def _build_default_page_image_prompt(
    *,
    request: dict[str, Any],
    selection: dict[str, Any],
) -> str:
    """Build a text-free image prompt for a Page support visual."""
    task = str(request.get("task") or "").strip()
    template_id = str(selection.get("template_id") or "").strip() or "content-first page"
    clipped_task = task[:600]
    return (
        "Create one original premium editorial support visual for a content-first HTML page. "
        f"Template context: {template_id}. Topic and intent: {clipped_task}. "
        "No readable text, no logos, no fake UI, no charts with numbers; all copy and data labels "
        "must remain editable in HTML. Use a polished composition with clear focal subject, "
        "safe margins, and enough quiet space for surrounding web layout."
    )


def _extract_input_materials(inputs: Any) -> list[dict[str, str]]:
    """Return material records from request inputs that already have workspace paths."""
    materials: list[dict[str, str]] = []
    for index, item in enumerate(list(inputs or []), start=1):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or item.get("input_path") or "").strip()
        if not path:
            continue
        materials.append(
            {
                "id": str(item.get("id") or f"input_material_{index}"),
                "path": path,
                "alt": str(item.get("description") or item.get("name") or f"Input material {index}"),
                "usage": str(item.get("usage") or "Reference or final page material."),
            }
        )
    return materials


async def _generate_page_visual_asset(
    ctx: InvocationContext,
    *,
    image_agent: BaseAgent,
    state: dict[str, Any],
    asset: dict[str, Any],
) -> dict[str, Any]:
    """Generate one Page visual asset with ImageGenerationAgent."""
    parameters = {
        "prompt": str(asset.get("prompt") or "").strip(),
        "provider": str(asset.get("provider") or "nano_banana").strip() or "nano_banana",
        "aspect_ratio": str(asset.get("aspect_ratio") or "16:9").strip() or "16:9",
        "resolution": str(asset.get("resolution") or "1K").strip() or "1K",
    }
    current_output: dict[str, Any] = {}
    session_state = ctx.session.state
    had_previous_parameters = "current_parameters" in session_state
    previous_parameters = session_state.get("current_parameters")
    session_state["current_parameters"] = parameters

    try:
        async for event in image_agent.run_async(ctx):
            state_delta = getattr(getattr(event, "actions", None), "state_delta", None) or {}
            if isinstance(state_delta, dict) and isinstance(state_delta.get("current_output"), dict):
                current_output = dict(state_delta["current_output"])
    except Exception as exc:
        failed_output = {
            "status": "error",
            "message": f"ImageGenerationAgent failed: {type(exc).__name__}: {exc}",
            "output_files": [],
        }
        _record_page_image_expert_result(
            state,
            asset=asset,
            parameters=parameters,
            current_output=failed_output,
            output_files=[],
        )
        return {
            **asset,
            "status": "failed",
            "parameters": parameters,
            "message": failed_output["message"],
        }
    finally:
        if had_previous_parameters:
            session_state["current_parameters"] = previous_parameters
        else:
            try:
                del session_state["current_parameters"]
            except KeyError:
                pass

    output_files = list(current_output.get("output_files") or [])
    status = str(current_output.get("status") or "error").strip().lower()
    if status == "success" and output_files:
        normalized_files = _record_output_files(state, output_files)
        output_path = _first_output_file_path(normalized_files) or _first_output_file_path(output_files)
        if output_path:
            _record_page_image_expert_result(
                state,
                asset=asset,
                parameters=parameters,
                current_output=current_output,
                output_files=normalized_files,
            )
            return {
                **asset,
                "status": "ready",
                "path": output_path,
                "provider": parameters["provider"],
                "parameters": parameters,
                "message": str(current_output.get("message") or "Image asset generated."),
                "output_files": normalized_files,
            }

    _record_page_image_expert_result(
        state,
        asset=asset,
        parameters=parameters,
        current_output=current_output,
        output_files=output_files,
    )
    return {
        **asset,
        "status": "failed",
        "provider": parameters["provider"],
        "parameters": parameters,
        "message": str(current_output.get("message") or "ImageGenerationAgent did not return an image file."),
        "output_files": output_files,
    }


def _record_page_image_expert_result(
    state: dict[str, Any],
    *,
    asset: dict[str, Any],
    parameters: dict[str, Any],
    current_output: dict[str, Any],
    output_files: list[dict[str, Any]],
) -> None:
    """Record an internal Page image expert invocation in Page expert history."""
    tool_result = {
        "agent_name": "ImageGenerationAgent",
        "status": str(current_output.get("status") or "error"),
        "message": str(current_output.get("message") or ""),
        "output_files": output_files,
        "parameters": parameters,
        "structured_data": {
            "asset_id": str(asset.get("asset_id") or ""),
            "source_kind": "image_generation",
        },
    }
    history = list(state.get(PAGE_PRODUCT_EXPERT_HISTORY_STATE_KEY) or [])
    history.append(tool_result)
    state[PAGE_PRODUCT_EXPERT_HISTORY_STATE_KEY] = history
    state[PAGE_PRODUCT_LAST_EXPERT_RESULT_STATE_KEY] = tool_result


def _first_output_file_path(output_files: list[dict[str, Any]]) -> str:
    """Return the first path-like value from generated output file records."""
    for item in output_files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or item.get("output_path") or "").strip()
        if path:
            return path
    return ""


def _expected_page_html_output_path(ctx: InvocationContext, state: dict[str, Any]) -> str:
    """Return the deterministic final HTML output path for Page code generation."""
    session = getattr(ctx, "session", None)
    output_path = build_generated_output_path(
        session_id=str(getattr(session, "id", "") or state.get("sid", "") or "default"),
        turn_index=int(state.get("turn_index", 0) or 0),
        step=int(state.get("step", 0) or 0),
        output_type="page_code_generation",
        index=0,
        extension=".html",
    )
    return workspace_relative_path(output_path)


def _html_relative_src(path: Any, html_output_path: Any) -> str:
    """Return a material path relative to the final HTML file directory."""
    clean_path = str(path or "").strip()
    clean_html_path = str(html_output_path or "").strip()
    if not clean_path or not clean_html_path:
        return ""
    try:
        material_path = resolve_workspace_path(clean_path)
        html_path = resolve_workspace_path(clean_html_path)
        return Path(os.path.relpath(material_path, start=html_path.parent)).as_posix()
    except Exception:
        return ""


def _local_file_src(path: Any) -> str:
    """Return a local absolute file URL for a workspace path, if it is valid."""
    clean_path = str(path or "").strip()
    if not clean_path:
        return ""
    try:
        return resolve_workspace_path(clean_path).as_uri()
    except Exception:
        return ""


def _build_page_final_draft_markdown(
    *,
    request: dict[str, Any],
    selection: dict[str, Any],
    draft: dict[str, Any],
    materials: dict[str, Any],
    html_output_path: str = "",
) -> str:
    """Build the final HTML generation brief from pipeline state."""
    use_template = bool(selection.get("use_template"))
    template_id = str(selection.get("template_id") or "").strip()
    material_records = list(materials.get("materials") or [])
    generated_assets = list(materials.get("generated_assets") or [])
    unresolved_materials = list(materials.get("unresolved_materials") or [])
    material_lines = [
        f"- Provided material `{item.get('id')}`: workspace_path={item.get('path')}; "
        f"html_relative_src={_html_relative_src(item.get('path'), html_output_path) or 'unavailable'}; "
        f"absolute_file_src={_local_file_src(item.get('path')) or 'unavailable'}; "
        f"alt={item.get('alt')}; usage={item.get('usage')}"
        for item in material_records
        if isinstance(item, dict)
    ]
    material_lines.extend(
        [
            f"- Generated image `{item.get('asset_id')}`: workspace_path={item.get('path')}; "
            f"html_relative_src={_html_relative_src(item.get('path'), html_output_path) or 'unavailable'}; "
            f"absolute_file_src={_local_file_src(item.get('path')) or 'unavailable'}; "
            f"usage={item.get('usage')}; prompt={item.get('prompt')}"
            for item in generated_assets
            if isinstance(item, dict) and item.get("path")
        ]
    )
    if not material_lines:
        material_lines = ["- No external material paths. Use CSS/SVG/data visualization as needed."]

    unresolved_lines = [
        f"- {item.get('asset_id') or item.get('id')}: status={item.get('status')}; message={item.get('message')}"
        for item in unresolved_materials
        if isinstance(item, dict)
    ]
    if not unresolved_lines:
        unresolved_lines = ["- None."]

    return "\n".join(
        [
            "# Final Page HTML Generation Brief",
            "",
            "## Original User Task",
            str(request.get("task") or "").strip(),
            "",
            "## Selected Template",
            f"- Use template: {use_template}",
            f"- Template ID: {template_id if use_template and template_id else 'none'}",
            f"- Selection mode: {selection.get('selection_mode') or 'automatic'}",
            f"- Final HTML output path: {html_output_path or 'auto'}",
            "",
            "## Resolved Materials",
            *material_lines,
            "",
            "## Unresolved Materials",
            *unresolved_lines,
            "",
            "## Content Draft",
            str(draft.get("draft_markdown") or "").strip(),
            "",
            "## Final HTML Requirements",
            "- Output exactly one standalone HTML document.",
            "- Preserve all user-provided data and insight wording unless a concise label is needed for layout.",
            "- Use non-empty `html_relative_src` values exactly for resolved material image `src` attributes and CSS `url(...)` references.",
            "- `html_relative_src` is relative to the final HTML file directory and works in Finder and CreativeClaw design tab.",
            "- Keep `absolute_file_src` values as a local-file fallback/debug reference only; do not use them when `html_relative_src` is present.",
            "- Keep `workspace_path` values for provenance only; do not use them as browser image sources.",
            "- If `html_relative_src` is unavailable for a material, do not use that material as a final browser image source.",
            "- Do not invent image paths. If a requested image is unresolved, use CSS/SVG/data visualization instead.",
            "- Avoid review-board or multi-option language unless explicitly requested.",
            "- Register-worthy output means the file is directly publishable as the first usable version.",
        ]
    )


def _supporting_page_paths(state: dict[str, Any]) -> list[str]:
    """Return draft and final-brief paths to attach as supporting files."""
    materials = dict(state.get(PAGE_PRODUCT_MATERIALS_STATE_KEY) or {})
    paths = [
        str(dict(state.get(PAGE_PRODUCT_DRAFT_STATE_KEY) or {}).get("draft_file_path") or "").strip(),
        str(dict(state.get(PAGE_PRODUCT_FINAL_DRAFT_STATE_KEY) or {}).get("final_draft_file_path") or "").strip(),
    ]
    for key in ("materials", "generated_assets"):
        for item in list(materials.get(key) or []):
            if isinstance(item, dict):
                paths.append(str(item.get("path") or "").strip())
    unique_paths: list[str] = []
    for path in paths:
        if path and path not in unique_paths:
            unique_paths.append(path)
    return unique_paths


def _register_page_pipeline_delivery(
    state: dict[str, Any],
    *,
    status: str,
    reply_text: str,
    final_file_paths: list[str],
    supporting_file_paths: list[str] | None,
) -> dict[str, Any]:
    """Register the final Page pipeline result in session state."""
    normalized_paths = _normalize_final_paths(final_file_paths)
    supporting_paths = _normalize_final_paths(supporting_file_paths or [])
    result = {
        "result_schema_version": PAGE_PRODUCT_RESULT_SCHEMA_VERSION,
        "status": str(status or "success").strip() or "success",
        "product_line": "page",
        "message": str(reply_text or "").strip() or "Page product task completed.",
        "final_file_paths": normalized_paths,
        "supporting_file_paths": supporting_paths,
        "progress": list(state.get(PAGE_PRODUCT_PROGRESS_STATE_KEY) or []),
        "active_skill": state.get(PAGE_PRODUCT_ACTIVE_SKILL_STATE_KEY) or {},
        "experts": state.get(PAGE_PRODUCT_EXPERTS_STATE_KEY) or [],
        "expert_history": list(state.get(PAGE_PRODUCT_EXPERT_HISTORY_STATE_KEY) or []),
        "last_expert_result": state.get(PAGE_PRODUCT_LAST_EXPERT_RESULT_STATE_KEY) or {},
        "code_generation_history": list(state.get(PAGE_PRODUCT_CODE_GENERATION_HISTORY_STATE_KEY) or []),
        "last_code_generation_result": state.get(PAGE_PRODUCT_LAST_CODE_GENERATION_RESULT_STATE_KEY) or {},
        "generation": state.get("page_product_generation") or {},
        "validation": state.get("page_product_validation") or [],
        "output_files": _file_records_for_paths(normalized_paths, state=state),
        "supporting_files": _file_records_for_paths(supporting_paths, state=state),
        "template_selection": state.get(PAGE_PRODUCT_TEMPLATE_SELECTION_STATE_KEY) or {},
        "materials": state.get(PAGE_PRODUCT_MATERIALS_STATE_KEY) or {},
    }
    state[PAGE_PRODUCT_RESULT_STATE_KEY] = result
    state["product_line"] = "page"
    state["current_output"] = result
    state["last_product_result"] = result
    state["final_response"] = result["message"]
    state["final_file_paths"] = normalized_paths
    state["last_output_message"] = result["message"]
    _append_page_progress(
        state,
        stage="finalizing",
        status=result["status"],
        message=result["message"],
    )
    return result


__all__ = [
    "PAGE_PRODUCT_DRAFT_STATE_KEY",
    "PAGE_PRODUCT_FINAL_DRAFT_STATE_KEY",
    "PAGE_PRODUCT_HTML_GENERATION_STATE_KEY",
    "PAGE_PRODUCT_MATERIALS_STATE_KEY",
    "PAGE_PRODUCT_RESULT_SCHEMA_VERSION",
    "PAGE_PRODUCT_TEMPLATE_SELECTION_STATE_KEY",
    "PageProductManager",
]
