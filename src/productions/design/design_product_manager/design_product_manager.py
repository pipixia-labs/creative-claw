"""ADK-native DesignProductManager for Creative Claw design tasks."""

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
from src.productions.design.design_systems import list_design_systems, read_design_system
from src.productions.design.design_product_manager.design_code_generation_agent import (
    DesignCodeGenerationAgent,
)
from src.productions.design.design_product_manager.brief_form import (
    DESIGN_BRIEF_FORM_ANSWERS_STATE_KEY,
    DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY,
    DESIGN_BRIEF_FORM_SCHEMA_VERSION,
    DESIGN_BRIEF_FORM_STATE_KEY,
    DesignBriefFormExpert,
    build_task_with_form_answers,
    parse_form_answers,
)
from src.productions.design.design_product_manager.design_product_experts import (
    DESIGN_PRODUCT_EXPERT_ALLOWLIST,
    build_design_expert_listing,
    is_design_product_expert,
)
from src.productions.design.design_product_manager.product_design_skills import (
    ProductDesignSkillRegistry,
)
from src.productions.design.design_product_manager.validation import validate_design_artifacts
from src.runtime.expert_dispatcher import dispatch_expert_call
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
DESIGN_PRODUCT_EXPERTS_STATE_KEY = "design_product_experts"
DESIGN_PRODUCT_EXPERT_HISTORY_STATE_KEY = "design_product_expert_history"
DESIGN_PRODUCT_LAST_EXPERT_RESULT_STATE_KEY = "design_product_last_expert_result"
DESIGN_PRODUCT_CODE_GENERATION_HISTORY_STATE_KEY = "design_product_code_generation_history"
DESIGN_PRODUCT_LAST_CODE_GENERATION_RESULT_STATE_KEY = "design_product_last_code_generation_result"
DESIGN_PRODUCT_SELECTED_DESIGN_SYSTEM_STATE_KEY = "design_product_selected_design_system"


def _clear_state_value(state: Any, key: str) -> None:
    """Clear one session state value through the public ADK State API."""
    state[key] = None


