"""ADK-native product manager skeleton for Creative Claw PPT tasks."""

from __future__ import annotations

import copy
import html as html_lib
import inspect
import json
import re
from pathlib import Path
from typing import Any

from google.adk.apps import App
from google.adk.agents import BaseAgent, LlmAgent, SequentialAgent
from google.adk.artifacts import BaseArtifactService, InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai.types import Content, Part
from pydantic import PrivateAttr

from conf.llm import build_llm
from conf.path import PROJECT_PATH
from src.productions.ppt.planning import PptContentPlanner
from src.productions.ppt.ppt_product_manager.product_ppt_skills import (
    ProductPptSkillRegistry,
)
from src.productions.ppt.routes.html import build_html_route_with_agent
from src.productions.ppt.routes import PptRouteRegistration, build_default_ppt_route_registry
from src.productions.ppt.schemas import (
    ConfirmedRequirement,
    DeckContentPlan,
    DeliveryManifest,
    EditabilityRequirement,
    PptProductResult,
    QualityReviewResult,
    ReferenceAsset,
    SlideCountPolicy,
    SourceInput,
    SourceUnderstanding,
    StyleRequirement,
    TemplateRequirement,
)
from src.productions.ppt.schemas.contracts import PPT_PRODUCT_RESULT_SCHEMA_VERSION
from src.runtime.expert_dispatcher import dispatch_expert_call
from src.runtime.tool_context_artifact_service import ToolContextArtifactService
from src.runtime.workspace import (
    build_workspace_file_record,
    generated_session_dir,
    resolve_workspace_path,
    stage_attachment_into_workspace,
    workspace_relative_path,
)

PPT_CONFIRMED_REQUIREMENT_STATE_KEY = "ppt_confirmed_requirement"
PPT_PRODUCT_RESULT_STATE_KEY = "ppt_product_result"
PPT_WORKFLOW_STATE_KEY = "ppt_workflow_state"
PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION = "awaiting_requirement_confirmation"
PPT_STAGE_AWAITING_CONTENT_PLAN_CONFIRMATION = "awaiting_content_plan_confirmation"
PPT_STAGE_COMPLETED = "completed"
PPT_WORKFLOW_WAITING_SINCE_TURN_KEY = "waiting_since_turn_index"
PPT_WORKFLOW_LAST_CONSUMED_TURN_KEY = "last_consumed_turn_index"
PPT_REQUIREMENT_ANALYSIS_BASE_KEY = "ppt_requirement_analysis_base"
PPT_REQUIREMENT_ANALYSIS_AGENT_MESSAGE_KEY = "ppt_requirement_analysis_agent_message"
PPT_PRODUCT_SKILLS_STATE_KEY = "product_ppt_skills"
PPT_PRODUCT_ACTIVE_SKILL_STATE_KEY = "active_product_ppt_skill"
PPT_SYSTEM_SELECTION_STATE_KEY = "ppt_system_selection"
PPT_SYSTEM_SELECTION_BASE_KEY = "ppt_system_selection_base"
PPT_SYSTEM_SELECTION_AGENT_MESSAGE_KEY = "ppt_system_selection_agent_message"
PPT_PRIVATE_SKILL_BUILD_STATE_KEY = "ppt_private_skill_build"
PPT_PRIVATE_SKILL_MASKED_HTML_COUNT_STATE_KEY = "ppt_private_skill_masked_html_content_count"
PPT_PRIVATE_SKILL_HTML_CONTENT_MASK_THRESHOLD = 8000
PPT_PRIVATE_SKILL_HTML_SAVE_TOOL_NAME = "save_ppt_private_skill_html"


class PptProductManager(LlmAgent):
    """ADK LlmAgent that owns PPT product-line requests."""

    _project_root: Path = PrivateAttr()
    _content_planner: PptContentPlanner = PrivateAttr()
    _route_registry: dict[str, PptRouteRegistration] = PrivateAttr(default_factory=dict)
    _skill_registry: ProductPptSkillRegistry = PrivateAttr()
    _skill_runtime_expert_agents: dict[str, BaseAgent] = PrivateAttr(default_factory=dict)
    _skill_runtime_app_name: str = PrivateAttr(default="creative_claw")
    _skill_runtime_artifact_service: BaseArtifactService | None = PrivateAttr(default=None)

    def __init__(
        self,
        project_root: str | Path | None = None,
        skills_dir: str | Path | None = None,
        route_registry: dict[str, PptRouteRegistration] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the ADK PPT product manager."""
        provided_tools = kwargs.pop("tools", None)
        super().__init__(
            name=kwargs.pop("name", "PptProductManager"),
            model=kwargs.pop("model", build_llm()),
            description=kwargs.pop("description", "Owns PPT product requests and route dispatch."),
            instruction=kwargs.pop("instruction", type(self).build_instruction()),
            tools=provided_tools or [],
            include_contents=kwargs.pop("include_contents", "none"),
            before_model_callback=kwargs.pop(
                "before_model_callback",
                _mask_private_skill_html_content_before_model,
            ),
            **kwargs,
        )
        self.project_root = Path(project_root or PROJECT_PATH).resolve()
        self._content_planner = PptContentPlanner()
        self._route_registry = dict(route_registry or build_default_ppt_route_registry())
        self._skill_registry = ProductPptSkillRegistry(
            project_root=self.project_root,
            skills_dir=skills_dir,
        )
        if provided_tools is None:
            self.tools = [
                self.list_product_ppt_skills,
                self.read_product_ppt_skill,
                self.read_product_ppt_skill_file,
                self.list_ppt_experts,
                self.invoke_ppt_expert,
                self.save_ppt_system_selection,
                self.save_ppt_private_skill_html,
                self.dispatch_ppt_route,
            ]

    @property
    def project_root(self) -> Path:
        """Return the resolved project root used by deterministic product tools."""
        return self._project_root

    @project_root.setter
    def project_root(self, value: Path) -> None:
        """Set the resolved project root used by deterministic product tools."""
        self._project_root = value

    @property
    def content_planner(self) -> PptContentPlanner:
        """Return the content planner used by the current MVP execution path."""
        return self._content_planner

    @property
    def skill_registry(self) -> ProductPptSkillRegistry:
        """Return the private product-ppt skill registry."""
        return self._skill_registry

    @staticmethod
    def build_instruction() -> str:
        """Return the ADK-facing product manager instruction."""
        return """
You are Creative Claw's PptProductManager.

# Role
Own PPT and PowerPoint production end to end. If the requested final deliverable is `.pptx`, PowerPoint, PPT, or an editable slide deck, this product line is the default path unless the user explicitly asks for a non-PPTX HTML design artifact.

# ADK workflow
- Use deterministic tools and session state for stage contracts.
- Treat `ConfirmedRequirement` as the requirement source of truth.
- Treat `DeckContentPlan` as template-independent content truth.
- Pause for user confirmation after `ConfirmedRequirement` is prepared.
- Pause again after `DeckContentPlan` is prepared, before searching or generating images.
- Dispatch exactly one route pipeline per task.
- Treat route implementation status as one input to the PPT system-selection step.
- Do not expose route-internal editing tools at the top product-manager layer.

# Route policy
- HTML route: currently implemented built-in route; when selected, use no-template free design unless a system HTML template is explicitly selected; export to PPTX with explicit editability caveats.
- SVG route: later route for high-control SVG pages and SVG-to-PPTX.
- XML route: later route for user-uploaded PPTX templates and native OOXML editing.

# PPT system selection
- Creative Claw currently has multiple PPT-making systems under this product line.
- Private product-ppt skills live under `skills/product-ppt-skills/<skill-name>/SKILL.md`; those skills may describe a complete PPT production workflow.
- The product manager also owns the built-in HTML route, which generates an HTML deck, previews, quality report, and editable PPTX.
- Use only your private product-ppt skills, exposed through `list_product_ppt_skills` and `read_product_ppt_skill`.
- Do not ask the orchestrator to read PPT private skills for you.
- Before committing to a delivery system, run a PPT system-selection step. Base the decision on the user's actual task, available private skill names/descriptions/content, and registered built-in routes.
- If the user explicitly names a PPT system, route, skill, template workflow, or output method, follow that choice when it is available and report clearly when it is not implemented.
- If the user does not specify the PPT system, freely choose between the private PPT skill workflow and the built-in HTML route based on task fit. This selection policy is intentionally flexible for later testing and optimization.
- Do not rely on hard-coded keyword-to-skill rules. Inspect the available private skills and choose from their actual metadata and content.

# Private skill execution
- When a private product-ppt skill is selected, you run that skill workflow directly as PptProductManager.
- Let the selected skill drive the execution order: read its referenced files, call available PPT product tools, call `invoke_ppt_expert` when it needs a registered expert, and save the final artifact with `save_ppt_private_skill_html`.
- Do not delegate selected private skill execution to a separate private execution agent.
- Do not invent facts, citations, local absolute paths, unavailable resources, or generated file paths.

# Result policy
Return structured status, current phase, selected route, warnings, next actions, and delivery manifest. Do not claim PPTX generation succeeded unless a route pipeline produced and validated a file.
""".strip()

    def build_agent(self, *, tools: list[Any] | None = None) -> LlmAgent:
        """Return this product manager as the ADK LlmAgent instance."""
        if tools is not None:
            self.tools = tools
        return self

    def build_requirement_analysis_agent(self) -> LlmAgent:
        """Build the product-internal ADK agent that writes ConfirmedRequirement JSON."""
        return LlmAgent(
            name="PptRequirementAnalysisAgent",
            model=build_llm(),
            instruction=(
                "You are Creative Claw's PPT requirement analysis agent.\n"
                "Normalize the user's PPT request into one complete ConfirmedRequirement JSON object.\n"
                "For revision turns, start from the existing ConfirmedRequirement and apply only the user's requested changes.\n"
                "Do not append revision text to the topic. Keep topic concise and audience-facing.\n"
                "Separate task description from source documents: files and URLs are source_inputs, not slide content by themselves.\n"
                "Preserve source_inputs, source_understanding, reference_assets, output_format, and safe defaults from the provided fallback JSON unless the user explicitly changes route, template, aspect ratio, language, page count, audience, scenario, topic, or style.\n"
                "Always call save_ppt_confirmed_requirement_json with one argument named requirement_json.\n"
                "The JSON must include route, request_brief, topic, audience, scenario, slide_count_policy, language, aspect_ratio, output_format, template_requirement, style_requirement, editability_requirement, and confirmed_by_user.\n"
                "Creative Claw has multiple PPT systems: private product-ppt skills and the built-in HTML route.\n"
                "If the user explicitly names a route, skill, template workflow, or PPT system, preserve that choice in the normalized requirement when the schema can represent it.\n"
                "If the user does not specify the system, keep route normalization conservative; the separate PPT system-selection agent chooses the delivery system from task fit.\n"
                "If the user says 受众为/受众设置为, write that value to audience. If the user says 场景为/场景设置为, write that value to scenario.\n"
                "For Chinese group meeting requests, scenario should be `组会`.\n"
                "Do not invent source file paths or generated artifacts."
            ),
            tools=[self.save_ppt_confirmed_requirement_json],
            output_key=PPT_REQUIREMENT_ANALYSIS_AGENT_MESSAGE_KEY,
            include_contents="none",
        )

    def build_system_selection_agent(self) -> LlmAgent:
        """Build the product-internal ADK agent that chooses a PPT delivery system."""
        return LlmAgent(
            name="PptSystemSelectionAgent",
            model=build_llm(),
            instruction=(
                "You are Creative Claw's PPT system selection agent.\n"
                "Choose the best delivery system for one PPT request.\n"
                "You can choose a built-in route or one private product-ppt skill.\n"
                "Always call list_product_ppt_skills first.\n"
                "Read the most relevant private skill with read_product_ppt_skill when the user mentions a skill, "
                "asks for a style/workflow that a skill may cover, or when skill metadata looks relevant.\n"
                "Do not use hard-coded keyword rules. Decide from the user task, output request, available route summaries, "
                "private skill names/descriptions, and any skill content you read.\n"
                "If the user explicitly asks for an available private skill, choose system_type `private_skill` and its exact folder name.\n"
                "If the user explicitly asks for a built-in route, choose system_type `built_in_route` and the route when available.\n"
                "If nothing is explicit, choose freely based on task fit.\n"
                "Private HTML/web deck skills may produce a final single-file HTML presentation instead of PPTX.\n"
                "When ready, call save_ppt_system_selection with one argument named selection_json.\n"
                "The JSON must include system_type, route, skill_name, output_format, and reason."
            ),
            tools=[
                self.list_product_ppt_skills,
                self.read_product_ppt_skill,
                self.save_ppt_system_selection,
            ],
            output_key=PPT_SYSTEM_SELECTION_AGENT_MESSAGE_KEY,
        )

    def build_html_mvp_workflow(self) -> SequentialAgent:
        """Build the intended ADK SequentialAgent skeleton for the HTML MVP route."""
        requirement_agent = LlmAgent(
            name="PptRequirementAnalysisAgent",
            model=build_llm(),
            instruction=(
                "Normalize the user PPT request into ConfirmedRequirement. "
                "Use source understanding when available and do not plan individual slides."
            ),
            output_key=PPT_CONFIRMED_REQUIREMENT_STATE_KEY,
            include_contents="none",
        )
        content_agent = self.content_planner.build_agent()
        quality_agent = LlmAgent(
            name="PptQualityDeliveryAgent",
            model=build_llm(),
            instruction=(
                "Review PPT route artifacts and prepare a DeliveryManifest. "
                "For the current skeleton, report not_run when no route output exists."
            ),
            output_key="ppt_quality_delivery",
            include_contents="none",
        )
        return SequentialAgent(
            name="PptHtmlMvpSequentialAgent",
            sub_agents=[requirement_agent, content_agent, quality_agent],
        )

    def save_ppt_confirmed_requirement_json(
        self,
        requirement_json: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Validate and save the requirement JSON produced by the requirement agent."""
        raw_payload: Any = requirement_json
        if isinstance(raw_payload, str):
            raw_payload = json.loads(raw_payload)
        if not isinstance(raw_payload, dict):
            raise ValueError("requirement_json must be a JSON object.")

        base_payload = dict(tool_context.state.get(PPT_REQUIREMENT_ANALYSIS_BASE_KEY) or {})
        fallback_requirement = ConfirmedRequirement.model_validate(
            base_payload.get("fallback_requirement") or {}
        )
        requirement = self._merge_requirement_payload(
            raw_payload,
            fallback_requirement=fallback_requirement,
        )
        requirement_payload = requirement.model_dump(mode="json")
        tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = requirement_payload
        tool_context.state["ppt_requirement_analysis_output"] = {
            "status": "success",
            "message": "PptRequirementAnalysisAgent saved ConfirmedRequirement.",
            "source": "llm_agent",
        }
        return {
            "status": "success",
            "message": "ConfirmedRequirement saved.",
            "confirmed_requirement": requirement_payload,
        }

    async def prepare_confirmed_requirement_with_agent(
        self,
        *,
        task: str,
        inputs: Any | None = None,
        output: dict[str, Any] | None = None,
        source_understanding: SourceUnderstanding | None = None,
        tool_context: ToolContext,
        app_name: str,
        artifact_service: BaseArtifactService | None,
    ) -> ConfirmedRequirement:
        """Prepare ConfirmedRequirement through the ADK requirement agent when possible."""
        fallback_requirement = self.prepare_confirmed_requirement(
            task=task,
            inputs=inputs,
            output=output,
            source_understanding=source_understanding,
        )
        return await self._run_requirement_analysis_agent(
            mode="initial",
            task=task,
            raw_inputs=self._normalize_raw_inputs(inputs),
            output=dict(output or {}),
            fallback_requirement=fallback_requirement,
            existing_requirement=None,
            user_revision="",
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
        )

    async def revise_confirmed_requirement_with_agent(
        self,
        *,
        existing_requirement: ConfirmedRequirement,
        user_response: str,
        task: str,
        raw_inputs: list[Any],
        output: dict[str, Any],
        source_understanding: SourceUnderstanding,
        tool_context: ToolContext,
        app_name: str,
        artifact_service: BaseArtifactService | None,
    ) -> ConfirmedRequirement:
        """Revise ConfirmedRequirement through structured JSON instead of task appending."""
        fallback_requirement = self._revise_confirmed_requirement_deterministically(
            existing_requirement,
            user_response=user_response,
            raw_inputs=raw_inputs,
            output=output,
            source_understanding=source_understanding,
        )
        return await self._run_requirement_analysis_agent(
            mode="revision",
            task=task,
            raw_inputs=raw_inputs,
            output=output,
            fallback_requirement=fallback_requirement,
            existing_requirement=existing_requirement,
            user_revision=user_response,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
        )

    async def _run_requirement_analysis_agent(
        self,
        *,
        mode: str,
        task: str,
        raw_inputs: list[Any],
        output: dict[str, Any],
        fallback_requirement: ConfirmedRequirement,
        existing_requirement: ConfirmedRequirement | None,
        user_revision: str,
        tool_context: ToolContext,
        app_name: str,
        artifact_service: BaseArtifactService | None,
    ) -> ConfirmedRequirement:
        """Run the internal requirement agent, falling back to deterministic JSON patches."""
        if not hasattr(tool_context, "_invocation_context"):
            tool_context.state["ppt_requirement_analysis_output"] = {
                "status": "fallback",
                "message": "Requirement analysis agent skipped because no ADK invocation context was available.",
                "source": "deterministic_fallback",
            }
            return fallback_requirement

        invocation_context = tool_context._invocation_context
        child_session_service = InMemorySessionService()
        child_artifact_service = _resolve_child_artifact_service(
            tool_context=tool_context,
            fallback_service=artifact_service or InMemoryArtifactService(),
        )
        requirement_agent = self.build_requirement_analysis_agent()
        child_runner = _build_child_runner(
            agent=requirement_agent,
            app_name=app_name,
            session_service=child_session_service,
            artifact_service=child_artifact_service,
            invocation_context=invocation_context,
        )
        child_state = _copy_state(tool_context.state)
        child_state[PPT_REQUIREMENT_ANALYSIS_BASE_KEY] = {
            "mode": mode,
            "task": task,
            "raw_inputs": raw_inputs,
            "output": output,
            "user_revision": user_revision,
            "fallback_requirement": fallback_requirement.model_dump(mode="json"),
            "existing_requirement": (
                existing_requirement.model_dump(mode="json") if existing_requirement is not None else {}
            ),
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
                            text=_build_requirement_analysis_user_message(
                                mode=mode,
                                task=task,
                                raw_inputs=raw_inputs,
                                output=output,
                                fallback_requirement=fallback_requirement,
                                existing_requirement=existing_requirement,
                                user_revision=user_revision,
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
            requirement_payload = final_state.get(PPT_CONFIRMED_REQUIREMENT_STATE_KEY)
            if not requirement_payload:
                raise ValueError("PptRequirementAnalysisAgent did not save ConfirmedRequirement.")
            requirement = ConfirmedRequirement.model_validate(requirement_payload)
            tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = requirement.model_dump(mode="json")
            if final_state.get(PPT_REQUIREMENT_ANALYSIS_AGENT_MESSAGE_KEY):
                tool_context.state[PPT_REQUIREMENT_ANALYSIS_AGENT_MESSAGE_KEY] = str(
                    final_state.get(PPT_REQUIREMENT_ANALYSIS_AGENT_MESSAGE_KEY)
                )
            tool_context.state["ppt_requirement_analysis_output"] = {
                "status": "success",
                "message": "PptRequirementAnalysisAgent produced ConfirmedRequirement.",
                "source": "llm_agent",
            }
            return requirement
        except Exception as exc:
            tool_context.state["ppt_requirement_analysis_output"] = {
                "status": "fallback",
                "message": f"Requirement analysis agent fallback: {type(exc).__name__}: {exc}",
                "source": "deterministic_fallback",
            }
            return fallback_requirement
        finally:
            await child_runner.close()

    async def select_ppt_system_with_agent(
        self,
        *,
        task: str,
        output: dict[str, Any],
        requirement: ConfirmedRequirement,
        tool_context: ToolContext,
        app_name: str,
        artifact_service: BaseArtifactService | None,
        system_selection_builder: Any | None = None,
    ) -> dict[str, Any]:
        """Choose the PPT delivery system through an internal agent or injected selector."""
        if system_selection_builder is not None:
            selected = await _call_system_selection_builder(
                system_selection_builder,
                task=task,
                output=output,
                requirement=requirement,
                private_skills=[skill.to_dict() for skill in self.skill_registry.list_skills()],
                routes=self.list_registered_routes(),
            )
            return self._persist_system_selection(tool_context, selected)

        fallback_selection = self._build_default_system_selection(requirement)
        if not hasattr(tool_context, "_invocation_context"):
            return self._persist_system_selection(tool_context, fallback_selection)

        invocation_context = tool_context._invocation_context
        child_session_service = InMemorySessionService()
        child_artifact_service = _resolve_child_artifact_service(
            tool_context=tool_context,
            fallback_service=artifact_service or InMemoryArtifactService(),
        )
        selection_agent = self.build_system_selection_agent()
        child_runner = _build_child_runner(
            agent=selection_agent,
            app_name=app_name,
            session_service=child_session_service,
            artifact_service=child_artifact_service,
            invocation_context=invocation_context,
        )
        child_state = _copy_state(tool_context.state)
        child_state[PPT_SYSTEM_SELECTION_BASE_KEY] = {
            "task": task,
            "output": output,
            "confirmed_requirement": requirement.model_dump(mode="json"),
            "registered_routes": self.list_registered_routes(),
            "fallback_selection": fallback_selection,
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
                            text=_build_system_selection_user_message(
                                task=task,
                                output=output,
                                requirement=requirement,
                                route_summaries=self.list_registered_routes(),
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
            selection_payload = final_state.get(PPT_SYSTEM_SELECTION_STATE_KEY)
            if not selection_payload:
                raise ValueError("PptSystemSelectionAgent did not save a selection.")
            selection = self._normalize_system_selection(selection_payload, fallback_selection=fallback_selection)
            tool_context.state[PPT_SYSTEM_SELECTION_STATE_KEY] = selection
            if final_state.get(PPT_SYSTEM_SELECTION_AGENT_MESSAGE_KEY):
                tool_context.state[PPT_SYSTEM_SELECTION_AGENT_MESSAGE_KEY] = str(
                    final_state.get(PPT_SYSTEM_SELECTION_AGENT_MESSAGE_KEY)
                )
            if final_state.get(PPT_PRODUCT_SKILLS_STATE_KEY):
                tool_context.state[PPT_PRODUCT_SKILLS_STATE_KEY] = final_state[PPT_PRODUCT_SKILLS_STATE_KEY]
            if final_state.get(PPT_PRODUCT_ACTIVE_SKILL_STATE_KEY):
                tool_context.state[PPT_PRODUCT_ACTIVE_SKILL_STATE_KEY] = final_state[PPT_PRODUCT_ACTIVE_SKILL_STATE_KEY]
            return selection
        except Exception as exc:
            selection = {
                **fallback_selection,
                "reason": (
                    f"{fallback_selection.get('reason', '')} "
                    f"System selection agent fallback: {type(exc).__name__}: {exc}"
                ).strip(),
            }
            return self._persist_system_selection(tool_context, selection)
        finally:
            await child_runner.close()

    async def run_product_request(
        self,
        *,
        task: str,
        inputs: Any | None = None,
        output: dict[str, Any] | None = None,
        tool_context: ToolContext | None = None,
        expert_agents: dict[str, BaseAgent] | None = None,
        app_name: str = "creative_claw",
        artifact_service: InMemoryArtifactService | None = None,
        source_converter: Any | None = None,
        content_plan_builder: Any | None = None,
        asset_resolver: Any | None = None,
        system_selection_builder: Any | None = None,
    ) -> dict[str, Any]:
        """Accept one PPT product task and return the current structured status."""
        if tool_context is None:
            return {
                "status": "error",
                "message": "PptProductManager requires tool context.",
                "result_schema_version": PPT_PRODUCT_RESULT_SCHEMA_VERSION,
            }

        clean_task = str(task or "").strip()
        if not clean_task:
            return {
                "status": "error",
                "message": "PptProductManager requires a non-empty task.",
                "result_schema_version": PPT_PRODUCT_RESULT_SCHEMA_VERSION,
            }

        output_options = dict(output or {})
        if not self._should_auto_confirm(output_options):
            workflow_state = self._get_workflow_state(tool_context.state)
            if self._is_pending_confirmation_stage(workflow_state.get("stage")):
                return await self.continue_product_request(
                    user_response=clean_task,
                    tool_context=tool_context,
                    expert_agents=expert_agents,
                    app_name=app_name,
                    artifact_service=artifact_service,
                    source_converter=source_converter,
                    content_plan_builder=content_plan_builder,
                    asset_resolver=asset_resolver,
                )
            return await self._start_interactive_product_request(
                task=clean_task,
                inputs=inputs,
                output=output_options,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
                system_selection_builder=system_selection_builder,
            )

        try:
            raw_inputs = self._normalize_raw_inputs(inputs)
            source_inputs = self._normalize_source_inputs(raw_inputs)
            source_inputs = self._stage_source_inputs_for_workspace(source_inputs, tool_context.state)
            source_converter = source_converter or self._build_source_converter(
                tool_context=tool_context,
                expert_agents=expert_agents or {},
                app_name=app_name,
                artifact_service=artifact_service,
            )
            source_materials = await self._prepare_source_materials(
                source_inputs,
                fallback_document_type=self._infer_document_type(source_inputs),
                tool_context=tool_context,
                source_converter=source_converter,
            )
            tool_context.state["ppt_source_materials"] = source_materials.model_dump(mode="json")
            tool_context.state["ppt_source_markdown_sources"] = source_materials.markdown_sources
            tool_context.state["ppt_source_figures"] = source_materials.figures
            tool_context.state["ppt_source_output_files"] = source_materials.output_files
            requirement = await self.prepare_confirmed_requirement_with_agent(
                task=clean_task,
                inputs=raw_inputs,
                output=output_options,
                source_understanding=source_materials,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
            )
            requirement = requirement.model_copy(update={"source_inputs": source_inputs})
            tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = requirement.model_dump(mode="json")
            clarification_questions = self.validate_confirmed_requirement(requirement)
            if clarification_questions:
                result = PptProductResult(
                    status="needs_clarification",
                    phase="requirement_confirmation",
                    message="PptProductManager needs a clearer PPT topic or source material before generation.",
                    selected_route=requirement.route,
                    confirmed_requirement=requirement,
                    delivery_manifest=DeliveryManifest(),
                    warnings=[],
                    next_actions=clarification_questions,
                )
            else:
                system_selection = await self.select_ppt_system_with_agent(
                    task=clean_task,
                    output=output_options,
                    requirement=requirement,
                    tool_context=tool_context,
                    app_name=app_name,
                    artifact_service=artifact_service,
                    system_selection_builder=system_selection_builder,
                )
                requirement = self._apply_system_selection_to_requirement(requirement, system_selection)
                tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = requirement.model_dump(mode="json")
                if self._is_private_skill_selection(system_selection):
                    content_plan = await self.build_deck_content_plan(
                        requirement,
                        tool_context=tool_context,
                        app_name=app_name,
                        artifact_service=artifact_service,
                        expert_agents=expert_agents or {},
                        content_plan_builder=content_plan_builder,
                        resolve_assets=False,
                    )
                    private_build = await self.execute_private_ppt_skill(
                        requirement=requirement,
                        content_plan=content_plan,
                        system_selection=system_selection,
                        tool_context=tool_context,
                        expert_agents=expert_agents or {},
                        app_name=app_name,
                        artifact_service=artifact_service,
                    )
                    result = self._build_private_skill_delivery_result(
                        requirement=requirement,
                        content_plan=content_plan,
                        system_selection=system_selection,
                        private_build=private_build,
                    )
                else:
                    route_registration = self._route_registry.get(requirement.route)
                    if route_registration is None or not route_registration.implemented:
                        result = self._build_route_not_implemented_result(requirement, route_registration)
                    else:
                        content_plan = await self.build_deck_content_plan(
                            requirement,
                            tool_context=tool_context,
                            app_name=app_name,
                            artifact_service=artifact_service,
                            expert_agents=expert_agents or {},
                            content_plan_builder=content_plan_builder,
                            asset_resolver=asset_resolver,
                        )
                        output_dir = self._build_route_output_dir(tool_context.state)
                        route_build = await self._dispatch_ppt_route(
                            requirement=requirement,
                            content_plan=content_plan,
                            output_dir=output_dir,
                            tool_context=tool_context,
                            app_name=app_name,
                            artifact_service=artifact_service,
                        )
                        route_succeeded = bool(route_build.pptx_path)
                        output_files = self._record_output_files(
                            tool_context.state,
                            [
                                route_build.pptx_path,
                                route_build.html_deck_path,
                                route_build.quality_report_path,
                                route_build.build_log_path,
                                *route_build.preview_paths,
                            ],
                        )
                        delivery_manifest = DeliveryManifest(
                            final_pptx=route_build.pptx_path,
                            previews=route_build.preview_paths,
                            quality_report=route_build.quality_report_path,
                            build_log=route_build.build_log_path,
                            intermediate_artifacts=[route_build.html_deck_path],
                            output_files=output_files,
                        )
                        result = PptProductResult(
                            status="success" if route_succeeded else "generation_failed",
                            phase="html_route_delivery",
                            message=(
                                "HTML route MVP generated an HTML deck, PNG previews, and an editable PPTX."
                                if route_succeeded
                                else "HTML route generated HTML and previews, but failed to export an editable PPTX. See the build log for conversion findings."
                            ),
                            selected_route=requirement.route,
                            confirmed_requirement=requirement,
                            deck_content_plan=content_plan,
                            route_build=route_build,
                            quality_review=QualityReviewResult(
                                status="pass" if route_succeeded else "failed",
                                page_count_ok=route_succeeded,
                                file_open_ok=route_succeeded,
                                text_complete_ok=route_succeeded,
                                assets_ok=route_succeeded,
                                placeholder_free_ok=route_succeeded,
                                overflow_ok=None,
                                style_consistency_ok=route_succeeded,
                            ),
                            delivery_manifest=delivery_manifest,
                            output_files=output_files,
                            warnings=[
                                *list(route_build.warnings),
                                *list(requirement.source_understanding.extraction_warnings),
                                *list(tool_context.state.get("ppt_content_planning_warnings") or []),
                            ],
                            next_actions=(
                                ["Review the generated PPTX and previews; improve HTML template fidelity next."]
                                if route_succeeded
                                else ["Fix the HTML-to-PPTX conversion findings and retry PPTX export."]
                            ),
                        )
        except Exception as exc:
            result = PptProductResult(
                status="error",
                phase="ppt_product_execution",
                message=f"PPT product request normalization failed: {type(exc).__name__}: {exc}",
                selected_route="html",
                warnings=[str(exc)],
                next_actions=["Fix the malformed PPT product request and retry."],
            )

        result_payload = result.model_dump(mode="json")
        tool_context.state["product_line"] = "ppt"
        tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = (
            result_payload.get("confirmed_requirement") or {}
        )
        if result_payload.get("deck_content_plan"):
            tool_context.state["ppt_deck_content_plan"] = result_payload["deck_content_plan"]
        if result_payload.get("route_build"):
            tool_context.state["ppt_route_build"] = result_payload["route_build"]
        tool_context.state[PPT_PRODUCT_RESULT_STATE_KEY] = result_payload
        tool_context.state["current_output"] = result_payload
        tool_context.state["last_product_result"] = result_payload
        tool_context.state["last_output_message"] = str(result_payload.get("message") or "")
        return result_payload

    async def continue_product_request(
        self,
        *,
        user_response: str,
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent] | None = None,
        app_name: str = "creative_claw",
        artifact_service: InMemoryArtifactService | None = None,
        source_converter: Any | None = None,
        content_plan_builder: Any | None = None,
        asset_resolver: Any | None = None,
    ) -> dict[str, Any]:
        """Continue a paused PPT product workflow after a user confirmation turn."""
        workflow_state = self._get_workflow_state(tool_context.state)
        stage = str(workflow_state.get("stage") or "").strip()
        clean_response = str(user_response or "").strip()
        if not workflow_state or not stage:
            result = PptProductResult(
                status="error",
                phase="ppt_workflow_resume",
                message="没有找到等待确认的 PPT 工作流，请重新发起 PPT 任务。",
                selected_route="html",
                warnings=["Missing ppt_workflow_state."],
                next_actions=["重新发起 PPT 任务。"],
            )
            return self._persist_product_result(tool_context, result)

        if self._is_pending_confirmation_stage(stage) and self._is_waiting_for_later_user_turn(
            workflow_state,
            tool_context.state,
        ):
            result = self._build_current_confirmation_result(workflow_state)
            return self._persist_product_result(tool_context, result)

        workflow_state[PPT_WORKFLOW_LAST_CONSUMED_TURN_KEY] = self._current_turn_index(tool_context.state)

        if stage == PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION:
            return await self._continue_after_requirement_confirmation(
                user_response=clean_response,
                workflow_state=workflow_state,
                tool_context=tool_context,
                expert_agents=expert_agents or {},
                app_name=app_name,
                artifact_service=artifact_service,
                source_converter=source_converter,
                content_plan_builder=content_plan_builder,
            )
        if stage == PPT_STAGE_AWAITING_CONTENT_PLAN_CONFIRMATION:
            return await self._continue_after_content_plan_confirmation(
                user_response=clean_response,
                workflow_state=workflow_state,
                tool_context=tool_context,
                expert_agents=expert_agents or {},
                app_name=app_name,
                artifact_service=artifact_service,
                content_plan_builder=content_plan_builder,
                asset_resolver=asset_resolver,
            )

        result = PptProductResult(
            status="error",
            phase="ppt_workflow_resume",
            message=f"PPT 工作流当前阶段 `{stage}` 不能继续确认。",
            selected_route="html",
            warnings=[f"Unsupported PPT workflow stage: {stage}"],
            next_actions=["重新发起 PPT 任务。"],
        )
        return self._persist_product_result(tool_context, result)

    async def _start_interactive_product_request(
        self,
        *,
        task: str,
        inputs: Any | None,
        output: dict[str, Any],
        tool_context: ToolContext,
        app_name: str,
        artifact_service: BaseArtifactService | None,
        system_selection_builder: Any | None = None,
    ) -> dict[str, Any]:
        """Start a PPT workflow and stop at the requirement confirmation gate."""
        try:
            raw_inputs = self._normalize_raw_inputs(inputs)
            requirement = await self.prepare_confirmed_requirement_with_agent(
                task=task,
                inputs=raw_inputs,
                output=output,
                source_understanding=SourceUnderstanding(
                    document_type=self._infer_document_type(self._normalize_source_inputs(raw_inputs)),
                ),
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
            )
            tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = requirement.model_dump(mode="json")
            clarification_questions = self.validate_confirmed_requirement(requirement)
            if clarification_questions:
                result = PptProductResult(
                    status="needs_clarification",
                    phase="requirement_confirmation",
                    message="PptProductManager needs a clearer PPT topic or source material before generation.",
                    selected_route=requirement.route,
                    confirmed_requirement=requirement,
                    delivery_manifest=DeliveryManifest(),
                    warnings=[],
                    next_actions=clarification_questions,
                )
                return self._persist_product_result(tool_context, result)

            system_selection = await self.select_ppt_system_with_agent(
                task=task,
                output=output,
                requirement=requirement,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
                system_selection_builder=system_selection_builder,
            )
            requirement = self._apply_system_selection_to_requirement(requirement, system_selection)
            tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = requirement.model_dump(mode="json")
            workflow_state = {
                "workflow_id": self._build_workflow_id(tool_context.state),
                "stage": PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION,
                "revision": 1,
                "task": task,
                "raw_inputs": raw_inputs,
                "output": dict(output or {}),
                "confirmed_requirement": requirement.model_dump(mode="json"),
                "system_selection": system_selection,
            }
            self._mark_confirmation_waiting(workflow_state, tool_context.state)
            result = self._build_requirement_confirmation_result(requirement, workflow_state)
            tool_context.state[PPT_WORKFLOW_STATE_KEY] = workflow_state
            return self._persist_product_result(tool_context, result)
        except Exception as exc:
            result = PptProductResult(
                status="error",
                phase="requirement_confirmation",
                message=f"PPT requirement confirmation failed: {type(exc).__name__}: {exc}",
                selected_route="html",
                warnings=[str(exc)],
                next_actions=["Fix the malformed PPT product request and retry."],
            )
            return self._persist_product_result(tool_context, result)

    async def _continue_after_requirement_confirmation(
        self,
        *,
        user_response: str,
        workflow_state: dict[str, Any],
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        source_converter: Any | None,
        content_plan_builder: Any | None,
    ) -> dict[str, Any]:
        """Handle the first confirmation gate and then prepare a content plan."""
        if not self._is_confirmation_text(user_response):
            return await self._revise_requirement_confirmation(
                user_response=user_response,
                workflow_state=workflow_state,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
            )

        requirement = ConfirmedRequirement.model_validate(workflow_state.get("confirmed_requirement") or {})
        system_selection = self._get_workflow_system_selection(workflow_state, requirement, tool_context)
        requirement = self._apply_system_selection_to_requirement(requirement, system_selection)
        route_registration = self._route_registry.get(requirement.route)
        if (
            not self._is_private_skill_selection(system_selection)
            and (route_registration is None or not route_registration.implemented)
        ):
            result = self._build_route_not_implemented_result(requirement, route_registration)
            workflow_state["stage"] = PPT_STAGE_COMPLETED
            tool_context.state[PPT_WORKFLOW_STATE_KEY] = workflow_state
            return self._persist_product_result(tool_context, result)

        raw_inputs = list(workflow_state.get("raw_inputs") or [])
        source_inputs = self._normalize_source_inputs(raw_inputs)
        source_inputs = self._stage_source_inputs_for_workspace(source_inputs, tool_context.state)
        source_converter = source_converter or self._build_source_converter(
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        source_materials = await self._prepare_source_materials(
            source_inputs,
            fallback_document_type=self._infer_document_type(source_inputs),
            tool_context=tool_context,
            source_converter=source_converter,
        )
        requirement = requirement.model_copy(
            update={
                "source_inputs": source_inputs,
                "source_understanding": source_materials,
            }
        )
        tool_context.state["ppt_source_materials"] = source_materials.model_dump(mode="json")
        tool_context.state["ppt_source_markdown_sources"] = source_materials.markdown_sources
        tool_context.state["ppt_source_figures"] = source_materials.figures
        tool_context.state["ppt_source_output_files"] = source_materials.output_files
        tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = requirement.model_dump(mode="json")

        content_plan = await self.build_deck_content_plan(
            requirement,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
            content_plan_builder=content_plan_builder,
            resolve_assets=False,
        )
        workflow_state.update(
            {
                "stage": PPT_STAGE_AWAITING_CONTENT_PLAN_CONFIRMATION,
                "confirmed_requirement": requirement.model_dump(mode="json"),
                "source_materials": source_materials.model_dump(mode="json"),
                "deck_content_plan": content_plan.model_dump(mode="json"),
                "deck_content_plan_markdown": str(tool_context.state.get("ppt_deck_content_plan_markdown") or ""),
                "system_selection": system_selection,
                "revision": int(workflow_state.get("revision", 1) or 1) + 1,
            }
        )
        self._mark_confirmation_waiting(workflow_state, tool_context.state)
        tool_context.state[PPT_WORKFLOW_STATE_KEY] = workflow_state
        result = self._build_content_plan_confirmation_result(requirement, content_plan, workflow_state)
        return self._persist_product_result(tool_context, result)

    async def _revise_requirement_confirmation(
        self,
        *,
        user_response: str,
        workflow_state: dict[str, Any],
        tool_context: ToolContext,
        app_name: str,
        artifact_service: BaseArtifactService | None,
    ) -> dict[str, Any]:
        """Apply user edits to the requirement draft and ask for confirmation again."""
        base_task = str(workflow_state.get("task") or "")
        raw_inputs = list(workflow_state.get("raw_inputs") or [])
        output = dict(workflow_state.get("output") or {})
        existing_requirement = ConfirmedRequirement.model_validate(workflow_state.get("confirmed_requirement") or {})
        source_understanding = existing_requirement.source_understanding or SourceUnderstanding(
            document_type=self._infer_document_type(self._normalize_source_inputs(raw_inputs)),
        )
        requirement = await self.revise_confirmed_requirement_with_agent(
            existing_requirement=existing_requirement,
            user_response=user_response,
            task=base_task,
            raw_inputs=raw_inputs,
            output=output,
            source_understanding=source_understanding,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        system_selection = await self.select_ppt_system_with_agent(
            task=f"{base_task}\n{user_response}".strip(),
            output=output,
            requirement=requirement,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        requirement = self._apply_system_selection_to_requirement(requirement, system_selection)
        workflow_state.update(
            {
                "task": base_task,
                "confirmed_requirement": requirement.model_dump(mode="json"),
                "system_selection": system_selection,
                "revision": int(workflow_state.get("revision", 1) or 1) + 1,
                "stage": PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION,
            }
        )
        self._mark_confirmation_waiting(workflow_state, tool_context.state)
        tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = requirement.model_dump(mode="json")
        tool_context.state[PPT_WORKFLOW_STATE_KEY] = workflow_state
        result = self._build_requirement_confirmation_result(requirement, workflow_state)
        return self._persist_product_result(tool_context, result)

    async def _continue_after_content_plan_confirmation(
        self,
        *,
        user_response: str,
        workflow_state: dict[str, Any],
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        content_plan_builder: Any | None,
        asset_resolver: Any | None,
    ) -> dict[str, Any]:
        """Handle the second confirmation gate and then resolve assets plus route output."""
        requirement = ConfirmedRequirement.model_validate(workflow_state.get("confirmed_requirement") or {})
        system_selection = self._get_workflow_system_selection(workflow_state, requirement, tool_context)
        requirement = self._apply_system_selection_to_requirement(requirement, system_selection)
        if not self._is_confirmation_text(user_response):
            revised_requirement = requirement.model_copy(
                update={
                    "request_brief": self._append_user_revision(
                        requirement.request_brief,
                        user_response,
                        label="Content plan revision",
                    )
                }
            )
            content_plan = await self.build_deck_content_plan(
                revised_requirement,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
                expert_agents=expert_agents,
                content_plan_builder=content_plan_builder,
                resolve_assets=False,
            )
            workflow_state.update(
                {
                    "stage": PPT_STAGE_AWAITING_CONTENT_PLAN_CONFIRMATION,
                    "confirmed_requirement": revised_requirement.model_dump(mode="json"),
                    "deck_content_plan": content_plan.model_dump(mode="json"),
                    "deck_content_plan_markdown": str(tool_context.state.get("ppt_deck_content_plan_markdown") or ""),
                    "system_selection": system_selection,
                    "revision": int(workflow_state.get("revision", 1) or 1) + 1,
                }
            )
            self._mark_confirmation_waiting(workflow_state, tool_context.state)
            tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = revised_requirement.model_dump(mode="json")
            tool_context.state[PPT_WORKFLOW_STATE_KEY] = workflow_state
            result = self._build_content_plan_confirmation_result(revised_requirement, content_plan, workflow_state)
            return self._persist_product_result(tool_context, result)

        content_plan = DeckContentPlan.model_validate(workflow_state.get("deck_content_plan") or {})
        if self._is_private_skill_selection(system_selection):
            private_build = await self.execute_private_ppt_skill(
                requirement=requirement,
                content_plan=content_plan,
                system_selection=system_selection,
                tool_context=tool_context,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
            )
            workflow_state.update(
                {
                    "stage": PPT_STAGE_COMPLETED,
                    "deck_content_plan": content_plan.model_dump(mode="json"),
                    "private_skill_build": private_build,
                    "system_selection": system_selection,
                }
            )
            tool_context.state[PPT_WORKFLOW_STATE_KEY] = workflow_state
            result = self._build_private_skill_delivery_result(
                requirement=requirement,
                content_plan=content_plan,
                system_selection=system_selection,
                private_build=private_build,
            )
            return self._persist_product_result(tool_context, result)

        resolved_plan = await self.content_planner.resolve_plan_assets(
            content_plan,
            requirement,
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            asset_resolver=asset_resolver,
        )
        output_dir = self._build_route_output_dir(tool_context.state)
        route_build = await self._dispatch_ppt_route(
            requirement=requirement,
            content_plan=resolved_plan,
            output_dir=output_dir,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        route_succeeded = bool(route_build.pptx_path)
        output_files = self._record_output_files(
            tool_context.state,
            [
                route_build.pptx_path,
                route_build.html_deck_path,
                route_build.quality_report_path,
                route_build.build_log_path,
                *route_build.preview_paths,
            ],
        )
        delivery_manifest = DeliveryManifest(
            final_pptx=route_build.pptx_path,
            previews=route_build.preview_paths,
            quality_report=route_build.quality_report_path,
            build_log=route_build.build_log_path,
            intermediate_artifacts=[route_build.html_deck_path],
            output_files=output_files,
        )
        workflow_state.update(
            {
                "stage": PPT_STAGE_COMPLETED,
                "deck_content_plan": resolved_plan.model_dump(mode="json"),
                "route_build": route_build.model_dump(mode="json"),
                "system_selection": system_selection,
            }
        )
        tool_context.state[PPT_WORKFLOW_STATE_KEY] = workflow_state
        result = PptProductResult(
            status="success" if route_succeeded else "generation_failed",
            phase="html_route_delivery",
            message=(
                "HTML route generated the PPTX after requirement and content-plan confirmation."
                if route_succeeded
                else "HTML route generated HTML and previews, but failed to export an editable PPTX after confirmation."
            ),
            selected_route=requirement.route,
            confirmed_requirement=requirement,
            deck_content_plan=resolved_plan,
            route_build=route_build,
            quality_review=QualityReviewResult(
                status="pass" if route_succeeded else "failed",
                page_count_ok=route_succeeded,
                file_open_ok=route_succeeded,
                text_complete_ok=route_succeeded,
                assets_ok=route_succeeded,
                placeholder_free_ok=route_succeeded,
                overflow_ok=None,
                style_consistency_ok=route_succeeded,
            ),
            delivery_manifest=delivery_manifest,
            output_files=output_files,
            warnings=[
                *list(route_build.warnings),
                *list(requirement.source_understanding.extraction_warnings),
                *list(tool_context.state.get("ppt_content_planning_warnings") or []),
            ],
            next_actions=(
                ["Review the generated PPTX and previews."]
                if route_succeeded
                else ["Fix the HTML-to-PPTX conversion findings and retry PPTX export."]
            ),
        )
        return self._persist_product_result(tool_context, result)

    @staticmethod
    def _should_auto_confirm(output: dict[str, Any]) -> bool:
        """Return whether the caller explicitly requests the old single-shot behavior."""
        raw_value = output.get("auto_confirm") or output.get("skip_confirmations") or output.get("confirmation_mode")
        if isinstance(raw_value, bool):
            return raw_value
        return str(raw_value or "").strip().lower() in {"auto", "skip", "true", "yes", "1"}

    @staticmethod
    def _get_workflow_state(state: dict[str, Any]) -> dict[str, Any]:
        """Return the persisted PPT workflow state if it is a dictionary."""
        workflow_state = state.get(PPT_WORKFLOW_STATE_KEY)
        return dict(workflow_state) if isinstance(workflow_state, dict) else {}

    @staticmethod
    def _is_pending_confirmation_stage(stage: Any) -> bool:
        """Return whether a PPT workflow is waiting for user confirmation."""
        return str(stage or "").strip() in {
            PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION,
            PPT_STAGE_AWAITING_CONTENT_PLAN_CONFIRMATION,
        }

    @staticmethod
    def _current_turn_index(state: dict[str, Any]) -> int:
        """Return the current user turn index from ADK session state."""
        try:
            return int(state.get("turn_index", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _mark_confirmation_waiting(
        self,
        workflow_state: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        """Record the user turn that created the current confirmation state."""
        waiting_since_turn = self._current_turn_index(state)
        workflow_state[PPT_WORKFLOW_WAITING_SINCE_TURN_KEY] = waiting_since_turn
        workflow_state["confirmation_id"] = (
            f"{workflow_state.get('workflow_id', '')}:"
            f"{workflow_state.get('stage', '')}:"
            f"{workflow_state.get('revision', 0)}:"
            f"{waiting_since_turn}"
        )

    def _is_waiting_for_later_user_turn(
        self,
        workflow_state: dict[str, Any],
        state: dict[str, Any],
    ) -> bool:
        """Return whether the current confirmation was already returned this turn."""
        waiting_since_turn = workflow_state.get(PPT_WORKFLOW_WAITING_SINCE_TURN_KEY)
        if waiting_since_turn is None:
            return False
        try:
            waiting_since_turn_index = int(waiting_since_turn)
        except (TypeError, ValueError):
            return False
        return self._current_turn_index(state) <= waiting_since_turn_index

    def _build_current_confirmation_result(self, workflow_state: dict[str, Any]) -> PptProductResult:
        """Rebuild the current pending confirmation without advancing the workflow."""
        stage = str(workflow_state.get("stage") or "").strip()
        requirement = ConfirmedRequirement.model_validate(workflow_state.get("confirmed_requirement") or {})
        if stage == PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION:
            return self._build_requirement_confirmation_result(requirement, workflow_state)
        if stage == PPT_STAGE_AWAITING_CONTENT_PLAN_CONFIRMATION:
            content_plan = DeckContentPlan.model_validate(workflow_state.get("deck_content_plan") or {})
            return self._build_content_plan_confirmation_result(requirement, content_plan, workflow_state)
        return PptProductResult(
            status="error",
            phase="ppt_workflow_resume",
            message=f"PPT 工作流当前阶段 `{stage}` 不能继续确认。",
            selected_route=requirement.route,
            warnings=[f"Unsupported PPT workflow stage: {stage}"],
            next_actions=["重新发起 PPT 任务。"],
        )

    @staticmethod
    def _build_workflow_id(state: dict[str, Any]) -> str:
        """Build a stable workflow id from the current ADK session and turn."""
        session_id = str(state.get("sid") or "ppt-session").strip()
        turn_index = int(state.get("turn_index", 0) or 0)
        return f"{session_id}:ppt:{turn_index}"

    @staticmethod
    def _is_confirmation_text(text: str) -> bool:
        """Return whether the user response is an approval rather than a revision."""
        normalized = re.sub(r"[\s，。,.！!？?：:；;、]+", "", str(text or "").lower())
        return normalized in {
            "确认",
            "确认无误",
            "可以",
            "可以继续",
            "继续",
            "开始",
            "没问题",
            "没问题继续",
            "同意",
            "通过",
            "ok",
            "okay",
            "yes",
            "y",
            "confirm",
            "approved",
            "approve",
            "goahead",
        }

    @staticmethod
    def _append_user_revision(base_text: str, user_response: str, *, label: str = "User revision") -> str:
        """Append a user revision to the original brief in a planner-readable form."""
        clean_base = str(base_text or "").strip()
        clean_revision = str(user_response or "").strip()
        if not clean_revision:
            return clean_base
        return f"{clean_base}\n{label}: {clean_revision}".strip()

    def _revise_confirmed_requirement_deterministically(
        self,
        existing_requirement: ConfirmedRequirement,
        *,
        user_response: str,
        raw_inputs: list[Any],
        output: dict[str, Any],
        source_understanding: SourceUnderstanding,
    ) -> ConfirmedRequirement:
        """Apply a conservative structured requirement patch without mutating the task text."""
        clean_response = str(user_response or "").strip()
        update: dict[str, Any] = {
            "request_brief": self._merge_request_brief_revision(
                existing_requirement.request_brief,
                clean_response,
            ),
            "source_inputs": [
                item.model_dump(mode="json") for item in self._normalize_source_inputs(raw_inputs)
            ],
            "reference_assets": [
                item.model_dump(mode="json") for item in self._normalize_reference_assets(raw_inputs)
            ],
            "source_understanding": source_understanding.model_dump(mode="json"),
        }

        explicit_topic = self._extract_explicit_requirement_text(clean_response, ("主题", "题目", "topic"))
        if explicit_topic:
            update["topic"] = self._clean_public_topic(explicit_topic, original_task=clean_response) or explicit_topic

        explicit_audience = self._extract_explicit_requirement_text(
            clean_response,
            ("受众", "听众", "对象", "目标受众", "面向对象", "audience"),
        )
        if explicit_audience:
            update["audience"] = self._clean_audience(explicit_audience, split_possessive=False)

        explicit_scenario = self._extract_explicit_requirement_text(
            clean_response,
            ("场景", "使用场景", "汇报场景", "scenario", "use case"),
        )
        inferred_scenario = explicit_scenario or self._infer_scenario(clean_response)
        if inferred_scenario:
            update["scenario"] = inferred_scenario

        slide_policy = self._infer_slide_count_policy(clean_response, {})
        if slide_policy.source == "user":
            update["slide_count_policy"] = slide_policy.model_dump(mode="json")

        explicit_language = self._extract_explicit_requirement_text(clean_response, ("语言", "language"))
        if explicit_language:
            update["language"] = explicit_language

        aspect_ratio = self._select_aspect_ratio(clean_response, {})
        if (
            any(ratio_token in clean_response for ratio_token in ("16:9", "4:3"))
            and aspect_ratio != existing_requirement.aspect_ratio
            and aspect_ratio in {"16:9", "4:3"}
        ):
            update["aspect_ratio"] = aspect_ratio

        route, route_confirmed = self._select_route(clean_response, output)
        if route_confirmed:
            update["route"] = route
            update["confirmed_by_user"] = True
            update["template_requirement"] = self._infer_template_requirement(
                clean_response,
                route,
                self._normalize_source_inputs(raw_inputs),
                output,
            ).model_dump(mode="json")
            update["editability_requirement"] = self._infer_editability_requirement(
                clean_response,
                route,
                output,
            ).model_dump(mode="json")

        revised_style_keywords = self._infer_style_keywords(clean_response, output)
        if revised_style_keywords:
            existing_keywords = list(existing_requirement.style_requirement.style_keywords)
            update["style_requirement"] = StyleRequirement(
                style_keywords=self._dedupe_preserve_order([*existing_keywords, *revised_style_keywords]),
                tone=existing_requirement.style_requirement.tone,
                language_style=existing_requirement.style_requirement.language_style,
                brand_notes=existing_requirement.style_requirement.brand_notes,
            ).model_dump(mode="json")

        payload = existing_requirement.model_dump(mode="json")
        payload.update(update)
        return ConfirmedRequirement.model_validate(payload)

    @classmethod
    def _merge_requirement_payload(
        cls,
        payload: dict[str, Any],
        *,
        fallback_requirement: ConfirmedRequirement,
    ) -> ConfirmedRequirement:
        """Merge an LLM-produced requirement JSON with validated fallback-owned fields."""
        merged = fallback_requirement.model_dump(mode="json")
        for field_name in ("topic", "audience", "scenario", "language", "request_brief"):
            raw_value = payload.get(field_name)
            if isinstance(raw_value, str) and raw_value.strip():
                clean_value = cls._normalize_request_text(raw_value)
                if field_name == "topic":
                    clean_value = (
                        cls._clean_public_topic(
                            clean_value,
                            original_task=fallback_requirement.request_brief,
                        )
                        or clean_value
                    )
                merged[field_name] = clean_value

        raw_route = str(payload.get("route") or "").strip().lower()
        if raw_route in {"html", "svg", "xml"}:
            merged["route"] = raw_route

        raw_aspect_ratio = str(payload.get("aspect_ratio") or "").strip()
        if raw_aspect_ratio in {"16:9", "4:3"}:
            merged["aspect_ratio"] = raw_aspect_ratio

        raw_confirmed = payload.get("confirmed_by_user")
        if isinstance(raw_confirmed, bool):
            merged["confirmed_by_user"] = raw_confirmed

        nested_models: tuple[tuple[str, type[Any]], ...] = (
            ("slide_count_policy", SlideCountPolicy),
            ("template_requirement", TemplateRequirement),
            ("style_requirement", StyleRequirement),
            ("editability_requirement", EditabilityRequirement),
        )
        for field_name, model_type in nested_models:
            raw_nested = payload.get(field_name)
            if isinstance(raw_nested, dict):
                try:
                    merged[field_name] = model_type.model_validate(raw_nested).model_dump(mode="json")
                except Exception:
                    pass

        if not str(merged.get("topic") or "").strip():
            merged["topic"] = fallback_requirement.topic

        # File/material ownership stays in deterministic code so the LLM cannot invent paths.
        merged["source_inputs"] = [
            item.model_dump(mode="json") for item in fallback_requirement.source_inputs
        ]
        merged["source_understanding"] = fallback_requirement.source_understanding.model_dump(mode="json")
        merged["reference_assets"] = [
            item.model_dump(mode="json") for item in fallback_requirement.reference_assets
        ]
        merged["output_format"] = "pptx"
        return ConfirmedRequirement.model_validate(merged)

    @staticmethod
    def _merge_request_brief_revision(base_text: str, user_response: str) -> str:
        """Record a requirement revision in the planner brief without polluting display fields."""
        clean_base = str(base_text or "").strip()
        clean_revision = str(user_response or "").strip()
        if not clean_revision:
            return clean_base
        if clean_revision in clean_base:
            return clean_base
        if not clean_base:
            return clean_revision
        return f"{clean_base}\n需求修订：{clean_revision}"

    @staticmethod
    def _extract_explicit_requirement_text(text: str, aliases: tuple[str, ...]) -> str:
        """Extract `field is value` style requirement edits from one user response."""
        clean_text = PptProductManager._normalize_request_text(text)
        alias_pattern = "|".join(re.escape(alias) for alias in aliases)
        match = re.search(
            rf"(?:{alias_pattern})\s*(?:设置为|设为|改成|改为|调整为|指定为|为|是|:|：)\s*[\"“']?"
            rf"(?P<value>[^，。,.；;\n\"”']{{1,80}})",
            clean_text,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        return str(match.group("value") or "").strip(" ：:，。,.；;、-\"'“”‘’")

    def _build_requirement_confirmation_result(
        self,
        requirement: ConfirmedRequirement,
        workflow_state: dict[str, Any],
    ) -> PptProductResult:
        """Build the first user-confirmation result for normalized requirements."""
        summary_markdown = self._format_requirement_confirmation(requirement)
        system_selection = workflow_state.get("system_selection")
        if isinstance(system_selection, dict) and system_selection:
            summary_markdown = (
                f"{summary_markdown}\n\n"
                f"{self._format_system_selection_confirmation(system_selection)}"
            )
        return PptProductResult(
            status="awaiting_requirement_confirmation",
            phase="requirement_confirmation",
            message="请确认 PPT 需求参数。确认后我再开始读取材料并规划内容。",
            selected_route=requirement.route,
            confirmed_requirement=requirement,
            delivery_manifest=DeliveryManifest(),
            confirmation_request={
                "type": "requirement",
                "workflow_id": workflow_state.get("workflow_id", ""),
                "summary_markdown": summary_markdown,
                "expected_user_action": "回复“确认”继续；或直接说明要修改的主题、页数、受众、模板、路线等。",
            },
            next_actions=["确认需求参数，或说明需要修改的参数。"],
        )

    def _build_content_plan_confirmation_result(
        self,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        workflow_state: dict[str, Any],
    ) -> PptProductResult:
        """Build the second user-confirmation result for the content plan."""
        summary_markdown = self._format_content_plan_confirmation(content_plan)
        system_selection = workflow_state.get("system_selection")
        if isinstance(system_selection, dict) and system_selection:
            summary_markdown = (
                f"{summary_markdown}\n\n"
                f"{self._format_system_selection_confirmation(system_selection)}"
            )
        message = "请确认 PPT 内容规划。确认后我才会开始搜索或生成图片，并导出 PPTX。"
        if self._is_private_skill_selection(system_selection):
            message = "请确认 PPT 内容规划。确认后我会按选中的私有 PPT skill 生成演示稿。"
        return PptProductResult(
            status="awaiting_content_plan_confirmation",
            phase="content_plan_confirmation",
            message=message,
            selected_route=requirement.route,
            confirmed_requirement=requirement,
            deck_content_plan=content_plan,
            delivery_manifest=DeliveryManifest(),
            confirmation_request={
                "type": "content_plan",
                "workflow_id": workflow_state.get("workflow_id", ""),
                "summary_markdown": summary_markdown,
                "expected_user_action": "回复“确认”开始补图和导出；或说明要调整的页面、标题、内容重点、图片意图。",
            },
            next_actions=["确认内容规划，或说明需要修改的页面和内容。"],
        )

    @staticmethod
    def _format_requirement_confirmation(requirement: ConfirmedRequirement) -> str:
        """Render ConfirmedRequirement as user-facing Markdown for confirmation."""
        template = requirement.template_requirement
        slide_policy = requirement.slide_count_policy
        if slide_policy.target is not None and slide_policy.minimum == slide_policy.maximum:
            slide_count = f"{slide_policy.target} 页"
        elif slide_policy.target is not None:
            slide_count = f"目标 {slide_policy.target} 页，范围 {slide_policy.minimum}-{slide_policy.maximum} 页"
        else:
            slide_count = f"范围 {slide_policy.minimum}-{slide_policy.maximum} 页"
        template_text = (
            f"系统模板 `{template.template_id}`"
            if template.template_source == "system" and template.template_id
            else "用户模板"
            if template.template_source == "user"
            else "无模板，自由设计"
        )
        source_count = len(requirement.source_inputs)
        lines = [
            "| 参数 | 当前值 |",
            "| --- | --- |",
            f"| 主题 | {requirement.topic or '未识别'} |",
            f"| 受众 | {requirement.audience or '未指定'} |",
            f"| 场景 | {requirement.scenario or '未指定'} |",
            f"| 页数 | {slide_count} |",
            f"| 语言 | {requirement.language} |",
            f"| 比例 | {requirement.aspect_ratio} |",
            f"| 路线 | {requirement.route} |",
            f"| 模板 | {template_text} |",
            f"| 输入材料 | {source_count} 个 |",
        ]
        return "\n".join(lines)

    @staticmethod
    def _format_content_plan_confirmation(content_plan: DeckContentPlan) -> str:
        """Render DeckContentPlan as a concise user-facing Markdown table."""
        lines = [
            f"标题：{content_plan.title}",
            "",
            "| 页 | 页型 | 标题 | 重点 | 插图意图 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for page in content_plan.pages:
            visual_intent = page.asset_intent or "无"
            if page.assets:
                first_asset = page.assets[0]
                visual_intent = first_asset.description or first_asset.prompt or visual_intent
            lines.append(
                "| "
                f"{page.slide_number} | "
                f"{page.page_type} | "
                f"{page.title} | "
                f"{page.key_takeaway} | "
                f"{visual_intent} |"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_system_selection_confirmation(selection: dict[str, Any]) -> str:
        """Render the selected PPT system as user-facing Markdown."""
        system_type = str(selection.get("system_type") or "").strip()
        route = str(selection.get("route") or "").strip() or "html"
        skill_name = str(selection.get("skill_name") or "").strip()
        reason = str(selection.get("reason") or "").strip()
        if system_type == "private_skill" and skill_name:
            system_text = f"私有 PPT skill `{skill_name}`"
        else:
            system_text = f"内置路线 `{route}`"
        lines = [
            "| 系统选择 | 当前值 |",
            "| --- | --- |",
            f"| 制作系统 | {system_text} |",
            f"| 输出方式 | {selection.get('output_format') or 'pptx'} |",
        ]
        if reason:
            lines.append(f"| 选择理由 | {reason} |")
        return "\n".join(lines)

    def _persist_product_result(
        self,
        tool_context: ToolContext,
        result: PptProductResult,
    ) -> dict[str, Any]:
        """Persist one PPT product result into ADK session state."""
        result_payload = result.model_dump(mode="json")
        tool_context.state["product_line"] = "ppt"
        tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = (
            result_payload.get("confirmed_requirement") or {}
        )
        if result_payload.get("deck_content_plan"):
            tool_context.state["ppt_deck_content_plan"] = result_payload["deck_content_plan"]
        if result_payload.get("route_build"):
            tool_context.state["ppt_route_build"] = result_payload["route_build"]
        tool_context.state[PPT_PRODUCT_RESULT_STATE_KEY] = result_payload
        tool_context.state["current_output"] = result_payload
        tool_context.state["last_product_result"] = result_payload
        tool_context.state["last_output_message"] = str(result_payload.get("message") or "")
        return result_payload

    def build_initial_deck_content_plan(self, requirement: ConfirmedRequirement) -> DeckContentPlan:
        """Build a template-independent content plan for the HTML MVP."""
        return self.content_planner.build_plan(requirement)

    async def build_deck_content_plan(
        self,
        requirement: ConfirmedRequirement,
        *,
        tool_context: ToolContext,
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        expert_agents: dict[str, BaseAgent] | None = None,
        content_plan_builder: Any | None = None,
        asset_resolver: Any | None = None,
        resolve_assets: bool = True,
    ) -> DeckContentPlan:
        """Build a deck content plan through the ADK planning agent when possible."""
        if content_plan_builder is not None:
            plan_result = content_plan_builder(requirement)
            if inspect.isawaitable(plan_result):
                plan_result = await plan_result
            plan = DeckContentPlan.model_validate(plan_result)
            tool_context.state["ppt_content_planning_output"] = {
                "status": "success",
                "message": "Injected content plan builder produced DeckContentPlan.",
                "source": "injected",
            }
        else:
            plan = await self.content_planner.build_plan_with_agent(
                requirement,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
            )

        tool_context.state["ppt_deck_content_plan"] = plan.model_dump(mode="json")
        if not resolve_assets:
            return plan

        return await self.content_planner.resolve_plan_assets(
            plan,
            requirement,
            tool_context=tool_context,
            expert_agents=expert_agents or {},
            app_name=app_name,
            artifact_service=artifact_service,
            asset_resolver=asset_resolver,
        )

    async def execute_private_ppt_skill(
        self,
        *,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        system_selection: dict[str, Any],
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: BaseArtifactService | None,
    ) -> dict[str, Any]:
        """Run the selected private PPT skill through PptProductManager itself."""
        selection = self._normalize_system_selection(
            system_selection,
            fallback_selection=self._build_default_system_selection(requirement),
            strict=True,
        )
        skill_name = str(selection.get("skill_name") or "").strip()
        skill_content = self.skill_registry.read_skill(skill_name)
        tool_context.state[PPT_PRODUCT_ACTIVE_SKILL_STATE_KEY] = {
            "name": skill_name,
            "content": skill_content,
        }

        if not hasattr(tool_context, "_invocation_context"):
            tool_context.state["ppt_private_skill_execution_output"] = {
                "status": "fallback",
                "message": "PptProductManager skill runner skipped because no ADK invocation context was available.",
                "source": "deterministic_fallback",
            }
            return self.save_ppt_private_skill_html(
                file_name="index.html",
                html_content=self._build_private_skill_fallback_html(
                    requirement=requirement,
                    content_plan=content_plan,
                    system_selection=selection,
                    skill_content=skill_content,
                ),
                description=f"Private PPT skill `{skill_name}` HTML deck artifact.",
                tool_context=tool_context,
            )

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
        child_state["ppt_product_manager_skill_run_base"] = {
            "confirmed_requirement": requirement.model_dump(mode="json"),
            "deck_content_plan": content_plan.model_dump(mode="json"),
            "system_selection": selection,
            "active_skill": {
                "name": skill_name,
                "content": skill_content,
            },
            "available_experts": sorted((expert_agents or {}).keys()),
        }
        child_state["ppt_private_skill_execution_base"] = {
            "confirmed_requirement": requirement.model_dump(mode="json"),
            "deck_content_plan": content_plan.model_dump(mode="json"),
            "system_selection": selection,
            "active_skill": {
                "name": skill_name,
                "content": skill_content,
            },
        }

        previous_runtime = (
            dict(self._skill_runtime_expert_agents),
            self._skill_runtime_app_name,
            self._skill_runtime_artifact_service,
        )
        self._skill_runtime_expert_agents = dict(expert_agents or {})
        self._skill_runtime_app_name = app_name
        self._skill_runtime_artifact_service = artifact_service
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
                            text=_build_product_manager_skill_run_user_message(
                                requirement=requirement,
                                content_plan=content_plan,
                                system_selection=selection,
                                skill_content=skill_content,
                                available_experts=sorted((expert_agents or {}).keys()),
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
            private_build = dict(final_state.get(PPT_PRIVATE_SKILL_BUILD_STATE_KEY) or {})
            if not str(private_build.get("output_path") or "").strip():
                raise ValueError("PptProductManager did not save a private skill HTML artifact.")
            for key in (
                PPT_PRIVATE_SKILL_BUILD_STATE_KEY,
                "generated",
                "new_files",
                "files_history",
                "final_file_paths",
                "current_output",
            ):
                if key in final_state:
                    tool_context.state[key] = copy.deepcopy(final_state[key])
            if final_state.get("ppt_private_skill_execution_agent_message"):
                tool_context.state["ppt_private_skill_execution_agent_message"] = str(
                    final_state.get("ppt_private_skill_execution_agent_message")
                )
            if final_state.get("ppt_skill_last_expert_result"):
                tool_context.state["ppt_skill_last_expert_result"] = copy.deepcopy(
                    final_state["ppt_skill_last_expert_result"]
                )
            tool_context.state["ppt_private_skill_execution_output"] = {
                "status": "success",
                "message": "PptProductManager ran the selected private skill and saved the artifact.",
                "source": "ppt_product_manager",
            }
            return private_build
        except Exception as exc:
            tool_context.state["ppt_private_skill_execution_output"] = {
                "status": "fallback",
                "message": f"PptProductManager private skill fallback: {type(exc).__name__}: {exc}",
                "source": "deterministic_fallback",
            }
            return self.save_ppt_private_skill_html(
                file_name="index.html",
                html_content=self._build_private_skill_fallback_html(
                    requirement=requirement,
                    content_plan=content_plan,
                    system_selection=selection,
                    skill_content=skill_content,
                ),
                description=f"Private PPT skill `{skill_name}` HTML deck artifact.",
                tool_context=tool_context,
            )
        finally:
            (
                self._skill_runtime_expert_agents,
                self._skill_runtime_app_name,
                self._skill_runtime_artifact_service,
            ) = previous_runtime
            await child_runner.close()

    def _build_private_skill_delivery_result(
        self,
        *,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        system_selection: dict[str, Any],
        private_build: dict[str, Any],
    ) -> PptProductResult:
        """Build the product result for a private PPT skill delivery."""
        skill_name = str(system_selection.get("skill_name") or "").strip()
        output_path = str(private_build.get("output_path") or "").strip()
        output_files = list(private_build.get("output_files") or [])
        status = "success" if output_path else "generation_failed"
        return PptProductResult(
            status=status,
            phase="private_skill_delivery",
            message=(
                f"Private PPT skill `{skill_name}` generated a presentation artifact."
                if status == "success"
                else f"Private PPT skill `{skill_name}` did not save a presentation artifact."
            ),
            selected_route=requirement.route,
            confirmed_requirement=requirement,
            deck_content_plan=content_plan,
            quality_review=QualityReviewResult(status="not_run"),
            delivery_manifest=DeliveryManifest(
                final_pptx="",
                intermediate_artifacts=[output_path] if output_path else [],
                output_files=output_files,
            ),
            output_files=output_files,
            warnings=[
                *list(requirement.source_understanding.extraction_warnings),
                "Private skill delivery may produce HTML instead of editable PPTX; no built-in PPTX export was claimed.",
            ],
            next_actions=(
                ["Review the generated private-skill presentation artifact."]
                if status == "success"
                else ["Retry with another PPT system or inspect the private skill execution output."]
            ),
        )

    def _persist_system_selection(
        self,
        tool_context: ToolContext,
        selection_json: Any,
        *,
        fallback_selection: dict[str, Any] | None = None,
        strict: bool = False,
    ) -> dict[str, Any]:
        """Normalize and save the current PPT system selection."""
        selection = self._normalize_system_selection(
            selection_json,
            fallback_selection=fallback_selection,
            strict=strict,
        )
        tool_context.state[PPT_SYSTEM_SELECTION_STATE_KEY] = selection
        return selection

    def _normalize_system_selection(
        self,
        selection_json: Any,
        *,
        fallback_selection: dict[str, Any] | None = None,
        strict: bool = False,
    ) -> dict[str, Any]:
        """Normalize a PPT system selection without encoding skill-specific rules."""
        fallback = dict(fallback_selection or {})
        if not fallback:
            fallback = {
                "system_type": "built_in_route",
                "route": "html",
                "skill_name": "",
                "output_format": "pptx",
                "reason": "Using the built-in route as the conservative fallback.",
            }
        payload: Any = selection_json
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            if strict:
                raise ValueError("selection_json must be a JSON object.")
            payload = {}

        skill_name = str(payload.get("skill_name") or "").strip()
        system_type = str(payload.get("system_type") or "").strip().lower()
        if system_type not in {"built_in_route", "private_skill"}:
            if skill_name:
                system_type = "private_skill"
            else:
                system_type = str(fallback.get("system_type") or "built_in_route").strip()
        if system_type not in {"built_in_route", "private_skill"}:
            system_type = "built_in_route"

        route = str(payload.get("route") or fallback.get("route") or "html").strip().lower()
        if route not in self._route_registry:
            if strict:
                raise ValueError(f"Unknown PPT route `{route}`.")
            route = str(fallback.get("route") or "html").strip().lower()
        if route not in self._route_registry:
            route = "html"

        output_format = str(payload.get("output_format") or "").strip().lower()
        reason = str(payload.get("reason") or fallback.get("reason") or "").strip()

        if system_type == "private_skill":
            available_skills = {skill.name: skill.name for skill in self.skill_registry.list_skills()}
            available_by_lower = {name.lower(): name for name in available_skills}
            normalized_skill_name = available_skills.get(skill_name) or available_by_lower.get(skill_name.lower())
            if not normalized_skill_name:
                if strict:
                    raise ValueError(f"Product PPT skill `{skill_name}` is not available.")
                return self._normalize_system_selection(fallback, strict=False)
            skill_name = normalized_skill_name
            output_format = output_format or "html"
        else:
            skill_name = ""
            output_format = output_format or "pptx"

        return {
            "system_type": system_type,
            "route": route,
            "skill_name": skill_name,
            "output_format": output_format,
            "reason": reason or "Selected by PPT system-selection step.",
        }

    def _build_default_system_selection(self, requirement: ConfirmedRequirement) -> dict[str, Any]:
        """Return the conservative fallback system selection."""
        route = requirement.route if requirement.route in self._route_registry else "html"
        return {
            "system_type": "built_in_route",
            "route": route,
            "skill_name": "",
            "output_format": "pptx",
            "reason": "No private PPT skill was selected; using the confirmed built-in route.",
        }

    def _get_workflow_system_selection(
        self,
        workflow_state: dict[str, Any],
        requirement: ConfirmedRequirement,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Return the workflow's saved PPT system selection, creating a fallback if needed."""
        fallback_selection = self._build_default_system_selection(requirement)
        selection = self._normalize_system_selection(
            workflow_state.get("system_selection")
            or tool_context.state.get(PPT_SYSTEM_SELECTION_STATE_KEY)
            or fallback_selection,
            fallback_selection=fallback_selection,
        )
        workflow_state["system_selection"] = selection
        tool_context.state[PPT_SYSTEM_SELECTION_STATE_KEY] = selection
        return selection

    @staticmethod
    def _is_private_skill_selection(selection: Any) -> bool:
        """Return whether the normalized PPT system selection points to a private skill."""
        return (
            isinstance(selection, dict)
            and str(selection.get("system_type") or "").strip() == "private_skill"
            and bool(str(selection.get("skill_name") or "").strip())
        )

    @staticmethod
    def _apply_system_selection_to_requirement(
        requirement: ConfirmedRequirement,
        system_selection: dict[str, Any],
    ) -> ConfirmedRequirement:
        """Apply the selected route from system selection back to ConfirmedRequirement."""
        selected_route = str(system_selection.get("route") or "").strip().lower()
        if selected_route and selected_route != requirement.route:
            return requirement.model_copy(update={"route": selected_route})
        return requirement

    @staticmethod
    def _build_private_skill_fallback_html(
        *,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        system_selection: dict[str, Any],
        skill_content: str,
    ) -> str:
        """Build a deterministic HTML fallback when the PM skill runner is unavailable."""
        skill_name = html_lib.escape(str(system_selection.get("skill_name") or "private-skill"))
        topic = html_lib.escape(requirement.topic or content_plan.title or "Presentation")
        audience = html_lib.escape(requirement.audience or "audience")
        scenario = html_lib.escape(requirement.scenario or "presentation")
        skill_excerpt = html_lib.escape(str(skill_content or "").strip()[:500])
        slide_sections: list[str] = []
        for page in content_plan.pages:
            bullet_items = _extract_page_bullet_texts(page)
            bullets = "\n".join(
                f"<li>{html_lib.escape(item)}</li>"
                for item in bullet_items[:5]
            )
            if not bullets:
                bullets = f"<li>{html_lib.escape(page.key_takeaway or page.purpose)}</li>"
            slide_sections.append(
                "\n".join(
                    [
                        '<section class="slide">',
                        f"<p class=\"kicker\">{html_lib.escape(page.page_type)}</p>",
                        f"<h2>{html_lib.escape(page.title)}</h2>",
                        f"<p class=\"takeaway\">{html_lib.escape(page.key_takeaway)}</p>",
                        f"<ul>{bullets}</ul>",
                        "</section>",
                    ]
                )
            )
        slides_html = "\n".join(slide_sections)
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{topic}</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f7f5ef;
      color: #17202a;
    }}
    body {{
      margin: 0;
      background: #f7f5ef;
    }}
    .deck {{
      display: grid;
      gap: 24px;
      padding: 32px;
    }}
    .slide {{
      box-sizing: border-box;
      min-height: 720px;
      padding: 64px;
      border: 1px solid #d9d1c4;
      background: #fffdf7;
      display: flex;
      flex-direction: column;
      justify-content: center;
    }}
    h1, h2 {{
      margin: 0 0 24px;
      line-height: 1.08;
    }}
    h1 {{
      font-size: 72px;
    }}
    h2 {{
      font-size: 52px;
    }}
    p, li {{
      font-size: 28px;
      line-height: 1.5;
    }}
    ul {{
      margin: 16px 0 0;
      padding-left: 32px;
    }}
    .kicker {{
      margin: 0 0 16px;
      font-size: 18px;
      letter-spacing: 0;
      text-transform: uppercase;
      color: #667085;
    }}
    .takeaway {{
      font-weight: 650;
      color: #224f8f;
    }}
    .meta {{
      color: #475467;
    }}
    .skill-note {{
      margin-top: 40px;
      padding-top: 20px;
      border-top: 1px solid #d9d1c4;
      font-size: 16px;
      color: #667085;
      white-space: pre-wrap;
    }}
  </style>
</head>
<body>
  <main class="deck" data-skill="{skill_name}">
    <section class="slide">
      <p class="kicker">{skill_name}</p>
      <h1>{topic}</h1>
      <p class="meta">Audience: {audience} | Scenario: {scenario}</p>
      <p class="takeaway">{html_lib.escape(content_plan.core_narrative or requirement.request_brief)}</p>
      <p class="skill-note">{skill_excerpt}</p>
    </section>
    {slides_html}
  </main>
</body>
</html>
"""

    def list_product_ppt_skills(self, tool_context: ToolContext) -> dict[str, Any]:
        """List private product-ppt skills available to this product manager."""
        skills = [skill.to_dict() for skill in self.skill_registry.list_skills()]
        tool_context.state[PPT_PRODUCT_SKILLS_STATE_KEY] = skills
        return {
            "status": "success",
            "skills": skills,
            "count": len(skills),
        }

    def read_product_ppt_skill(
        self,
        name: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Read one private product-ppt skill."""
        content = self.skill_registry.read_skill(name)
        payload = {
            "name": str(name or "").strip(),
            "content": content,
        }
        tool_context.state[PPT_PRODUCT_ACTIVE_SKILL_STATE_KEY] = payload
        return {
            "status": "success",
            **payload,
        }

    def read_product_ppt_skill_file(
        self,
        name: str,
        relative_path: str,
        tool_context: ToolContext,
        max_chars: int = 60000,
    ) -> dict[str, Any]:
        """Read one text file inside a private product-ppt skill folder."""
        content = self.skill_registry.read_skill_file(
            name,
            relative_path,
            max_chars=max_chars,
        )
        payload = {
            "name": str(name or "").strip(),
            "relative_path": str(relative_path or "").strip(),
            "content": content,
            "truncated": len(content) >= max(0, int(max_chars)),
        }
        tool_context.state["active_product_ppt_skill_file"] = {
            "name": payload["name"],
            "relative_path": payload["relative_path"],
            "truncated": payload["truncated"],
        }
        return {
            "status": "success",
            **payload,
        }

    def list_ppt_experts(self, tool_context: ToolContext) -> dict[str, Any]:
        """List expert agents available to the current PPT skill run."""
        expert_names = sorted(self._skill_runtime_expert_agents)
        payload = {
            "status": "success",
            "experts": expert_names,
            "count": len(expert_names),
        }
        tool_context.state["ppt_skill_available_experts"] = expert_names
        return payload

    async def invoke_ppt_expert(
        self,
        agent_name: str,
        prompt: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Invoke one registered expert agent from a PPT skill workflow."""
        clean_agent_name = str(agent_name or "").strip()
        if not clean_agent_name:
            return {
                "status": "error",
                "message": "invoke_ppt_expert requires agent_name.",
            }
        if clean_agent_name not in self._skill_runtime_expert_agents:
            return {
                "status": "error",
                "message": (
                    f"Expert `{clean_agent_name}` is not available in this PPT skill run. "
                    f"Available experts: {', '.join(sorted(self._skill_runtime_expert_agents)) or 'none'}."
                ),
            }
        if not hasattr(tool_context, "_invocation_context"):
            return {
                "status": "error",
                "message": "invoke_ppt_expert requires an ADK invocation context.",
            }
        if self._skill_runtime_artifact_service is None:
            return {
                "status": "error",
                "message": "invoke_ppt_expert requires an artifact service.",
            }

        invocation = await dispatch_expert_call(
            agent_name=clean_agent_name,
            prompt=str(prompt or ""),
            tool_context=tool_context,
            expert_agents=self._skill_runtime_expert_agents,
            app_name=self._skill_runtime_app_name,
            artifact_service=self._skill_runtime_artifact_service,
        )
        current_output = copy.deepcopy(invocation.current_output)
        if not isinstance(current_output, dict):
            current_output = {}
        tool_result = copy.deepcopy(invocation.tool_result)
        if not isinstance(tool_result, dict):
            tool_result = {}
        payload = {
            "status": str(current_output.get("status") or tool_result.get("status") or "success"),
            "agent_name": clean_agent_name,
            "current_output": current_output,
            "tool_result": tool_result,
            "output_files": list(
                current_output.get("output_files")
                or tool_result.get("output_files")
                or []
            ),
        }
        tool_context.state["ppt_skill_last_expert_result"] = payload
        return payload

    def save_ppt_system_selection(
        self,
        selection_json: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Validate and save the PPT system selected by the internal selector."""
        selection = self._persist_system_selection(tool_context, selection_json)
        return {
            "status": "success",
            "message": "PPT system selection saved.",
            "selection": selection,
        }

    def save_ppt_private_skill_html(
        self,
        file_name: str,
        html_content: str,
        description: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Save one private-skill HTML deck artifact into the workspace."""
        output_dir = self._build_private_skill_output_dir(tool_context.state)
        clean_name = Path(str(file_name or "index.html").strip() or "index.html").name
        if not clean_name.lower().endswith((".html", ".htm")):
            clean_name = f"{Path(clean_name).stem or 'index'}.html"
        output_path = output_dir / clean_name
        output_path.write_text(_strip_html_code_fence(html_content), encoding="utf-8")
        relative_path = workspace_relative_path(output_path)
        output_files = self._record_output_files(
            tool_context.state,
            [relative_path],
            description=(
                str(description or "").strip()
                or "PPT product private skill HTML deck artifact."
            ),
            final_file_paths=[relative_path],
        )
        result = {
            "status": "success",
            "message": f"Saved private PPT skill HTML deck at {relative_path}.",
            "output_path": relative_path,
            "output_files": output_files,
        }
        tool_context.state[PPT_PRIVATE_SKILL_BUILD_STATE_KEY] = result
        tool_context.state["current_output"] = result
        return result

    def list_registered_routes(self) -> dict[str, dict[str, object]]:
        """Return product-level route registrations without exposing route internals."""
        return {
            route: registration.summary()
            for route, registration in sorted(self._route_registry.items())
        }

    def validate_confirmed_requirement(self, requirement: ConfirmedRequirement) -> list[str]:
        """Return user-facing clarification questions for under-specified requests."""
        questions: list[str] = []
        if self._is_generic_ppt_request(requirement.topic) and not (
            requirement.source_inputs or requirement.source_understanding.markdown_sources
        ):
            questions.append("请补充 PPT 的主题、用途或上传源材料后再生成。")
        return questions

    async def dispatch_ppt_route(
        self,
        route: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Dispatch the route stored in PPT session state through the route registry."""
        requested_route = str(route or "").strip().lower()
        requirement_payload = tool_context.state.get(PPT_CONFIRMED_REQUIREMENT_STATE_KEY) or {}
        content_plan_payload = tool_context.state.get("ppt_deck_content_plan") or {}
        if not requirement_payload or not content_plan_payload:
            return {
                "status": "error",
                "message": "dispatch_ppt_route requires confirmed requirement and deck content plan in session state.",
            }

        requirement = ConfirmedRequirement.model_validate(requirement_payload)
        if requested_route and requested_route != requirement.route:
            return {
                "status": "error",
                "message": (
                    f"Requested route `{requested_route}` does not match confirmed route "
                    f"`{requirement.route}`."
                ),
            }

        registration = self._route_registry.get(requirement.route)
        if registration is None or not registration.implemented:
            result = self._build_route_not_implemented_result(requirement, registration)
            payload = result.model_dump(mode="json")
            tool_context.state[PPT_PRODUCT_RESULT_STATE_KEY] = payload
            return payload

        content_plan = DeckContentPlan.model_validate(content_plan_payload)
        route_build = await self._dispatch_ppt_route(
            requirement=requirement,
            content_plan=content_plan,
            output_dir=self._build_route_output_dir(tool_context.state),
            tool_context=tool_context,
            app_name=str(getattr(getattr(tool_context, "_invocation_context", None), "app_name", "creative_claw")),
            artifact_service=None,
        )
        route_succeeded = bool(route_build.pptx_path)
        output_files = self._record_output_files(
            tool_context.state,
            [
                route_build.pptx_path,
                route_build.html_deck_path,
                route_build.quality_report_path,
                route_build.build_log_path,
                *route_build.preview_paths,
            ],
        )
        payload = {
            "status": "success" if route_succeeded else "generation_failed",
            "selected_route": requirement.route,
            "route_build": route_build.model_dump(mode="json"),
            "output_files": output_files,
        }
        tool_context.state["ppt_route_build"] = payload["route_build"]
        return payload

    async def _dispatch_ppt_route(
        self,
        *,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        output_dir: Path,
        tool_context: ToolContext | None = None,
        app_name: str = "creative_claw",
        artifact_service: InMemoryArtifactService | None = None,
    ) -> Any:
        """Dispatch one confirmed PPT request to the registered route workflow."""
        registration = self._route_registry.get(requirement.route)
        if registration is None:
            raise ValueError(f"Unknown PPT route `{requirement.route}`.")
        if not registration.implemented or registration.handler is None:
            raise NotImplementedError(f"PPT route `{requirement.route}` is not implemented.")
        template_id = ""
        if (
            requirement.template_requirement.template_source == "system"
            and requirement.template_requirement.template_id
        ):
            template_id = requirement.template_requirement.template_id
        if requirement.route == "html":
            return await build_html_route_with_agent(
                content_plan=content_plan,
                output_dir=output_dir,
                aspect_ratio=requirement.aspect_ratio,
                template_id=template_id,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
            )
        return registration.handler(
            content_plan,
            output_dir,
            requirement.aspect_ratio,
            template_id,
        )

    @staticmethod
    def _build_route_not_implemented_result(
        requirement: ConfirmedRequirement,
        registration: PptRouteRegistration | None,
    ) -> PptProductResult:
        """Build a structured result for a recognized but unavailable route."""
        workflow_name = registration.workflow_name if registration else f"{requirement.route}_route"
        return PptProductResult(
            status="route_not_implemented",
            phase=f"{requirement.route}_route_dispatch",
            message=(
                f"The {requirement.route.upper()} route is acknowledged as {workflow_name} "
                "but not implemented yet. The current MVP supports the HTML route first."
            ),
            selected_route=requirement.route,
            confirmed_requirement=requirement,
            delivery_manifest=DeliveryManifest(),
            warnings=[f"{requirement.route.upper()} route is deferred after HTML route MVP."],
            next_actions=["Use the HTML route now, or implement the requested route next."],
        )

    @staticmethod
    def _build_route_output_dir(state: dict[str, Any]) -> Path:
        """Return a deterministic output directory for the current PPT route run."""
        session_id = str(state.get("sid") or "ppt-session").strip()
        turn_index = int(state.get("turn_index", 0) or 0)
        step = int(state.get("step", 0) or 0)
        output_dir = generated_session_dir(session_id, turn_index=turn_index) / f"ppt_html_route_step_{step}"
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    @staticmethod
    def _build_private_skill_output_dir(state: dict[str, Any]) -> Path:
        """Return a deterministic output directory for private PPT skill artifacts."""
        session_id = str(state.get("sid") or "ppt-session").strip()
        turn_index = int(state.get("turn_index", 0) or 0)
        step = int(state.get("step", 0) or 0)
        output_dir = generated_session_dir(session_id, turn_index=turn_index) / f"ppt_private_skill_step_{step}"
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    @staticmethod
    def _record_output_files(
        state: dict[str, Any],
        paths: list[str],
        *,
        description: str = "PPT product HTML route artifact.",
        final_file_paths: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Record PPT product output files in session state."""
        current_turn = int(state.get("turn_index", 0) or 0)
        current_step = int(state.get("step", 0) or 0)
        records = [
            build_workspace_file_record(
                path,
                description=description,
                source="ppt_product_manager",
                turn=current_turn,
                step=current_step,
            )
            for path in paths
            if str(path or "").strip()
        ]
        generated = list(state.get("generated") or [])
        generated.extend(records)
        files_history = list(state.get("files_history") or [])
        files_history.append(records)
        state["generated"] = generated
        state["new_files"] = records
        state["files_history"] = files_history
        clean_final_file_paths = [
            str(path).strip()
            for path in (final_file_paths or [])
            if str(path or "").strip()
        ]
        if clean_final_file_paths:
            state["final_file_paths"] = clean_final_file_paths
        else:
            final_pptx = next((record["path"] for record in records if record["path"].endswith(".pptx")), "")
            if final_pptx:
                state["final_file_paths"] = [final_pptx]
        return records

    @classmethod
    def _stage_source_inputs_for_workspace(
        cls,
        source_inputs: list[SourceInput],
        state: dict[str, Any],
    ) -> list[SourceInput]:
        """Copy external local source files into the runtime workspace before expert use."""
        staged_inputs: list[SourceInput] = []
        for index, source_input in enumerate(source_inputs, start=1):
            staged_inputs.append(cls._stage_source_input_for_workspace(source_input, state, index))
        return staged_inputs

    @classmethod
    def _stage_source_input_for_workspace(
        cls,
        source_input: SourceInput,
        state: dict[str, Any],
        index: int,
    ) -> SourceInput:
        """Return a SourceInput whose local path is workspace-relative when possible."""
        source_path = str(source_input.path or "").strip()
        if not source_path or cls._looks_like_url(source_path):
            return source_input

        try:
            workspace_path = resolve_workspace_path(source_path)
            return source_input.model_copy(update={"path": workspace_relative_path(workspace_path)})
        except ValueError:
            pass

        external_path = Path(source_path).expanduser()
        if not external_path.exists() or not external_path.is_file():
            return source_input

        staged_path = stage_attachment_into_workspace(
            external_path,
            channel=str(state.get("channel") or "ppt"),
            session_id=str(state.get("sid") or "ppt-session"),
            turn_index=cls._coerce_optional_int(state.get("turn_index")),
            attachment_index=index,
            preferred_name=source_input.name or external_path.name,
        )
        return source_input.model_copy(
            update={
                "name": source_input.name or staged_path.name,
                "path": workspace_relative_path(staged_path),
            }
        )

    @staticmethod
    def _coerce_optional_int(value: Any) -> int | None:
        """Coerce an optional session index value for workspace staging paths."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def _prepare_source_materials(
        self,
        source_inputs: list[SourceInput],
        *,
        fallback_document_type: str,
        tool_context: ToolContext,
        source_converter: Any | None,
    ) -> SourceUnderstanding:
        """Convert source inputs to Markdown and keep only material file references."""
        if not source_inputs:
            return SourceUnderstanding(document_type=fallback_document_type)

        markdown_sources: list[dict[str, Any]] = []
        figures: list[dict[str, Any]] = []
        output_files: list[dict[str, Any]] = []
        warnings: list[str] = []
        source_output_dir = self._build_source_output_dir(tool_context.state)

        for index, source_input in enumerate(source_inputs, start=1):
            source_path = str(source_input.path or "").strip()
            source_label = source_input.name or source_path or f"source_{index}"
            if not source_path:
                warnings.append(f"Source {source_label} has no path or URL.")
                continue
            image_passthrough = self._register_existing_image_source(
                source_input,
                source_label=source_label,
            )
            if image_passthrough is not None:
                figure_record, file_record = image_passthrough
                figures.append(figure_record)
                output_files.append(file_record)
                continue
            if source_converter is None:
                markdown_passthrough = self._register_existing_markdown_source(
                    source_input,
                    source_label=source_label,
                )
            else:
                markdown_passthrough = None
            if markdown_passthrough is not None:
                markdown_record, markdown, file_record = markdown_passthrough
                markdown_sources.append(markdown_record)
                figures.extend(
                    self._collect_markdown_figures(
                        markdown,
                        source_name=source_label,
                        markdown_output_path=markdown_record["output_path"],
                    )
                )
                output_files.append(file_record)
                continue
            if source_converter is None:
                warnings.append(f"AnythingToMD expert was not available for {source_label}.")
                continue

            parameters = self._build_source_conversion_parameters(
                source_input,
                runtime_state=tool_context.state,
                source_index=index,
                output_dir=source_output_dir,
            )
            conversion = await source_converter(source_input, parameters)
            if conversion.get("status") != "success":
                message = str(conversion.get("message") or "conversion failed").strip()
                warnings.append(f"Could not convert {source_label}: {message}")
                continue

            markdown = str(conversion.get("output_text") or "").strip()
            results = dict(conversion.get("results") or {})
            output_path = str(results.get("output_path") or parameters.get("output_path") or "")
            if not output_path:
                warnings.append(f"Converted source {source_label} did not report a Markdown path.")
                continue
            if not markdown:
                warnings.append(f"Converted source {source_label} produced empty Markdown.")

            markdown_record = {
                "name": source_label,
                "source_path": source_path,
                "method": str(results.get("method") or ""),
                "output_path": output_path,
            }
            markdown_sources.append(markdown_record)
            figures.extend(
                self._collect_markdown_figures(
                    markdown,
                    source_name=source_label,
                    markdown_output_path=output_path,
                )
            )
            output_files.extend(list(conversion.get("output_files") or []))

        return SourceUnderstanding(
            document_type=self._select_source_document_type(fallback_document_type, markdown_sources),
            markdown_sources=markdown_sources,
            figures=figures,
            output_files=output_files,
            extraction_warnings=warnings,
        )

    @classmethod
    def _register_existing_markdown_source(
        cls,
        source_input: SourceInput,
        *,
        source_label: str,
    ) -> tuple[dict[str, Any], str, dict[str, Any]] | None:
        """Register an already prepared Markdown source without calling an expert."""
        source_path = str(source_input.path or "").strip()
        if cls._looks_like_url(source_path) or not cls._is_markdown_path(source_path):
            return None

        try:
            markdown_file = resolve_workspace_path(source_path)
        except Exception:
            return None
        if not markdown_file.exists() or not markdown_file.is_file():
            return None

        markdown = markdown_file.read_text(encoding="utf-8")
        output_path = workspace_relative_path(markdown_file)
        markdown_record = {
            "name": source_label,
            "source_path": source_path,
            "method": "local:markdown_passthrough",
            "output_path": output_path,
        }
        file_record = build_workspace_file_record(
            markdown_file,
            description="Prepared Markdown source for PPT planning.",
            source="ppt_product_manager",
            name=markdown_file.name,
        )
        return markdown_record, markdown, file_record

    @classmethod
    def _register_existing_image_source(
        cls,
        source_input: SourceInput,
        *,
        source_label: str,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Register an existing image source as a ready figure for PPT planning."""
        source_path = str(source_input.path or "").strip()
        if cls._looks_like_url(source_path) or not cls._is_image_path(source_path):
            return None

        try:
            image_file = resolve_workspace_path(source_path)
        except Exception:
            return None
        if not image_file.exists() or not image_file.is_file():
            return None

        output_path = workspace_relative_path(image_file)
        figure_record = {
            "source_name": source_label,
            "alt": source_input.description or source_label,
            "path": output_path,
            "material_type": "image_input",
        }
        file_record = build_workspace_file_record(
            image_file,
            description="Prepared image source for PPT planning.",
            source="ppt_product_manager",
            name=image_file.name,
        )
        return figure_record, file_record

    @staticmethod
    def _build_source_output_dir(state: dict[str, Any]) -> Path:
        """Return a deterministic directory for converted source Markdown files."""
        session_id = str(state.get("sid") or "ppt-session").strip()
        turn_index = int(state.get("turn_index", 0) or 0)
        step = int(state.get("step", 0) or 0)
        output_dir = generated_session_dir(session_id, turn_index=turn_index) / f"ppt_sources_step_{step}"
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    @classmethod
    def _build_source_conversion_parameters(
        cls,
        source_input: SourceInput,
        *,
        runtime_state: dict[str, Any],
        source_index: int,
        output_dir: Path,
    ) -> dict[str, Any]:
        """Build AnythingToMD expert parameters for one source file or URL."""
        source_path = str(source_input.path or "").strip()
        source_label = source_input.name or source_path or f"source_{source_index}"
        output_file = output_dir / f"source_{source_index:02d}_{cls._safe_source_stem(source_label)}.md"
        parameters: dict[str, Any] = {
            "__session_id": str(runtime_state.get("sid") or "ppt-source"),
            "__turn_index": int(runtime_state.get("turn_index", 0) or 0),
            "__step": int(runtime_state.get("step", 0) or 0),
            "__expert_step": int(runtime_state.get("expert_step", 0) or 0) + source_index,
            "output_path": workspace_relative_path(output_file),
        }
        if cls._looks_like_url(source_path):
            parameters["url"] = source_path
        else:
            parameters["input_path"] = source_path
        return parameters

    @classmethod
    def _collect_markdown_figures(
        cls,
        markdown: str,
        *,
        source_name: str,
        markdown_output_path: str,
    ) -> list[dict[str, Any]]:
        """Collect Markdown image references as source material figure records."""
        figures: list[dict[str, Any]] = []
        for match in re.finditer(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)", markdown):
            raw_path = match.group("src").strip()
            figures.append(
                {
                    "source_name": source_name,
                    "alt": cls._clean_markdown_text(match.group("alt")),
                    "path": cls._resolve_markdown_relative_path(
                        raw_path,
                        markdown_output_path=markdown_output_path,
                    ),
                    "markdown_output_path": markdown_output_path,
                }
            )
        return figures

    @staticmethod
    def _resolve_markdown_relative_path(raw_path: str, *, markdown_output_path: str) -> str:
        """Resolve a Markdown image path relative to the generated Markdown file when possible."""
        clean_path = str(raw_path or "").strip()
        if not clean_path or PptProductManager._looks_like_url(clean_path):
            return clean_path
        try:
            markdown_file = resolve_workspace_path(markdown_output_path)
            candidate = (markdown_file.parent / clean_path).resolve()
            if candidate.exists():
                return workspace_relative_path(candidate)
        except Exception:
            return clean_path
        return clean_path

    @staticmethod
    def _select_source_document_type(
        fallback_document_type: str,
        markdown_sources: list[dict[str, Any]],
    ) -> str:
        """Keep document type inference lightweight for source material preparation."""
        if len(markdown_sources) > 1 and fallback_document_type == "brief":
            return "mixed"
        return fallback_document_type or "brief"

    @staticmethod
    def _safe_source_stem(value: str) -> str:
        """Build a short filesystem-safe stem for converted source outputs."""
        stem = Path(str(value or "source")).stem or "source"
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
        return safe[:48] or "source"

    @staticmethod
    def _is_markdown_path(value: str) -> bool:
        """Return whether a path points to an existing Markdown-format source."""
        suffix = Path(str(value or "")).suffix.lower()
        return suffix in {".md", ".markdown", ".mdown", ".mkd"}

    @staticmethod
    def _is_image_path(value: str) -> bool:
        """Return whether a path points to an image-format source."""
        suffix = Path(str(value or "")).suffix.lower()
        return suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}

    @staticmethod
    def _clean_markdown_text(text: str) -> str:
        """Remove common Markdown syntax from one short material label."""
        clean_text = str(text or "").strip()
        clean_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean_text)
        clean_text = clean_text.replace("**", "").replace("__", "").replace("`", "")
        clean_text = re.sub(r"\s+", " ", clean_text)
        return clean_text.strip(" \t-:*")

    @staticmethod
    def _is_generic_ppt_request(topic: str) -> bool:
        """Return whether a task is too thin to generate a meaningful deck."""
        normalized = re.sub(r"[\s，。,.！!？?：:；;、]+", "", str(topic or "").lower())
        return normalized in {
            "ppt",
            "pptx",
            "做ppt",
            "做个ppt",
            "做一个ppt",
            "生成ppt",
            "生成一个ppt",
            "制作ppt",
            "制作一个ppt",
            "帮我做ppt",
            "帮我做个ppt",
            "做pptx",
            "做个pptx",
            "做一个pptx",
            "生成pptx",
            "生成一个pptx",
        }

    @staticmethod
    def _looks_like_url(value: str) -> bool:
        """Return whether a source path is an HTTP URL."""
        return value.lower().startswith(("http://", "https://"))

    def _build_source_converter(
        self,
        *,
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
    ) -> Any | None:
        """Build an AnythingToMD expert-agent converter when available."""
        if (
            "AnythingToMD" in expert_agents
            and artifact_service is not None
            and hasattr(tool_context, "_invocation_context")
        ):

            async def _dispatch_converter(source_input: SourceInput, parameters: dict[str, Any]) -> dict[str, Any]:
                invocation = await dispatch_expert_call(
                    agent_name="AnythingToMD",
                    prompt=json.dumps(parameters, ensure_ascii=False),
                    tool_context=tool_context,
                    expert_agents=expert_agents,
                    app_name=app_name,
                    artifact_service=artifact_service,
                )
                return invocation.current_output

            return _dispatch_converter

        return None

    def prepare_confirmed_requirement(
        self,
        *,
        task: str,
        inputs: Any | None = None,
        output: dict[str, Any] | None = None,
        source_understanding: SourceUnderstanding | None = None,
    ) -> ConfirmedRequirement:
        """Create the first deterministic ConfirmedRequirement draft."""
        clean_task = str(task or "").strip()
        if not clean_task:
            raise ValueError("task is required")

        output_options = dict(output or {})
        route, route_confirmed = self._select_route(clean_task, output_options)
        raw_inputs = self._normalize_raw_inputs(inputs)
        source_inputs = self._normalize_source_inputs(raw_inputs)
        reference_assets = self._normalize_reference_assets(raw_inputs)
        slide_policy = self._infer_slide_count_policy(clean_task, output_options)
        source_understanding = source_understanding or SourceUnderstanding(
            document_type=self._infer_document_type(source_inputs),
        )
        audience = self._select_output_text(output_options, ("audience", "target_audience"))
        scenario_text = " ".join(
            [
                clean_task,
                self._select_output_text(output_options, ("scenario", "use_case", "purpose", "occasion")),
            ]
        ).strip()

        return ConfirmedRequirement(
            route=route,
            request_brief=clean_task,
            topic=self._infer_topic(clean_task),
            audience=audience or self._infer_audience(clean_task),
            scenario=self._infer_scenario(scenario_text),
            slide_count_policy=slide_policy,
            language=self._infer_language(clean_task, output_options),
            aspect_ratio=self._select_aspect_ratio(clean_task, output_options),
            output_format="pptx",
            source_inputs=source_inputs,
            source_understanding=source_understanding,
            reference_assets=reference_assets,
            template_requirement=self._infer_template_requirement(
                clean_task,
                route,
                source_inputs,
                output_options,
            ),
            style_requirement=StyleRequirement(style_keywords=self._infer_style_keywords(clean_task, output_options)),
            editability_requirement=self._infer_editability_requirement(clean_task, route, output_options),
            confirmed_by_user=route_confirmed,
        )

    @staticmethod
    def _select_route(task: str, output: dict[str, Any]) -> tuple[str, bool]:
        """Select the requested route while keeping HTML as the first MVP route."""
        raw_route = str(output.get("route") or output.get("ppt_route") or "").strip().lower()
        if raw_route in {"html", "svg", "xml"}:
            return raw_route, True

        normalized = task.lower()
        route_patterns = {
            "xml": ("xml route", "xml路线", "原生模板", "套用模板", "上传pptx模板", "editable template"),
            "svg": ("svg route", "svg路线", "ppt-master", "drawingml"),
            "html": ("html route", "html路线", "html deck", "网页演示"),
        }
        for route, patterns in route_patterns.items():
            if any(pattern in normalized for pattern in patterns):
                return route, True
        return "html", False

    @staticmethod
    def _select_output_text(output: dict[str, Any], keys: tuple[str, ...]) -> str:
        """Return the first non-empty text value from output options."""
        for key in keys:
            raw_value = output.get(key)
            if isinstance(raw_value, str) and raw_value.strip():
                return raw_value.strip()
        return ""

    @staticmethod
    def _dedupe_preserve_order(items: list[str]) -> list[str]:
        """Deduplicate short string lists while preserving original order."""
        seen: set[str] = set()
        deduped: list[str] = []
        for item in items:
            clean_item = str(item or "").strip()
            if not clean_item or clean_item in seen:
                continue
            seen.add(clean_item)
            deduped.append(clean_item)
        return deduped

    @staticmethod
    def _normalize_raw_inputs(inputs: Any) -> list[Any]:
        """Normalize raw product inputs into a list while preserving only user-provided values."""
        if inputs is None:
            return []
        if isinstance(inputs, list):
            return inputs
        if isinstance(inputs, dict):
            normalized_items: list[Any] = []
            for key in ("documents", "files", "assets", "sources", "source_inputs"):
                value = inputs.get(key)
                if isinstance(value, list):
                    normalized_items.extend(value)
            return normalized_items or [inputs]
        return []

    @staticmethod
    def _normalize_source_inputs(inputs: list[Any]) -> list[SourceInput]:
        """Normalize raw tool inputs into SourceInput records."""
        source_inputs: list[SourceInput] = []
        for index, item in enumerate(inputs, start=1):
            if isinstance(item, str):
                path = item.strip()
                if not path:
                    continue
                source_inputs.append(
                    SourceInput(
                        name=PptProductManager._source_name_from_path(path, fallback=f"input_{index}"),
                        path=path,
                        role="source",
                    )
                )
                continue
            if not isinstance(item, dict):
                continue
            if not PptProductManager._looks_like_document_input(item):
                continue
            role = str(item.get("role") or item.get("type") or "source").strip() or "source"
            if role == "reference":
                continue
            source_inputs.append(
                SourceInput(
                    name=str(item.get("name") or item.get("filename") or f"input_{index}"),
                    path=str(item.get("path") or item.get("url") or item.get("uri") or item.get("file_path") or ""),
                    mime_type=str(item.get("mime_type") or item.get("mime") or ""),
                    role=role,
                    description=str(item.get("description") or ""),
                )
            )
        return source_inputs

    @staticmethod
    def _source_name_from_path(path: str, *, fallback: str) -> str:
        """Infer a stable display name from a file path or URL."""
        cleaned = str(path or "").split("?", 1)[0].rstrip("/")
        name = Path(cleaned).name
        return name or fallback

    @staticmethod
    def _looks_like_document_input(item: dict[str, Any]) -> bool:
        """Return whether an input item is a real user document or asset reference."""
        return bool(str(item.get("path") or item.get("url") or item.get("uri") or item.get("file_path") or "").strip())

    @staticmethod
    def _normalize_reference_assets(inputs: list[Any]) -> list[ReferenceAsset]:
        """Normalize raw tool inputs into reference asset records."""
        reference_assets: list[ReferenceAsset] = []
        for index, item in enumerate(inputs, start=1):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or item.get("type") or "").strip()
            if role != "reference":
                continue
            reference_assets.append(
                ReferenceAsset(
                    name=str(item.get("name") or item.get("filename") or f"reference_{index}"),
                    path=str(item.get("path") or item.get("url") or item.get("uri") or item.get("file_path") or ""),
                    asset_type=str(item.get("asset_type") or item.get("mime_type") or ""),
                    role="reference",
                    description=str(item.get("description") or ""),
                )
            )
        return reference_assets

    @staticmethod
    def _infer_slide_count_policy(task: str, output: dict[str, Any] | None = None) -> SlideCountPolicy:
        """Infer a conservative slide count policy from the task text."""
        output = output or {}
        raw_output_count = output.get("slide_count") or output.get("slides") or output.get("page_count") or output.get("pages")
        if raw_output_count not in (None, ""):
            try:
                target = max(1, int(raw_output_count))
                return SlideCountPolicy(
                    minimum=target,
                    maximum=target,
                    target=target,
                    source="user",
                )
            except (TypeError, ValueError):
                pass

        upper_bound_match = re.search(
            r"(?:小于|少于|低于|不超过|不超|最多|至多|以内|以下|<|<=)\s*(\d{1,2})\s*(?:页|p|pages?|slides?|张)",
            task,
            flags=re.IGNORECASE,
        )
        if upper_bound_match:
            raw_bound = max(1, int(upper_bound_match.group(1)))
            maximum = max(1, raw_bound - 1) if any(token in task for token in ("小于", "少于", "低于", "<")) else raw_bound
            target = min(maximum, max(1, maximum - 1))
            return SlideCountPolicy(
                minimum=1,
                maximum=maximum,
                target=target,
                source="user",
            )

        match = re.search(r"(\d{1,2})\s*(?:页|p|pages?|slides?|张)", task, flags=re.IGNORECASE)
        if match:
            target = max(1, int(match.group(1)))
            return SlideCountPolicy(
                minimum=target,
                maximum=target,
                target=target,
                source="user",
            )
        return SlideCountPolicy()

    @staticmethod
    def _infer_document_type(source_inputs: list[SourceInput]) -> str:
        """Infer the source document category from file extensions."""
        if not source_inputs:
            return "brief"
        suffixes = {Path(item.path or item.name).suffix.lower().lstrip(".") for item in source_inputs}
        suffixes.discard("")
        if len(suffixes) > 1:
            return "mixed"
        suffix = next(iter(suffixes), "")
        if suffix in {"pdf"}:
            return "pdf"
        if suffix in {"doc", "docx"}:
            return "word"
        if suffix in {"ppt", "pptx", "pptm"}:
            return "pptx"
        if suffix in {"md", "markdown", "txt"}:
            return "markdown"
        if suffix in {"png", "jpg", "jpeg", "webp", "gif"}:
            return "image"
        return "mixed"

    @staticmethod
    def _infer_topic(task: str) -> str:
        """Infer the public-facing deck topic without copying task instructions."""
        clean_task = PptProductManager._normalize_request_text(task)
        topic = (
            PptProductManager._topic_from_audience_pattern(clean_task)
            or PptProductManager._topic_from_action_pattern(clean_task)
            or PptProductManager._topic_from_subject_marker(clean_task)
            or PptProductManager._topic_from_purpose_pattern(clean_task)
            or PptProductManager._topic_from_cleaned_task(clean_task)
        )
        topic = PptProductManager._clean_public_topic(topic, original_task=clean_task)
        return topic or clean_task[:120].strip()

    @staticmethod
    def _normalize_request_text(task: str) -> str:
        """Normalize request text before lightweight requirement extraction."""
        return re.sub(r"\s+", " ", str(task or "").strip())

    @staticmethod
    def _topic_from_audience_pattern(task: str) -> str:
        """Extract topic from phrases like `面向大学生的AI科普PPTX`."""
        match = re.search(
            r"(?:面向|给|向|用于向|用于给|用来给|为)[^，。,.；;：:]{2,40}?的(?P<topic>[^，。,.；;：:]{1,50}?)(?:pptx?|PPTX?|幻灯片|演示文稿|$)",
            task,
            flags=re.IGNORECASE,
        )
        return str(match.group("topic") or "") if match else ""

    @staticmethod
    def _topic_from_action_pattern(task: str) -> str:
        """Extract topic from action phrases such as `科普AI` or `介绍产品`."""
        match = re.search(
            r"(?:科普|介绍|讲解|讲|说明|分享|培训|解读)(?P<topic>[A-Za-z0-9\u4e00-\u9fff][^，。,.；;：:]{0,50})",
            task,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        raw_topic = str(match.group("topic") or "").strip()
        action = task[match.start() : match.start() + 2]
        if action == "科普" and "科普" not in raw_topic:
            return f"{raw_topic}科普"
        return raw_topic

    @staticmethod
    def _topic_from_subject_marker(task: str) -> str:
        """Extract topic from explicit subject markers."""
        match = re.search(
            r"(?:主题(?:是|为)?|关于|围绕|around|about)\s*(?P<topic>[^，。,.；;：:]{1,60})",
            task,
            flags=re.IGNORECASE,
        )
        return str(match.group("topic") or "") if match else ""

    @staticmethod
    def _topic_from_purpose_pattern(task: str) -> str:
        """Extract topic from purpose phrases that do not describe an audience."""
        match = re.search(
            r"(?:用于|用来|为)(?!向|给)(?P<topic>[^，。,.；;：:]{2,60})",
            task,
            flags=re.IGNORECASE,
        )
        return str(match.group("topic") or "") if match else ""

    @staticmethod
    def _topic_from_cleaned_task(task: str) -> str:
        """Build a fallback topic by removing delivery instructions."""
        topic = re.sub(
            r"^(?:请|麻烦|帮我|给我)?(?:做一个|做个|做|制作一个|制作|生成一个|生成|产出|创建|create|make|build)\s*",
            "",
            task,
            flags=re.IGNORECASE,
        )
        topic = re.sub(r"^(?:一个|一份|一套)?\s*(?:pptx?|PPTX?|幻灯片|演示文稿)\s*", "", topic, flags=re.IGNORECASE)
        topic = re.split(r"[，。,.；;]", topic, maxsplit=1)[0]
        topic = re.sub(r"^(?:用于|用来|为|给|向|面向)", "", topic)
        return topic

    @staticmethod
    def _clean_public_topic(topic: str, *, original_task: str) -> str:
        """Clean a candidate topic so it is safe to display inside the deck."""
        clean_topic = PptProductManager._normalize_request_text(topic)
        clean_topic = re.sub(r"(?i)pptx?|powerpoint|slide deck|slides?", "", clean_topic)
        clean_topic = re.sub(r"(?:幻灯片|演示文稿|语言为中文|中文|风格.*$|内容需.*$)", "", clean_topic)
        clean_topic = re.sub(r"^\d{1,2}\s*(?:页|p|pages?|slides?|张)\s*", "", clean_topic, flags=re.IGNORECASE)
        clean_topic = re.sub(r"^(?:的|个|一个|一份|一套|用于|用来|给|向|面向)+", "", clean_topic)
        clean_topic = re.sub(r"(?:图文并茂|配图|插图|图片|小于\d{1,2}页|少于\d{1,2}页).*$", "", clean_topic)
        clean_topic = clean_topic.strip(" ：:，。,.；;、-\"'“”‘’《》")
        if clean_topic in {"", "个", "一个", "一份", "一套"}:
            return ""
        clean_topic = re.sub(r"(?i)ai", "AI", clean_topic)
        if clean_topic.lower() == "ai":
            clean_topic = "AI"
        if clean_topic == "AI" and "科普" in original_task:
            clean_topic = "AI科普"
        return clean_topic[:60].strip()

    @staticmethod
    def _clean_audience(audience: str, *, split_possessive: bool = True) -> str:
        """Clean audience text extracted from a PPT request."""
        clean_audience = PptProductManager._normalize_request_text(audience)
        if split_possessive and "的" in clean_audience:
            clean_audience = clean_audience.split("的", 1)[0]
        clean_audience = re.sub(r"^(?:一个|一份|一套|的|给|向|面向)+", "", clean_audience)
        clean_audience = re.sub(r"(?i)pptx?|powerpoint|slides?", "", clean_audience)
        return clean_audience.strip(" ：:，。,.；;、-")[:60]

    @staticmethod
    def _infer_audience(task: str) -> str:
        """Infer the intended audience from common PPT request phrasing."""
        clean_task = PptProductManager._normalize_request_text(task)
        audience_patterns = (
            r"(?:面向|给|向|用于向|用于给|用来给|为)(?P<audience>[^，。,.；;：:]{2,40}?)(?:科普|介绍|讲解|讲|说明|分享|培训|汇报|展示|演示)",
            r"面向(?P<audience>[^，。,.；;：:]{2,40}?)的",
            r"for (?P<audience>[A-Za-z][A-Za-z0-9\s-]{2,60}?)(?: about| on| to|,|\.|$)",
        )
        for pattern in audience_patterns:
            match = re.search(pattern, clean_task, flags=re.IGNORECASE)
            if match:
                audience = PptProductManager._clean_audience(match.group("audience"))
                if audience:
                    return audience
        return ""

    @staticmethod
    def _infer_scenario(task: str) -> str:
        """Infer a coarse presentation scenario from the request text."""
        normalized = task.lower()
        explicit_scenario = PptProductManager._extract_explicit_requirement_text(
            task,
            ("场景", "使用场景", "汇报场景", "scenario", "use case"),
        )
        if explicit_scenario:
            return explicit_scenario
        scenario_keywords = {
            "课堂/讲座": ("课堂", "讲座", "课程", "教学", "class", "lecture"),
            "组会": ("组会", "group meeting", "lab meeting"),
            "发布会": ("发布会", "launch", "发布"),
            "汇报": ("汇报", "review", "report"),
            "培训": ("培训", "training", "workshop"),
            "答辩": ("答辩", "defense"),
            "路演": ("路演", "pitch"),
        }
        for scenario, keywords in scenario_keywords.items():
            if any(keyword in normalized for keyword in keywords):
                return scenario
        return ""

    @staticmethod
    def _infer_language(task: str, output: dict[str, Any] | None = None) -> str:
        """Infer output language from the task text."""
        raw_language = str((output or {}).get("language") or "").strip()
        if raw_language:
            return raw_language
        return "zh-CN" if re.search(r"[\u4e00-\u9fff]", task) else "en"

    @staticmethod
    def _select_aspect_ratio(task: str, output: dict[str, Any]) -> str:
        """Select a supported slide aspect ratio."""
        raw_ratio = str(output.get("aspect_ratio") or output.get("ratio") or "").strip()
        if raw_ratio in {"16:9", "4:3"}:
            return raw_ratio
        return "4:3" if "4:3" in task else "16:9"

    @staticmethod
    def _infer_template_requirement(
        task: str,
        route: str,
        source_inputs: list[SourceInput],
        output: dict[str, Any],
    ) -> TemplateRequirement:
        """Infer template intent without analyzing the template yet."""
        normalized = task.lower()
        has_pptx_source = any(Path(item.path or item.name).suffix.lower() in {".pptx", ".pptm"} for item in source_inputs)
        asks_template = any(keyword in normalized for keyword in ("template", "模板", "套用"))
        template_id = str(output.get("template_id") or output.get("template") or "").strip()
        template_path = str(output.get("template_path") or "").strip()
        if route == "xml" or has_pptx_source and asks_template:
            return TemplateRequirement(
                use_template=True,
                template_source="user",
                template_path=template_path,
                notes="User PPTX template requires XML route analysis.",
            )
        if route == "html" and template_id:
            return TemplateRequirement(
                use_template=True,
                template_source="system",
                template_id=template_id,
                notes="HTML route uses the explicitly selected system template.",
            )
        if route == "html":
            return TemplateRequirement(
                use_template=False,
                template_source="none",
                notes="HTML route uses no-template free design when no template is explicitly selected.",
            )
        if route == "svg":
            return TemplateRequirement(use_template=False, template_source="none", notes="SVG route can later opt into system SVG templates.")
        return TemplateRequirement()

    @staticmethod
    def _infer_style_keywords(task: str, output: dict[str, Any] | None = None) -> list[str]:
        """Extract a small set of style keywords from common request language."""
        style_keywords: list[str] = []
        keyword_map = {
            "business": ("商务", "汇报", "executive", "business"),
            "editorial": ("杂志", "editorial", "magazine"),
            "academic": ("学术", "答辩", "academic"),
            "minimal": ("极简", "minimal"),
            "playful": ("活泼", "幼儿园", "小朋友", "儿童", "少儿", "playful", "kindergarten", "children", "kids"),
            "kid_friendly": ("幼儿园", "小朋友", "儿童", "少儿", "孩子", "低龄", "kindergarten", "children", "kids"),
            "illustrated": ("图文并茂", "配图", "插图", "图片", "图画", "绘本", "单词卡片", "flashcard", "illustrated", "visual"),
        }
        output = output or {}
        positive_style_text = " ".join(
            [
                task,
                str(output.get("style") or ""),
                str(output.get("tone") or ""),
                str(output.get("visual_style") or ""),
            ]
        ).lower()
        negative_style_text = " ".join(
            [
                str(output.get("must_not_include") or ""),
                str(output.get("negative_constraints") or ""),
                str(output.get("avoid") or ""),
                str(output.get("exclude") or ""),
            ]
        ).lower()
        task_negative_text = " ".join(
            match.group(0)
            for match in re.finditer(
                r"(?:不是|不要|禁止|避免|不需要|不能|must not|do not|avoid)[^，。,.；;]{0,40}",
                task,
                flags=re.IGNORECASE,
            )
        ).lower()
        negative_style_text = f"{negative_style_text} {task_negative_text}".strip()
        for label, keywords in keyword_map.items():
            if any(keyword in negative_style_text for keyword in keywords):
                continue
            if any(keyword in positive_style_text for keyword in keywords):
                style_keywords.append(label)
        return style_keywords

    @staticmethod
    def _infer_editability_requirement(
        task: str,
        route: str,
        output: dict[str, Any],
    ) -> EditabilityRequirement:
        """Infer editability expectations and route caveats."""
        normalized = task.lower()
        raw_level = str(output.get("editability") or output.get("editable_level") or "").strip().lower()
        if raw_level in {"low", "medium", "high", "native", "unknown"}:
            level = raw_level
        elif any(keyword in normalized for keyword in ("可编辑", "editable", "原生")):
            level = "native" if route == "xml" else "high"
        elif route == "html":
            level = "high"
        elif route == "svg":
            level = "medium"
        else:
            level = "native"
        notes = (
            "HTML route exports editable text boxes and vector shapes, but does not preserve uploaded PPTX templates."
            if route == "html"
            else ""
        )
        return EditabilityRequirement(level=level, must_preserve_template=route == "xml", notes=notes)


def _build_requirement_analysis_user_message(
    *,
    mode: str,
    task: str,
    raw_inputs: list[Any],
    output: dict[str, Any],
    fallback_requirement: ConfirmedRequirement,
    existing_requirement: ConfirmedRequirement | None,
    user_revision: str,
) -> str:
    """Build the user message for the internal requirement analysis agent."""
    payload = {
        "mode": mode,
        "user_task": task,
        "user_revision": user_revision,
        "raw_inputs": _summarize_raw_inputs(raw_inputs),
        "output_options": output,
        "fallback_requirement_json": fallback_requirement.model_dump(mode="json"),
        "existing_requirement_json": (
            existing_requirement.model_dump(mode="json") if existing_requirement is not None else {}
        ),
    }
    return (
        "Normalize or revise the PPT requirement.\n"
        "Use the fallback JSON as schema/defaults. For revision mode, apply only the user_revision to the existing JSON.\n"
        "Return the final requirement only by calling save_ppt_confirmed_requirement_json.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _build_system_selection_user_message(
    *,
    task: str,
    output: dict[str, Any],
    requirement: ConfirmedRequirement,
    route_summaries: dict[str, dict[str, object]],
) -> str:
    """Build the user message for the internal PPT system-selection agent."""
    payload = {
        "user_task": task,
        "output_options": output,
        "confirmed_requirement_json": requirement.model_dump(mode="json"),
        "registered_routes": route_summaries,
        "selection_contract": {
            "system_type": "built_in_route | private_skill",
            "route": "html | svg | xml",
            "skill_name": "exact private skill folder name, or empty for built-in route",
            "output_format": "pptx | html | other single-file format",
            "reason": "short decision rationale grounded in the task and available systems",
        },
    }
    return (
        "Choose the PPT delivery system for this request.\n"
        "First call list_product_ppt_skills. Read relevant private skills when needed.\n"
        "Do not use hard-coded keyword-to-skill rules; decide from the task and actual available systems.\n"
        "Save the final choice only by calling save_ppt_system_selection.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _build_product_manager_skill_run_user_message(
    *,
    requirement: ConfirmedRequirement,
    content_plan: DeckContentPlan,
    system_selection: dict[str, Any],
    skill_content: str,
    available_experts: list[str],
) -> str:
    """Build the user message for a PptProductManager-led private skill run."""
    payload = {
        "confirmed_requirement_json": requirement.model_dump(mode="json"),
        "deck_content_plan_json": content_plan.model_dump(mode="json"),
        "system_selection": system_selection,
        "selected_skill_content": skill_content,
        "available_experts": available_experts,
        "output_contract": {
            "tool": "save_ppt_private_skill_html",
            "file_name": "index.html",
            "html_content": "complete standalone HTML presentation",
            "description": "short artifact description",
        },
    }
    return (
        "Run the selected private PPT skill as PptProductManager.\n"
        "Let the selected skill content drive the workflow, layout choices, resources, and optional expert/tool use.\n"
        "Use the confirmed requirement and deck content plan as content truth.\n"
        "Read additional skill files with read_product_ppt_skill_file when the skill references them.\n"
        "Use list_ppt_experts and invoke_ppt_expert when the skill needs a registered expert.\n"
        "Save the final artifact by calling save_ppt_private_skill_html.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _mask_private_skill_html_content_before_model(
    callback_context: Any,
    llm_request: Any,
) -> None:
    """Replace saved private-skill HTML tool arguments with workspace pointers."""
    state = getattr(callback_context, "state", {}) or {}
    masked_count = _mask_private_skill_html_content_in_request(llm_request, state=state)
    if masked_count <= 0:
        return

    try:
        state[PPT_PRIVATE_SKILL_MASKED_HTML_COUNT_STATE_KEY] = int(
            state.get(PPT_PRIVATE_SKILL_MASKED_HTML_COUNT_STATE_KEY, 0) or 0
        ) + masked_count
    except Exception:
        # Masking is a cost optimization. Never let bookkeeping block the model request.
        return


def _mask_private_skill_html_content_in_request(
    llm_request: Any,
    *,
    state: Any,
    threshold: int = PPT_PRIVATE_SKILL_HTML_CONTENT_MASK_THRESHOLD,
) -> int:
    """Mask oversized `html_content` values in private PPT save-tool calls."""
    masked_count = 0
    output_path = _saved_private_skill_output_path(state)
    contents = list(getattr(llm_request, "contents", []) or [])
    for content in contents:
        for part in list(getattr(content, "parts", []) or []):
            function_call = getattr(part, "function_call", None)
            if getattr(function_call, "name", "") != PPT_PRIVATE_SKILL_HTML_SAVE_TOOL_NAME:
                continue
            args = getattr(function_call, "args", None)
            if not isinstance(args, dict):
                continue
            html_content = args.get("html_content")
            if not isinstance(html_content, str) or len(html_content) <= threshold:
                continue

            masked_args = dict(args)
            masked_args["html_content"] = _build_private_skill_html_mask_placeholder(
                output_path=output_path,
                file_name=str(args.get("file_name") or "").strip(),
                original_length=len(html_content),
            )
            function_call.args = masked_args
            masked_count += 1
    return masked_count


def _saved_private_skill_output_path(state: Any) -> str:
    """Return the workspace-relative output path saved by the private PPT tool."""
    try:
        private_build = state.get(PPT_PRIVATE_SKILL_BUILD_STATE_KEY) or {}
        if isinstance(private_build, dict):
            output_path = str(private_build.get("output_path") or "").strip()
            if output_path:
                return output_path
        current_output = state.get("current_output") or {}
        if isinstance(current_output, dict):
            output_path = str(current_output.get("output_path") or "").strip()
            if output_path:
                return output_path
    except Exception:
        return ""
    return ""


def _build_private_skill_html_mask_placeholder(
    *,
    output_path: str,
    file_name: str,
    original_length: int,
) -> str:
    """Build a compact LLM-visible placeholder for already-saved HTML content."""
    location = output_path or file_name or "workspace output path unavailable"
    return (
        "<tool_output_masked>\n"
        "[Private PPT HTML content was omitted because it was already saved to workspace. "
        f"Full file: {location}. Original length: {original_length} chars.]\n"
        "</tool_output_masked>"
    )


async def _call_system_selection_builder(system_selection_builder: Any, **kwargs: Any) -> Any:
    """Call an injected PPT system selector used by tests and controlled integrations."""
    result = system_selection_builder(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _strip_html_code_fence(html_content: str) -> str:
    """Remove a single Markdown code fence around generated HTML."""
    content = str(html_content or "").strip()
    match = re.fullmatch(r"```(?:html)?\s*(?P<body>.*?)\s*```", content, flags=re.DOTALL | re.IGNORECASE)
    if match:
        content = str(match.group("body") or "").strip()
    return f"{content}\n" if content else ""


def _extract_page_bullet_texts(page: DeckPagePlan) -> list[str]:
    """Extract short bullet text from generic content blocks."""
    texts: list[str] = []
    for block in page.content_blocks:
        if not isinstance(block, dict):
            continue
        for key in ("text", "content", "body", "title"):
            raw_value = block.get(key)
            if isinstance(raw_value, str) and raw_value.strip():
                texts.append(raw_value.strip())
        for key in ("items", "bullets", "points"):
            raw_items = block.get(key)
            if isinstance(raw_items, list):
                texts.extend(str(item).strip() for item in raw_items if str(item or "").strip())
    if not texts and page.asset_intent:
        texts.append(page.asset_intent)
    return texts


def _summarize_raw_inputs(raw_inputs: list[Any]) -> list[dict[str, Any]]:
    """Return a compact, safe summary of raw source inputs for the requirement agent."""
    summaries: list[dict[str, Any]] = []
    for index, item in enumerate(raw_inputs, start=1):
        if isinstance(item, str):
            summaries.append(
                {
                    "index": index,
                    "kind": "path",
                    "name": PptProductManager._source_name_from_path(item, fallback=f"input_{index}"),
                    "path": item,
                }
            )
            continue
        if isinstance(item, dict):
            summaries.append(
                {
                    "index": index,
                    "kind": "record",
                    "name": str(item.get("name") or item.get("filename") or f"input_{index}"),
                    "path": str(item.get("path") or item.get("url") or item.get("uri") or item.get("file_path") or ""),
                    "role": str(item.get("role") or item.get("type") or ""),
                    "mime_type": str(item.get("mime_type") or item.get("mime") or ""),
                    "description": str(item.get("description") or ""),
                }
            )
    return summaries


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
    """Pick the artifact service for the internal requirement-analysis runner."""
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
    """Create a child ADK runner for an internal PPT product agent."""
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