class DesignProductManager(LlmAgent):
    """ADK LlmAgent that owns design product-line tasks."""

    _project_root: Path = PrivateAttr()
    _skill_registry: ProductDesignSkillRegistry = PrivateAttr()
    _expert_agents: dict[str, BaseAgent] = PrivateAttr(default_factory=dict)
    _brief_form_expert: DesignBriefFormExpert = PrivateAttr()
    _design_code_generation_agent: DesignCodeGenerationAgent = PrivateAttr()
    _app_name: str = PrivateAttr(default="creative_claw")
    _artifact_service: BaseArtifactService | None = PrivateAttr(default=None)

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
        self._brief_form_expert = DesignBriefFormExpert()
        self._design_code_generation_agent = DesignCodeGenerationAgent()
        if provided_tools is None:
            self.tools = [
                self.list_product_design_skills,
                self.read_product_design_skill,
                self.list_design_experts,
                self.invoke_design_expert,
                self.invoke_design_code_generation,
                self.emit_design_progress,
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

# Design medium posture
- HTML is the tool, not the medium. Before choosing skills or experts, decide what kind of designer this task needs.
- Mobile app and product prototypes require an interaction designer posture: user flows, states, hit targets, screen relationships, and reviewable artboards matter more than building a routed app.
- Dashboards and internal tools require a systems designer posture: information density, hierarchy, scanning speed, tabular numerics, and operational clarity are the design.
- Landing pages and marketing sites require a brand designer posture: narrative, audience, proof, conversion path, and one memorable visual move matter more than generic sections.
- Posters, cards, editorial pages, and decks require a visual communication posture: composition, rhythm, contrast, typography, and message hierarchy drive the artifact.
- Do not let every task collapse into a generic SaaS landing page or app shell.

# Private skills
- Use only your private product-design skills, exposed through `list_product_design_skills` and `read_product_design_skill`.
- Private skills live under `skills/product-design-skills/<skill-name>/SKILL.md`.
- Do not ask the orchestrator to read design skills for you.
- Select the most relevant private skill yourself. If no private skill fits, proceed with your own design judgment and record that assumption.

# Private experts
- Use `list_design_experts` to inspect your private expert allowlist.
- Use `invoke_design_expert` when the task needs a specialized design operation.
- Allowed private experts: ImageGenerationAgent, CodeGenerationExpert, ImageUnderstandingAgent, AnythingToMD, SearchAgent.
- Use `invoke_design_code_generation` for final HTML, dashboards, landing pages, app screens, interactive prototypes, and other code-backed design artifacts.
- Use CodeGenerationExpert only for supporting non-final code snippets or auxiliary code files when the design-specific generator is not appropriate.
- Use ImageUnderstandingAgent for uploaded reference images, screenshots, visual style analysis, OCR, and reverse-prompt extraction.
- Use ImageGenerationAgent only when the design needs new bitmap assets such as hero images, poster visuals, illustrations, or backgrounds.
- Use AnythingToMD for user-provided documents, web pages, spreadsheets, or slide files that should become Markdown design input.
- Use SearchAgent only when external visual or textual reference is genuinely needed.
- Do not call experts outside the private allowlist.

# Final artifact ownership
- DesignProductManager is a code-backed design product line.
- The primary final deliverable must be a code-backed design artifact, usually standalone HTML, unless the user explicitly requests a different supporting asset and you decide it still belongs inside the design product line.
- DesignCodeGenerationAgent is the default and preferred producer of final code-backed design artifacts.
- Use `invoke_design_code_generation` for final standalone HTML, landing pages, dashboards, app screen prototypes, interactive tools, HTML posters/cards, HTML decks, and CSS/JS/HTML design artifacts.
- Do not use `save_design_artifact` to create the main final HTML or code artifact. Use it only for auxiliary files or already-complete small supporting files.
- Other private experts are supporting experts. AnythingToMD prepares source material, ImageUnderstandingAgent analyzes visual references, SearchAgent gathers external references, and ImageGenerationAgent creates image assets for the final code-backed artifact.
- ImageGenerationAgent output is normally an intermediate asset, not the primary final design product. If image assets are generated, pass their workspace paths and intended usage to `invoke_design_code_generation`.
- If the user asks only for a standalone image, treat it as a design asset request and prefer producing a code-backed final artifact when the request belongs to the design product line.

# Workflow
1. Call `emit_design_progress` when you start.
2. Call `list_product_design_skills`.
3. Call `list_design_experts` before invoking a private expert.
4. Read the best matching skill with `read_product_design_skill` when a skill is useful.
5. Decide whether the task has enough information. Lock the design medium, content structure, visual direction, design system, scale, and whether the user expects style exploration. If it does not have enough information, return a clarification result through `register_design_delivery` without generating a file.
6. Generate final code-backed design artifacts through `invoke_design_code_generation`. Use `save_design_artifact` only when you already have complete supporting file content.
7. Validate generated files with `validate_design_artifact`.
8. Finish by calling `register_design_delivery`.

# Design artifact generation contract
- Treat generated HTML as a design canvas, not a production application.
- Prefer visible sections and artboards for screens, states, and visual variants.
- Keep artboard identifiers, labels, and component names stable for future AI edits.
- When sketch annotations or screenshots are present, pass them as concrete design critique/edit context to `invoke_design_code_generation`.
- Do not hide design alternatives behind app routing when the user needs design review.
- When the user asks to explore styles, request 2-3 differentiated visual directions by default. Keep the same information architecture and content across directions so the comparison is meaningful.
- For style exploration, instruct code generation to produce a comparison canvas: one compact brief/design-system artboard first, then one visible section per direction using the same screens or sections.
- Ask each direction to state its title, design intent, token summary, and why it is different. The directions should differ in typography, density, radius/border model, image strategy, layout rhythm, or domain metaphor, not only in color.
- Keep shared screen lists, domain data, and variant metadata explicit with stable names such as `SCREENS`, `VARIANTS`, and human-readable artboard ids.
- When a design system is selected, treat its DESIGN.md as authoritative for palette, typography, spacing, and component posture. Use visual direction only to organize composition choices that do not conflict with the selected system.
- If no brand or design system is selected, choose and state a clear visual direction instead of improvising a vague style.

# Quality bar
- Before delivery, expect the generated artifact to pass a silent five-dimensional design self-check: philosophy, hierarchy, execution, specificity, and restraint.
- Avoid AI-slop patterns: aggressive purple gradients, filler metrics, generic feature cards, emoji-as-icons, overdecorated shadows, and every screen using the same rounded-card layout.
- Prefer honest placeholders over invented facts when the user has not supplied real copy, data, names, or metrics.

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
        expert_agents: dict[str, BaseAgent] | None = None,
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

        try:
            answer_payload = parse_form_answers(clean_task)
        except ValueError as exc:
            return _error_result(f"Invalid design brief form answers: {exc}")
        if answer_payload is not None:
            original_task = str(
                tool_context.state.get(DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY) or ""
            ).strip()
            tool_context.state[DESIGN_BRIEF_FORM_ANSWERS_STATE_KEY] = answer_payload
            clean_task = build_task_with_form_answers(
                original_task=original_task or clean_task,
                answer_payload=answer_payload,
            )
            _clear_state_value(tool_context.state, DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY)
            _clear_state_value(tool_context.state, DESIGN_BRIEF_FORM_STATE_KEY)
            selected_design_system = _resolve_selected_design_system_context(answer_payload)
            if selected_design_system:
                tool_context.state[DESIGN_PRODUCT_SELECTED_DESIGN_SYSTEM_STATE_KEY] = selected_design_system
                clean_task = _append_selected_design_system_summary(clean_task, selected_design_system)
            else:
                _clear_state_value(tool_context.state, DESIGN_PRODUCT_SELECTED_DESIGN_SYSTEM_STATE_KEY)
        elif _should_request_web_brief_form(tool_context.state):
            invocation_context = tool_context._invocation_context
            try:
                form_message = await self._brief_form_expert.generate_form(
                    task=clean_task,
                    app_name=app_name,
                    user_id=invocation_context.user_id,
                )
            except Exception as exc:
                return _error_result(
                    f"Design brief form generation failed: {type(exc).__name__}: {exc}"
                )
            result = _brief_form_result(form_message)
            tool_context.state[DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY] = clean_task
            tool_context.state[DESIGN_BRIEF_FORM_STATE_KEY] = {
                "schema_version": DESIGN_BRIEF_FORM_SCHEMA_VERSION,
                "message": form_message,
            }
            tool_context.state[DESIGN_PRODUCT_RESULT_STATE_KEY] = result
            tool_context.state["current_output"] = result
            tool_context.state["final_response"] = form_message
            tool_context.state["final_file_paths"] = []
            tool_context.state["last_output_message"] = form_message
            return result

        self._expert_agents = _filter_design_expert_agents(expert_agents or self._expert_agents)
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
        child_state[DESIGN_PRODUCT_REQUEST_STATE_KEY] = {
            "task": clean_task,
            "inputs": list(inputs or []),
            "output": dict(output or {}),
        }
        child_state[DESIGN_PRODUCT_EXPERTS_STATE_KEY] = build_design_expert_listing(
            self._available_design_expert_agents()
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

    def list_design_experts(self, tool_context: ToolContext) -> dict[str, Any]:
        """List DesignProductManager-private experts available in this runtime."""
        experts = build_design_expert_listing(self._available_design_expert_agents())
        tool_context.state[DESIGN_PRODUCT_EXPERTS_STATE_KEY] = experts
        return {
            "status": "success",
            "experts": experts,
            "allowlist": list(DESIGN_PRODUCT_EXPERT_ALLOWLIST),
            "count": len(experts),
        }

    async def invoke_design_expert(
        self,
        agent_name: str,
        prompt: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Invoke one DesignProductManager-private expert through the shared dispatcher."""
        clean_agent_name = str(agent_name or "").strip()
        if not is_design_product_expert(clean_agent_name):
            return {
                "status": "error",
                "message": (
                    f"DesignProductManager cannot invoke expert '{clean_agent_name}'. "
                    f"Allowed experts: {', '.join(DESIGN_PRODUCT_EXPERT_ALLOWLIST)}."
                ),
                "allowed_experts": list(DESIGN_PRODUCT_EXPERT_ALLOWLIST),
            }

        design_expert_agents = self._available_design_expert_agents()
        if clean_agent_name not in design_expert_agents:
            return {
                "status": "error",
                "message": (
                    f"Design expert '{clean_agent_name}' is allowed but not available "
                    "in the current runtime."
                ),
                "allowed_experts": list(DESIGN_PRODUCT_EXPERT_ALLOWLIST),
            }

        invocation = await dispatch_expert_call(
            agent_name=clean_agent_name,
            prompt=str(prompt or "").strip(),
            tool_context=tool_context,
            expert_agents=design_expert_agents,
            app_name=self._app_name,
            artifact_service=self._artifact_service or InMemoryArtifactService(),
        )
        history = list(tool_context.state.get(DESIGN_PRODUCT_EXPERT_HISTORY_STATE_KEY) or [])
        history.append(invocation.tool_result)
        tool_context.state[DESIGN_PRODUCT_EXPERT_HISTORY_STATE_KEY] = history
        tool_context.state[DESIGN_PRODUCT_LAST_EXPERT_RESULT_STATE_KEY] = invocation.tool_result
        if invocation.tool_result.get("output_files"):
            tool_context.state["design_product_generation"] = invocation.tool_result
        return invocation.tool_result

    async def invoke_design_code_generation(
        self,
        prompt: str,
        tool_context: ToolContext,
        language: str = "html",
        output_path: str = "",
        context_files: list[str] | str | None = None,
        constraints: list[str] | str | None = None,
    ) -> dict[str, Any]:
        """Invoke the DesignProductManager-private code generation agent."""
        clean_prompt = _append_selected_design_system_context_to_codegen_prompt(
            str(prompt or "").strip(),
            tool_context.state,
        )
        clean_constraints = _append_selected_design_system_constraints(
            _coerce_string_list(constraints),
            tool_context.state,
        )
        current_output = await self._design_code_generation_agent.run_generation(
            tool_context,
            prompt=clean_prompt,
            language=str(language or "html").strip() or "html",
            output_path=str(output_path or "").strip(),
            context_files=_coerce_string_list(context_files),
            constraints=clean_constraints,
        )
        if current_output.get("output_files"):
            current_output = {
                **current_output,
                "output_files": _record_output_files(
                    tool_context.state,
                    list(current_output.get("output_files") or []),
                ),
            }
            tool_context.state["design_product_generation"] = current_output

        history = list(tool_context.state.get(DESIGN_PRODUCT_CODE_GENERATION_HISTORY_STATE_KEY) or [])
        history.append(current_output)
        tool_context.state[DESIGN_PRODUCT_CODE_GENERATION_HISTORY_STATE_KEY] = history
        tool_context.state[DESIGN_PRODUCT_LAST_CODE_GENERATION_RESULT_STATE_KEY] = current_output
        tool_context.state["current_output"] = current_output
        return current_output

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
            "experts": tool_context.state.get(DESIGN_PRODUCT_EXPERTS_STATE_KEY) or [],
            "expert_history": list(tool_context.state.get(DESIGN_PRODUCT_EXPERT_HISTORY_STATE_KEY) or []),
            "last_expert_result": tool_context.state.get(DESIGN_PRODUCT_LAST_EXPERT_RESULT_STATE_KEY) or {},
            "code_generation_history": list(
                tool_context.state.get(DESIGN_PRODUCT_CODE_GENERATION_HISTORY_STATE_KEY) or []
            ),
            "last_code_generation_result": tool_context.state.get(
                DESIGN_PRODUCT_LAST_CODE_GENERATION_RESULT_STATE_KEY
            )
            or {},
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

    def _available_design_expert_agents(self) -> dict[str, BaseAgent]:
        """Return runtime experts that DesignProductManager is allowed to invoke."""
        return _filter_design_expert_agents(self._expert_agents)


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
            "You own skill selection, private expert usage, design decisions, generation, validation, progress, and final registration.",
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
        "experts": [],
        "expert_history": [],
        "last_expert_result": {},
        "code_generation_history": [],
        "last_code_generation_result": {},
        "generation": {},
        "validation": [],
        "output_files": [],
    }


def _brief_form_result(form_message: str) -> dict[str, Any]:
    """Build a DesignProductManager result that asks the Web UI for form answers."""
    return {
        "result_schema_version": DESIGN_PRODUCT_RESULT_SCHEMA_VERSION,
        "status": "needs_input",
        "product_line": "design",
        "message": form_message,
        "final_file_paths": [],
        "progress": [
            {
                "stage": "brief_form",
                "status": "needs_input",
                "message": "已生成设计需求确认表单，等待用户在 Web 前端提交。",
            }
        ],
        "active_skill": {},
        "experts": [],
        "expert_history": [],
        "last_expert_result": {},
        "code_generation_history": [],
        "last_code_generation_result": {},
        "generation": {},
        "validation": [],
        "output_files": [],
    }


def _should_request_web_brief_form(state: Any) -> bool:
    """Return whether the Web design flow should ask for a generated brief form."""
    channel = str(state.get("channel", "") or "").strip().lower()
    if channel != "web":
        return False
    if state.get(DESIGN_BRIEF_FORM_ANSWERS_STATE_KEY):
        return False
    if state.get(DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY):
        return False
    return True


def _resolve_selected_design_system_context(answer_payload: dict[str, Any]) -> dict[str, str] | None:
    """Resolve the selected local design system from submitted Web form answers."""
    answers = answer_payload.get("answers")
    if not isinstance(answers, dict):
        return None
    raw_value = answers.get("design_system_reference")
    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else ""
    design_system_id = str(raw_value or "").strip()
    if not design_system_id or design_system_id in {"decide_for_me", "other"}:
        return None

    design_system_body = read_design_system(design_system_id)
    if not design_system_body:
        return None

    title = next(
        (item.title for item in list_design_systems() if item.id == design_system_id),
        design_system_id,
    )
    return {
        "id": design_system_id,
        "title": title,
        "body": design_system_body.strip(),
    }


def _append_selected_design_system_summary(task: str, selected_design_system: dict[str, str]) -> str:
    """Append a lightweight selected design-system summary to the manager task."""
    design_system_id = str(selected_design_system.get("id") or "").strip()
    title = str(selected_design_system.get("title") or design_system_id).strip()
    if not design_system_id or "# Selected design system" in str(task or ""):
        return task
    return "\n".join(
        [
            task.strip(),
            "",
            "# Selected design system",
            f"Use {title} ({design_system_id}). The full DESIGN.md is stored in session state and will be injected automatically when invoking design code generation.",
        ]
    ).strip()


def _append_selected_design_system_context_to_codegen_prompt(prompt: str, state: Any) -> str:
    """Append authoritative DESIGN.md context to the private code-generation prompt."""
    selected_design_system = _selected_design_system_from_state(state)
    if not selected_design_system:
        return prompt
    design_system_id = selected_design_system["id"]
    title = selected_design_system["title"]
    body = selected_design_system["body"]
    marker = "# Selected design system (authoritative DESIGN.md)"
    if marker in prompt and design_system_id in prompt:
        return prompt
    return "\n".join(
        [
            prompt.strip(),
            "",
            marker,
            f"Design system: {title} ({design_system_id})",
            "Treat this DESIGN.md as authoritative for palette, typography, spacing, component posture, and design tokens. Do not invent conflicting tokens unless the user explicitly asks to override it.",
            body,
        ]
    ).strip()


def _append_selected_design_system_constraints(constraints: list[str], state: Any) -> list[str]:
    """Add a hard code-generation constraint for the selected design system."""
    selected_design_system = _selected_design_system_from_state(state)
    if not selected_design_system:
        return constraints
    constraint = (
        f"Use the selected design system {selected_design_system['title']} "
        f"({selected_design_system['id']}) as the authoritative visual system."
    )
    if any("selected design system" in item.lower() for item in constraints):
        return constraints
    return [*constraints, constraint]


def _selected_design_system_from_state(state: Any) -> dict[str, str] | None:
    """Return selected design-system context from ADK session state."""
    value = state.get(DESIGN_PRODUCT_SELECTED_DESIGN_SYSTEM_STATE_KEY)
    if not isinstance(value, dict):
        return None
    design_system_id = str(value.get("id") or "").strip()
    if not design_system_id:
        return None
    title = str(value.get("title") or design_system_id).strip()
    body = str(value.get("body") or "").strip() or read_design_system(design_system_id)
    if not body:
        return None
    return {
        "id": design_system_id,
        "title": title,
        "body": body.strip(),
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


def _filter_design_expert_agents(
    expert_agents: dict[str, BaseAgent] | None,
) -> dict[str, BaseAgent]:
    """Return only runtime experts exposed to DesignProductManager."""
    return {
        name: agent
        for name, agent in dict(expert_agents or {}).items()
        if is_design_product_expert(name)
    }


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


__all__ = [
    "DESIGN_PRODUCT_RESULT_SCHEMA_VERSION",
    "DesignProductManager",
]
