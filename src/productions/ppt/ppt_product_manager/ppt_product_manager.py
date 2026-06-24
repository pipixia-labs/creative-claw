"""ADK-native product manager skeleton for Creative Claw PPT tasks."""

from __future__ import annotations

import copy
import hashlib
import html as html_lib
import inspect
import json
import mimetypes
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from google.adk import Context, Workflow
from google.adk.agents import BaseAgent, LlmAgent
from google.adk.artifacts import BaseArtifactService, InMemoryArtifactService
from google.adk.tools.tool_context import ToolContext
from google.adk.workflow import node
from pydantic import PrivateAttr, ValidationError

from conf.llm import build_llm
from conf.path import PROJECT_PATH
from src.productions.ppt.planning import PptContentPlanner
from src.productions.ppt.ppt_product_manager.product_ppt_skills import (
    ProductPptSkillRegistry,
)
from src.productions.ppt.routes.html import (
    PPT_HTML_PAGE_GENERATION_EXPERT_NAME,
    build_html_route_with_agent,
    build_ppt_html_page_generation_expert,
    prepare_html_template,
    run_html_page_generation_expert,
)
from src.productions.ppt.routes.svg import (
    PPT_DESIGN_CONFIRMATION_STATE_KEY,
    PPT_DESIGN_STRATEGY_EXPERT_NAME,
    PPT_DESIGN_STRATEGY_STATE_KEY,
    PPT_SVG_DECK_EXECUTOR_EXPERT_NAME,
    PPT_SVG_EXECUTION_PLAN_STATE_KEY,
    PPT_SVG_ROUTE_CONTENT_PLAN_KEY,
    PPT_SVG_ROUTE_GENERATED_PAGES_KEY,
    PPT_SVG_ROUTE_OUTPUT_DIR_KEY,
    build_ppt_design_strategy_expert,
    build_ppt_svg_deck_executor_expert,
    build_svg_route_with_agent,
    check_svg_pages_quality,
    export_svg_pages_to_pptx,
)
from src.productions.ppt.routes.svg.native_converter import validate_svg_content
from src.productions.ppt.routes import PptRouteRegistration, build_default_ppt_route_registry
from src.productions.ppt.schemas import (
    ConfirmedRequirement,
    DeckContentPlan,
    DeckPageAsset,
    DeliveryManifest,
    EditabilityRequirement,
    PptAdkConfirmationRequest,
    PptAdkConfirmationResponse,
    PptContentPlanRevisionResult,
    PptContentPlanningResult,
    PptAssetResolutionResult,
    PptDesignConfirmation,
    PptDesignStrategy,
    PptFinalDeliveryResult,
    PptPrivateSkillDeliveryResult,
    PptPrivateSkillExecutionResult,
    PptProductRequest,
    PptProductResult,
    PptRequirementAnalysisResult,
    PptRequirementRevisionResult,
    PptRouteExecutionResult,
    PptSourcePreparationResult,
    PptSystemSelection,
    PptSystemSelectionResult,
    PptSvgExecutionPlan,
    PptSvgPageResult,
    PptWorkflowState,
    QualityReviewResult,
    ReferenceAsset,
    SlideCountPolicy,
    SourceInput,
    SourceUnderstanding,
    StyleRequirement,
    TemplateRequirement,
)
from src.productions.ppt.schemas.contracts import PPT_PRODUCT_RESULT_SCHEMA_VERSION
from src.runtime.expert_dispatcher import ExpertInvocationRequest, dispatch_expert_request
from src.runtime.agent_tool_transport import run_agent_tool, supports_agent_tool_context
from src.runtime.adk_compat import has_invocation_context, invocation_app_name
from src.runtime.interaction_language import INTERACTION_LANGUAGE_STATE_KEY
from src.runtime.workspace import (
    build_workspace_file_record,
    generated_session_dir,
    normalize_workspace_markdown_image_paths,
    resolve_workspace_path,
    stage_attachment_into_workspace,
    workspace_relative_file_reference,
    workspace_relative_path,
    workspace_root,
)
from src.tools.builtin_tools import BuiltinToolbox

PPT_CONFIRMED_REQUIREMENT_STATE_KEY = "ppt_confirmed_requirement"
PPT_PRODUCT_RESULT_STATE_KEY = "ppt_product_result"
PPT_PRODUCT_REQUEST_STATE_KEY = "ppt_product_request"
PPT_WORKFLOW_STATE_KEY = "ppt_workflow_state"
PPT_ADK_CONFIRMATION_REQUEST_STATE_KEY = "ppt_adk_confirmation_request"
PPT_CONTENT_PLAN_REVISION_RESULT_STATE_KEY = "ppt_content_plan_revision_result"
PPT_CONTENT_PLAN_REVISION_OUTPUT_STATE_KEY = "ppt_content_plan_revision_output"
PPT_CONTENT_PLAN_REVISION_WORKFLOW_OUTPUT_KEY = "ppt_content_plan_revision_workflow_output"
PPT_ASSET_RESOLUTION_RESULT_STATE_KEY = "ppt_asset_resolution_result"
PPT_ROUTE_EXECUTION_RESULT_STATE_KEY = "ppt_route_execution_result"
PPT_ROUTE_OUTPUT_DIR_STATE_KEY = "ppt_route_output_dir"
PPT_FINAL_DELIVERY_RESULT_STATE_KEY = "ppt_final_delivery_result"
PPT_PRIVATE_SKILL_EXECUTION_RESULT_STATE_KEY = "ppt_private_skill_execution_result"
PPT_PRIVATE_SKILL_DELIVERY_RESULT_STATE_KEY = "ppt_private_skill_delivery_result"
PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION = "awaiting_requirement_confirmation"
PPT_STAGE_AWAITING_CONTENT_PLAN_CONFIRMATION = "awaiting_content_plan_confirmation"
PPT_STAGE_COMPLETED = "completed"
PPT_WORKFLOW_WAITING_SINCE_TURN_KEY = "waiting_since_turn_index"
PPT_WORKFLOW_LAST_CONSUMED_TURN_KEY = "last_consumed_turn_index"
PPT_REQUIREMENT_ANALYSIS_BASE_KEY = "ppt_requirement_analysis_base"
PPT_REQUIREMENT_ANALYSIS_RESULT_STATE_KEY = "ppt_requirement_analysis_result"
PPT_REQUIREMENT_ANALYSIS_OUTPUT_STATE_KEY = "ppt_requirement_analysis_output"
PPT_REQUIREMENT_ANALYSIS_AGENT_MESSAGE_KEY = "ppt_requirement_analysis_agent_message"
PPT_REQUIREMENT_ANALYSIS_WORKFLOW_OUTPUT_KEY = "ppt_requirement_analysis_workflow_output"
PPT_REQUIREMENT_REVISION_RESULT_STATE_KEY = "ppt_requirement_revision_result"
PPT_REQUIREMENT_REVISION_OUTPUT_STATE_KEY = "ppt_requirement_revision_output"
PPT_REQUIREMENT_REVISION_WORKFLOW_OUTPUT_KEY = "ppt_requirement_revision_workflow_output"
PPT_AUTO_CONFIRM_WORKFLOW_OUTPUT_KEY = "ppt_auto_confirm_workflow_output"
PPT_INITIAL_REQUEST_WORKFLOW_OUTPUT_KEY = "ppt_initial_request_workflow_output"
PPT_REQUIREMENT_CONFIRMATION_WORKFLOW_OUTPUT_KEY = "ppt_requirement_confirmation_workflow_output"
PPT_CONTENT_PLAN_CONFIRMATION_WORKFLOW_OUTPUT_KEY = "ppt_content_plan_confirmation_workflow_output"
PPT_SYSTEM_SELECTION_WORKFLOW_OUTPUT_KEY = "ppt_system_selection_workflow_output"
PPT_SOURCE_PREPARATION_RESULT_STATE_KEY = "ppt_source_preparation_result"
PPT_SOURCE_PREPARATION_WORKFLOW_OUTPUT_KEY = "ppt_source_preparation_workflow_output"
PPT_CONTENT_PLANNING_WORKFLOW_OUTPUT_KEY = "ppt_content_planning_workflow_output"
PPT_ASSET_RESOLUTION_WORKFLOW_OUTPUT_KEY = "ppt_asset_resolution_workflow_output"
PPT_ROUTE_EXECUTION_WORKFLOW_OUTPUT_KEY = "ppt_route_execution_workflow_output"
PPT_FINAL_DELIVERY_WORKFLOW_OUTPUT_KEY = "ppt_final_delivery_workflow_output"
PPT_PRIVATE_SKILL_EXECUTION_WORKFLOW_OUTPUT_KEY = "ppt_private_skill_execution_workflow_output"
PPT_PRIVATE_SKILL_DELIVERY_WORKFLOW_OUTPUT_KEY = "ppt_private_skill_delivery_workflow_output"
PPT_PRODUCT_SKILLS_STATE_KEY = "product_ppt_skills"
PPT_PRODUCT_ACTIVE_SKILL_STATE_KEY = "active_product_ppt_skill"
PPT_SYSTEM_SELECTION_STATE_KEY = "ppt_system_selection"
PPT_SYSTEM_SELECTION_RESULT_STATE_KEY = "ppt_system_selection_result"
PPT_SYSTEM_SELECTION_OUTPUT_STATE_KEY = "ppt_system_selection_output"
PPT_SYSTEM_SELECTION_BASE_KEY = "ppt_system_selection_base"
PPT_SYSTEM_SELECTION_AGENT_MESSAGE_KEY = "ppt_system_selection_agent_message"
PPT_PRIVATE_SKILL_BUILD_STATE_KEY = "ppt_private_skill_build"
PPT_PRIVATE_SKILL_MASKED_HTML_COUNT_STATE_KEY = "ppt_private_skill_masked_html_content_count"
PPT_PRIVATE_SKILL_HTML_CONTENT_MASK_THRESHOLD = 8000
PPT_PRIVATE_SKILL_HTML_SAVE_TOOL_NAME = "save_ppt_private_skill_html"
PPT_PRIVATE_SKILL_FORWARD_STATE_KEYS = (
    PPT_PRIVATE_SKILL_BUILD_STATE_KEY,
    PPT_SVG_EXECUTION_PLAN_STATE_KEY,
    PPT_SVG_ROUTE_GENERATED_PAGES_KEY,
    "generated",
    "new_files",
    "files_history",
    "final_file_paths",
    "current_output",
    "ppt_svg_quality_report",
    "ppt_svg_pptx_export",
    "ppt_route_build",
)
WorkspacePptxSnapshot = dict[str, tuple[int, int]]
PPT_REMOTE_SOURCE_MAX_BYTES = 100 * 1024 * 1024
PPT_REMOTE_SOURCE_KNOWN_EXTENSIONS = {
    ".csv",
    ".docx",
    ".gif",
    ".htm",
    ".html",
    ".jpeg",
    ".jpg",
    ".json",
    ".markdown",
    ".md",
    ".pdf",
    ".png",
    ".potm",
    ".potx",
    ".pptm",
    ".pptx",
    ".txt",
    ".webp",
    ".xlsm",
    ".xlsx",
    ".xml",
    ".yaml",
    ".yml",
}


def _coerce_ppt_workflow_state_payload(value: Any) -> dict[str, Any]:
    """Validate PPT workflow state while preserving dictionary session storage."""
    if not isinstance(value, dict):
        return {}
    try:
        return PptWorkflowState.model_validate(value).to_state_dict()
    except Exception:
        return dict(value)


def _persist_ppt_workflow_state(state: Any, workflow_state: dict[str, Any]) -> None:
    """Persist normalized PPT workflow state into an ADK state mapping."""
    state[PPT_WORKFLOW_STATE_KEY] = _coerce_ppt_workflow_state_payload(workflow_state)


def _ppt_product_error_result(message: str, *, next_actions: list[str] | None = None) -> dict[str, Any]:
    """Build a schema-valid PPT product error result."""
    return PptProductResult(
        status="error",
        phase="ppt_product_request",
        message=str(message or "").strip() or "PPT product request failed.",
        selected_route="html",
        warnings=[str(message or "").strip()] if str(message or "").strip() else [],
        next_actions=list(next_actions or []),
    ).model_dump(mode="json")


def _dedupe_strings(items: list[str]) -> list[str]:
    """Deduplicate non-empty strings while preserving order."""
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        clean_item = str(item or "").strip()
        if not clean_item or clean_item in seen:
            continue
        seen.add(clean_item)
        deduped.append(clean_item)
    return deduped


class PptProductManager(LlmAgent):
    """ADK LlmAgent that owns PPT product-line requests."""

    _project_root: Path = PrivateAttr()
    _content_planner: PptContentPlanner = PrivateAttr()
    _route_registry: dict[str, PptRouteRegistration] = PrivateAttr(default_factory=dict)
    _skill_registry: ProductPptSkillRegistry = PrivateAttr()
    _toolbox: BuiltinToolbox = PrivateAttr()
    _product_expert_agents: dict[str, BaseAgent] = PrivateAttr(default_factory=dict)
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
        self._toolbox = BuiltinToolbox()
        self._product_expert_agents = self.build_product_expert_agents()
        if provided_tools is None:
            self.tools = [
                self.list_product_ppt_skills,
                self.read_product_ppt_skill,
                self.read_product_ppt_skill_file,
                self.list_session_files,
                self.read_prepared_ppt_sources,
                self.list_dir,
                self.glob,
                self.grep,
                self.read_file,
                self.write_file,
                self.edit_file,
                self.exec_command,
                self.process_session,
                self.list_ppt_experts,
                self.invoke_ppt_expert,
                self.save_ppt_system_selection,
                self.save_ppt_private_skill_html,
                self.save_ppt_private_skill_pptx,
                self.save_ppt_design_strategy,
                self.save_ppt_svg_execution_plan,
                self.read_ppt_svg_execution_plan,
                self.save_ppt_svg_page,
                self.check_ppt_svg_quality,
                self.export_ppt_svg_to_pptx,
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
- SVG route: implemented route for design strategy, per-slide converter-safe SVG pages, SVG quality checks, and editable native DrawingML PPTX export.
- XML route: later route for user-uploaded PPTX templates and native OOXML editing.

# PPT system selection
- Creative Claw currently has multiple PPT-making systems under this product line.
- Private product-ppt skills live under `skills/product-ppt-skills/<skill-name>/SKILL.md`; those skills may describe a complete PPT production workflow.
- The product manager also owns the built-in HTML route, which generates an HTML deck, previews, quality report, and editable PPTX.
- Use only your private product-ppt skills, exposed through `list_product_ppt_skills` and `read_product_ppt_skill`.
- Do not ask the orchestrator to read PPT private skills for you.
- Before committing to a delivery system, run a PPT system-selection step. Base the decision on the user's actual task, available private skill names/descriptions/content, and registered built-in routes.
- If the user explicitly names a PPT system, route, skill, template workflow, or output method, follow that choice when it is available and report clearly when it is not implemented.
- If the user does not specify the PPT system and uploaded input includes PPTX/PPTM/POTX/POTM, choose the private `pptx` skill when available, use route `xml`, and produce an editable `.pptx` by modifying or creating from that PowerPoint input.
- If the user does not specify the PPT system and there is no PowerPoint input, choose between the built-in HTML route and the built-in SVG route based on task fit.
- Do not rely on hard-coded keyword-to-skill rules. Inspect the available private skills and choose from their actual metadata and content.

# Private skill execution
- When a private product-ppt skill is selected, you run that skill workflow directly as PptProductManager.
- Product-level PPT experts are registered by PptProductManager; for example, `PptHtmlPageGenerationExpert` generates editable PPT-friendly HTML slide fragments, `PptDesignStrategyExpert` prepares generic design strategy, and `PptSvgDeckExecutorExpert` generates SVG pages.
- Let the selected skill drive the execution order: read its referenced files, inspect session files, call workspace file/search/command tools, call available PPT product tools, call `invoke_ppt_expert` when it needs a registered expert, and save/export the final artifact with the skill-appropriate product tool.
- General workspace tools are available to private skills: list session files, list/glob/grep/read/write/edit files, run commands, and inspect background command sessions. Paths are workspace-relative unless a tool returns otherwise.
- Selected private skill resources are staged into the runtime workspace before execution. Prefer the staged skill path from state when running bundled scripts.
- SVG route tools are available for skill workflows: save design strategy, save/read SVG execution plan, save SVG pages, check SVG quality, and export SVG pages to PPTX. Saved SVG pages must obey the native converter subset in the SVG execution plan. When `check_ppt_svg_quality` or `export_ppt_svg_to_pptx` should use the pages already saved in state, pass an empty list for `svg_page_paths`.
- PPTX private skills must register the final `.pptx` with `save_ppt_private_skill_pptx` immediately after the file is generated and verified. Do not wait for optional rendering, visual QA, or expert checks before registering the deliverable. Do not call `save_ppt_private_skill_html` for a PPTX/template workflow unless the selected skill explicitly asks for an HTML artifact.
- When the user uploaded or referenced a PPTX/POTX template and the selected private skill is `pptx`, follow a template-based PPTX workflow: locate the uploaded template, analyze thumbnails/text/XML as preparation, choose reusable slides or layouts for the `DeckContentPlan`, create or edit a new `.pptx`, verify the file exists, then call `save_ppt_private_skill_pptx`.
- Template analysis artifacts such as thumbnails, Markdown extraction, XML inspection, or layout notes are not final deliverables. Do not stop after `thumbnail.py`, `markitdown`, or template inspection when a PPTX deliverable is requested.
- Optional QA, screenshot rendering, or expert checks may add warnings, but they must not block delivery after a valid `.pptx` has been registered.
- If a template-based PPTX workflow cannot produce a deck, return a concrete blocker such as missing template path, template parse failure, command failure, missing dependency, write failure, or unsupported template structure.
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

    @property
    def product_expert_agents(self) -> dict[str, BaseAgent]:
        """Return PPT product-level expert agents managed by this product manager."""
        return dict(self._product_expert_agents)

    def build_product_expert_agents(self) -> dict[str, BaseAgent]:
        """Build PPT product-level experts that routes and skills may call."""
        return {
            PPT_HTML_PAGE_GENERATION_EXPERT_NAME: self.build_html_page_generation_expert(),
            PPT_DESIGN_STRATEGY_EXPERT_NAME: self.build_design_strategy_expert(),
            PPT_SVG_DECK_EXECUTOR_EXPERT_NAME: self.build_svg_deck_executor_expert(),
        }

    def build_html_page_generation_expert(self) -> LlmAgent:
        """Build the PPT expert that generates editable HTML slide fragments."""
        return build_ppt_html_page_generation_expert()

    def build_design_strategy_expert(self) -> LlmAgent:
        """Build the PPT expert that prepares design strategy and SVG execution constraints."""
        return build_ppt_design_strategy_expert(
            save_design_strategy_tool=self.save_ppt_design_strategy,
            save_svg_execution_plan_tool=self.save_ppt_svg_execution_plan,
        )

    def build_svg_deck_executor_expert(self) -> LlmAgent:
        """Build the PPT expert that generates SVG pages from a content plan."""
        return build_ppt_svg_deck_executor_expert(
            read_svg_execution_plan_tool=self.read_ppt_svg_execution_plan,
            save_svg_page_tool=self.save_ppt_svg_page,
        )

    def _resolve_ppt_expert_agents(
        self,
        expert_agents: dict[str, BaseAgent] | None = None,
    ) -> dict[str, BaseAgent]:
        """Merge built-in PPT experts with externally supplied expert agents."""
        resolved = dict(self._product_expert_agents)
        resolved.update(expert_agents or {})
        return resolved

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
                "Route choice policy: user-specified route/system wins; otherwise, if source_inputs include PPTX/PPTM/POTX/POTM, set route `xml` for a template-based PPTX workflow; otherwise keep the conservative fallback route and let the separate PPT system-selection agent choose HTML or SVG from task fit.\n"
                "Do not infer routes from keyword matching. Natural-language words like template, svg, html, or xml are not enough unless the user is explicitly choosing the system or route.\n"
                "If the user says 受众为/受众设置为, write that value to audience. If the user says 场景为/场景设置为, write that value to scenario.\n"
                "For Chinese group meeting requests, scenario should be `组会`.\n"
                "When prepared source Markdown exists, call read_prepared_ppt_sources before saving the requirement. "
                "Use the prepared source text to infer the topic from the document title or abstract instead of guessing from task wording.\n"
                "Never keep obvious bogus topics such as `解`, `讲`, `讲解`, `这个论文`, or `论文` when prepared source text or a source filename can provide a better topic.\n"
                "Do not invent source file paths or generated artifacts."
            ),
            tools=[self.read_prepared_ppt_sources, self.save_ppt_confirmed_requirement_json],
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
                "Route choice policy: user-specified route/system wins; otherwise, if source_inputs include PPTX/PPTM/POTX/POTM, choose the private `pptx` skill when available, route `xml`, and output_format `pptx`; otherwise choose built-in `html` or `svg` from task fit.\n"
                "Private skills may produce a final single-file HTML presentation or an editable PPTX, depending on the skill.\n"
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

        base_payload = dict(
            tool_context.state.get(PPT_REQUIREMENT_ANALYSIS_BASE_KEY) or {}
        )
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
        if not _supports_agent_tool_context(tool_context):
            tool_context.state["ppt_requirement_analysis_output"] = {
                "status": "fallback",
                "message": "Requirement analysis agent skipped because no ADK AgentTool-compatible context was available.",
                "source": "deterministic_fallback",
            }
            return fallback_requirement

        # AgentTool inherits app and artifact context from the parent invocation.
        _ = app_name, artifact_service
        requirement_agent = self.build_requirement_analysis_agent()
        agent_state = {
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
            await _run_ppt_internal_agent_tool(
                agent=requirement_agent,
                request=_build_requirement_analysis_user_message(
                    mode=mode,
                    task=task,
                    raw_inputs=raw_inputs,
                    output=output,
                    fallback_requirement=fallback_requirement,
                    existing_requirement=existing_requirement,
                    user_revision=user_revision,
                ),
                tool_context=tool_context,
                initial_state={PPT_REQUIREMENT_ANALYSIS_BASE_KEY: agent_state},
            )
            final_state = _copy_state(tool_context.state)
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
        if not _supports_agent_tool_context(tool_context):
            return self._persist_system_selection(tool_context, fallback_selection)

        # AgentTool inherits app and artifact context from the parent invocation.
        _ = app_name, artifact_service
        selection_agent = self.build_system_selection_agent()
        agent_state = {
            "task": task,
            "output": output,
            "confirmed_requirement": requirement.model_dump(mode="json"),
            "registered_routes": self.list_registered_routes(),
            "fallback_selection": fallback_selection,
        }

        try:
            await _run_ppt_internal_agent_tool(
                agent=selection_agent,
                request=_build_system_selection_user_message(
                    task=task,
                    output=output,
                    requirement=requirement,
                    route_summaries=self.list_registered_routes(),
                ),
                tool_context=tool_context,
                initial_state={PPT_SYSTEM_SELECTION_BASE_KEY: agent_state},
            )
            final_state = _copy_state(tool_context.state)
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

    async def run_product_request(
        self,
        *,
        task: str,
        inputs: Any | None = None,
        output: dict[str, Any] | None = None,
        interaction_language: str = "",
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
            return _ppt_product_error_result("PptProductManager requires tool context.")

        try:
            request = PptProductRequest.model_validate(
                {
                    "task": task,
                    "inputs": inputs,
                    "output": output,
                    "interaction_language": interaction_language,
                }
            )
        except ValidationError:
            if not str(task or "").strip():
                return _ppt_product_error_result("PptProductManager requires a non-empty task.")
            return _ppt_product_error_result("PptProductManager requires output to be an object.")

        available_expert_agents = self._resolve_ppt_expert_agents(expert_agents)
        clean_task = request.task
        output_options = request.output
        if request.interaction_language:
            tool_context.state[INTERACTION_LANGUAGE_STATE_KEY] = request.interaction_language
        if self._has_adk_tool_confirmation_response(tool_context):
            return await self._continue_from_adk_tool_confirmation(
                output=output_options,
                tool_context=tool_context,
                expert_agents=available_expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
                source_converter=source_converter,
                content_plan_builder=content_plan_builder,
                asset_resolver=asset_resolver,
            )
        if not self._should_auto_confirm(output_options):
            workflow_state = self._get_workflow_state(tool_context.state)
            if self._is_pending_confirmation_stage(workflow_state.get("stage")):
                result = await self.continue_product_request(
                    user_response=clean_task,
                    tool_context=tool_context,
                    expert_agents=available_expert_agents,
                    app_name=app_name,
                    artifact_service=artifact_service,
                    source_converter=source_converter,
                    content_plan_builder=content_plan_builder,
                    asset_resolver=asset_resolver,
                )
                self._request_adk_tool_confirmation_if_needed(
                    result=result,
                    output=output_options,
                    tool_context=tool_context,
                )
                return result
            tool_context.state[PPT_PRODUCT_REQUEST_STATE_KEY] = request.to_state_dict()
            result = await self._start_interactive_product_request(
                task=clean_task,
                inputs=request.inputs,
                output=output_options,
                tool_context=tool_context,
                expert_agents=available_expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
                source_converter=source_converter,
                system_selection_builder=system_selection_builder,
            )
            self._request_adk_tool_confirmation_if_needed(
                result=result,
                output=output_options,
                tool_context=tool_context,
            )
            return result

        tool_context.state[PPT_PRODUCT_REQUEST_STATE_KEY] = request.to_state_dict()
        return await self._run_auto_confirm_product_request(
            task=clean_task,
            inputs=request.inputs,
            output=output_options,
            tool_context=tool_context,
            expert_agents=available_expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            source_converter=source_converter,
            content_plan_builder=content_plan_builder,
            asset_resolver=asset_resolver,
            system_selection_builder=system_selection_builder,
        )

    async def _run_auto_confirm_product_request(
        self,
        *,
        task: str,
        inputs: Any | None,
        output: dict[str, Any],
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        source_converter: Any | None = None,
        content_plan_builder: Any | None = None,
        asset_resolver: Any | None = None,
        system_selection_builder: Any | None = None,
    ) -> dict[str, Any]:
        """Run a one-shot auto-confirm PPT request directly or through ADK Workflow."""
        raw_inputs = self._normalize_raw_inputs(inputs)
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_auto_confirm_workflow(
                manager=self,
                task=task,
                raw_inputs=raw_inputs,
                output=output,
                tool_context=tool_context,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
                source_converter=source_converter,
                content_plan_builder=content_plan_builder,
                asset_resolver=asset_resolver,
                system_selection_builder=system_selection_builder,
            )
        return await self._run_auto_confirm_product_request_direct(
            task=task,
            raw_inputs=raw_inputs,
            output=output,
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            source_converter=source_converter,
            content_plan_builder=content_plan_builder,
            asset_resolver=asset_resolver,
            system_selection_builder=system_selection_builder,
        )

    async def _run_auto_confirm_product_request_direct(
        self,
        *,
        task: str,
        raw_inputs: list[Any],
        output: dict[str, Any],
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        source_converter: Any | None = None,
        content_plan_builder: Any | None = None,
        asset_resolver: Any | None = None,
        system_selection_builder: Any | None = None,
    ) -> dict[str, Any]:
        """Run the auto-confirm PPT flow without a parent Workflow wrapper."""
        try:
            result = await self._build_auto_confirm_product_result(
                task=task,
                raw_inputs=raw_inputs,
                output=output,
                tool_context=tool_context,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
                source_converter=source_converter,
                content_plan_builder=content_plan_builder,
                asset_resolver=asset_resolver,
                system_selection_builder=system_selection_builder,
            )
        except Exception as exc:
            result = self._build_auto_confirm_error_result(exc)
        return self._persist_product_result(tool_context, result)

    async def _build_auto_confirm_product_result(
        self,
        *,
        task: str,
        raw_inputs: list[Any],
        output: dict[str, Any],
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        source_converter: Any | None = None,
        content_plan_builder: Any | None = None,
        asset_resolver: Any | None = None,
        system_selection_builder: Any | None = None,
    ) -> PptProductResult:
        """Build the auto-confirm result by composing typed PPT phases."""
        source_preparation = await self._run_source_preparation_phase(
            raw_inputs=raw_inputs,
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            source_converter=source_converter,
        )
        requirement_phase = await self._prepare_initial_requirement_phase(
            task=task,
            raw_inputs=raw_inputs,
            output=output,
            source_understanding=source_preparation.source_materials,
            source_inputs=source_preparation.source_inputs,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        requirement = requirement_phase.confirmed_requirement
        clarification_questions = self.validate_confirmed_requirement(requirement)
        if clarification_questions:
            return PptProductResult(
                status="needs_clarification",
                phase="requirement_confirmation",
                message="PptProductManager needs a clearer PPT topic or source material before generation.",
                selected_route=requirement.route,
                confirmed_requirement=requirement,
                delivery_manifest=DeliveryManifest(),
                warnings=[],
                next_actions=clarification_questions,
            )

        system_selection_phase = await self._select_ppt_system_phase(
            task=task,
            output=output,
            requirement=requirement,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            system_selection_builder=system_selection_builder,
        )
        system_selection = system_selection_phase.system_selection.model_dump(mode="json")
        requirement = self._apply_system_selection_to_requirement(requirement, system_selection)
        tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = requirement.model_dump(mode="json")

        if self._is_private_skill_selection(system_selection):
            planning = await self._run_content_planning_phase(
                requirement,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
                expert_agents=expert_agents,
                content_plan_builder=content_plan_builder,
            )
            content_plan = planning.content_plan
            private_execution = await self._run_private_skill_execution_phase(
                requirement=requirement,
                content_plan=content_plan,
                system_selection=system_selection,
                tool_context=tool_context,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
            )
            private_delivery = await self._run_private_skill_delivery_phase(
                requirement=requirement,
                content_plan=content_plan,
                system_selection=system_selection,
                private_build=private_execution.private_build,
                tool_context=tool_context,
            )
            return private_delivery.product_result

        route_registration = self._route_registry.get(requirement.route)
        if route_registration is None or not route_registration.implemented:
            return self._build_route_not_implemented_result(requirement, route_registration)

        planning = await self._run_content_planning_phase(
            requirement,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
            content_plan_builder=content_plan_builder,
        )
        content_plan = planning.content_plan
        asset_resolution = await self._run_asset_resolution_phase(
            content_plan,
            requirement,
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            asset_resolver=asset_resolver,
        )
        resolved_plan = asset_resolution.content_plan
        route_execution = await self._run_route_execution_phase(
            requirement=requirement,
            content_plan=resolved_plan,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
        )
        delivery = await self._run_route_final_delivery_phase(
            requirement=requirement,
            content_plan=resolved_plan,
            route_execution=route_execution,
            tool_context=tool_context,
        )
        return delivery.product_result

    @staticmethod
    def _build_auto_confirm_error_result(exc: Exception) -> PptProductResult:
        """Build the public error result for a failed auto-confirm request."""
        return PptProductResult(
            status="error",
            phase="ppt_product_execution",
            message=f"PPT product request normalization failed: {type(exc).__name__}: {exc}",
            selected_route="html",
            warnings=[str(exc)],
            next_actions=["Fix the malformed PPT product request and retry."],
        )

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
        allow_same_turn_confirmation: bool = False,
    ) -> dict[str, Any]:
        """Continue a paused PPT product workflow after a user confirmation turn."""
        workflow_state = self._get_workflow_state(tool_context.state)
        stage = str(workflow_state.get("stage") or "").strip()
        clean_response = str(user_response or "").strip()
        available_expert_agents = self._resolve_ppt_expert_agents(expert_agents)
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

        if (
            not allow_same_turn_confirmation
            and self._is_pending_confirmation_stage(stage)
            and self._is_waiting_for_later_user_turn(
                workflow_state,
                tool_context.state,
            )
        ):
            result = self._build_current_confirmation_result(workflow_state)
            return self._persist_product_result(tool_context, result)

        workflow_state[PPT_WORKFLOW_LAST_CONSUMED_TURN_KEY] = self._current_turn_index(tool_context.state)

        if stage == PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION:
            return await self._continue_after_requirement_confirmation(
                user_response=clean_response,
                workflow_state=workflow_state,
                tool_context=tool_context,
                expert_agents=available_expert_agents,
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
                expert_agents=available_expert_agents,
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
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: BaseArtifactService | None,
        source_converter: Any | None = None,
        system_selection_builder: Any | None = None,
    ) -> dict[str, Any]:
        """Start a PPT workflow and stop at the requirement confirmation gate."""
        raw_inputs = self._normalize_raw_inputs(inputs)
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_initial_request_workflow(
                manager=self,
                task=task,
                raw_inputs=raw_inputs,
                output=output,
                tool_context=tool_context,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
                source_converter=source_converter,
                system_selection_builder=system_selection_builder,
            )
        return await self._start_interactive_product_request_direct(
            task=task,
            raw_inputs=raw_inputs,
            output=output,
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            source_converter=source_converter,
            system_selection_builder=system_selection_builder,
        )

    async def _start_interactive_product_request_direct(
        self,
        *,
        task: str,
        raw_inputs: list[Any],
        output: dict[str, Any],
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: BaseArtifactService | None,
        source_converter: Any | None = None,
        system_selection_builder: Any | None = None,
    ) -> dict[str, Any]:
        """Run the initial interactive request logic without a parent Workflow wrapper."""
        try:
            source_preparation: PptSourcePreparationResult | None = None
            source_understanding = SourceUnderstanding(
                document_type=self._infer_document_type(self._normalize_source_inputs(raw_inputs)),
            )
            source_inputs: list[SourceInput] | None = None
            if raw_inputs:
                source_preparation = await self._run_source_preparation_phase(
                    raw_inputs=raw_inputs,
                    tool_context=tool_context,
                    expert_agents=expert_agents,
                    app_name=app_name,
                    artifact_service=artifact_service,
                    source_converter=source_converter,
                )
                source_understanding = source_preparation.source_materials
                source_inputs = source_preparation.source_inputs

            requirement_phase = await self._prepare_initial_requirement_phase(
                task=task,
                raw_inputs=raw_inputs,
                output=output,
                source_understanding=source_understanding,
                source_inputs=source_inputs,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
            )
            requirement = requirement_phase.confirmed_requirement
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

            system_selection_phase = await self._select_ppt_system_phase(
                task=task,
                output=output,
                requirement=requirement,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
                system_selection_builder=system_selection_builder,
            )
            system_selection = system_selection_phase.system_selection.model_dump(mode="json")
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
            if source_preparation is not None:
                workflow_state["source_inputs"] = [
                    item.model_dump(mode="json") for item in source_preparation.source_inputs
                ]
                workflow_state["source_materials"] = source_preparation.source_materials.model_dump(mode="json")
            self._mark_confirmation_waiting(workflow_state, tool_context.state)
            result = self._build_requirement_confirmation_result(requirement, workflow_state)
            _persist_ppt_workflow_state(tool_context.state, workflow_state)
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
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_requirement_confirmation_workflow(
                manager=self,
                user_response=user_response,
                workflow_state=workflow_state,
                tool_context=tool_context,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
                source_converter=source_converter,
                content_plan_builder=content_plan_builder,
            )
        return await self._continue_after_requirement_confirmation_direct(
            user_response=user_response,
            workflow_state=workflow_state,
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            source_converter=source_converter,
            content_plan_builder=content_plan_builder,
        )

    async def _continue_after_requirement_confirmation_direct(
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
        """Run the first-confirmation business logic without a Workflow wrapper."""
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
            _persist_ppt_workflow_state(tool_context.state, workflow_state)
            return self._persist_product_result(tool_context, result)

        raw_inputs = list(workflow_state.get("raw_inputs") or [])
        source_preparation = await self._run_source_preparation_phase(
            raw_inputs=raw_inputs,
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            source_converter=source_converter,
        )
        requirement = requirement.model_copy(
            update={
                "source_inputs": source_preparation.source_inputs,
                "source_understanding": source_preparation.source_materials,
            }
        )
        tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = requirement.model_dump(mode="json")

        planning = await self._run_content_planning_phase(
            requirement,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
            content_plan_builder=content_plan_builder,
        )
        content_plan = planning.content_plan
        workflow_state.update(
            {
                "stage": PPT_STAGE_AWAITING_CONTENT_PLAN_CONFIRMATION,
                "confirmed_requirement": requirement.model_dump(mode="json"),
                "source_materials": source_preparation.source_materials.model_dump(mode="json"),
                "deck_content_plan": content_plan.model_dump(mode="json"),
                "deck_content_plan_markdown": planning.deck_content_plan_markdown,
                "system_selection": system_selection,
                "revision": int(workflow_state.get("revision", 1) or 1) + 1,
            }
        )
        self._mark_confirmation_waiting(workflow_state, tool_context.state)
        _persist_ppt_workflow_state(tool_context.state, workflow_state)
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
        revision = await self._revise_requirement_phase(
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
        requirement = revision.confirmed_requirement
        system_selection_phase = await self._select_ppt_system_phase(
            task=f"{base_task}\n{user_response}".strip(),
            output=output,
            requirement=requirement,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        system_selection = system_selection_phase.system_selection.model_dump(mode="json")
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
        _persist_ppt_workflow_state(tool_context.state, workflow_state)
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
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_content_plan_confirmation_workflow(
                manager=self,
                user_response=user_response,
                workflow_state=workflow_state,
                tool_context=tool_context,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
                content_plan_builder=content_plan_builder,
                asset_resolver=asset_resolver,
            )
        return await self._continue_after_content_plan_confirmation_direct(
            user_response=user_response,
            workflow_state=workflow_state,
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            content_plan_builder=content_plan_builder,
            asset_resolver=asset_resolver,
        )

    async def _continue_after_content_plan_confirmation_direct(
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
        """Run the second-confirmation business logic without a Workflow wrapper."""
        requirement = ConfirmedRequirement.model_validate(workflow_state.get("confirmed_requirement") or {})
        system_selection = self._get_workflow_system_selection(workflow_state, requirement, tool_context)
        requirement = self._apply_system_selection_to_requirement(requirement, system_selection)
        if not self._is_confirmation_text(user_response):
            revision = await self._revise_content_plan_phase(
                requirement=requirement,
                user_response=user_response,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
                expert_agents=expert_agents,
                content_plan_builder=content_plan_builder,
            )
            revised_requirement = revision.confirmed_requirement
            content_plan = revision.content_plan
            workflow_state.update(
                {
                    "stage": PPT_STAGE_AWAITING_CONTENT_PLAN_CONFIRMATION,
                    "confirmed_requirement": revised_requirement.model_dump(mode="json"),
                    "deck_content_plan": content_plan.model_dump(mode="json"),
                    "deck_content_plan_markdown": revision.deck_content_plan_markdown,
                    "system_selection": system_selection,
                    "revision": int(workflow_state.get("revision", 1) or 1) + 1,
                }
            )
            self._mark_confirmation_waiting(workflow_state, tool_context.state)
            tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = revised_requirement.model_dump(mode="json")
            _persist_ppt_workflow_state(tool_context.state, workflow_state)
            result = self._build_content_plan_confirmation_result(revised_requirement, content_plan, workflow_state)
            return self._persist_product_result(tool_context, result)

        content_plan = DeckContentPlan.model_validate(workflow_state.get("deck_content_plan") or {})
        if self._is_private_skill_selection(system_selection):
            private_execution = await self._run_private_skill_execution_phase(
                requirement=requirement,
                content_plan=content_plan,
                system_selection=system_selection,
                tool_context=tool_context,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
            )
            private_build = private_execution.private_build
            workflow_state.update(
                {
                    "stage": PPT_STAGE_COMPLETED,
                    "deck_content_plan": content_plan.model_dump(mode="json"),
                    "private_skill_build": private_build,
                    "system_selection": system_selection,
                }
            )
            _persist_ppt_workflow_state(tool_context.state, workflow_state)
            private_delivery = await self._run_private_skill_delivery_phase(
                requirement=requirement,
                content_plan=content_plan,
                system_selection=system_selection,
                private_build=private_build,
                tool_context=tool_context,
            )
            return self._persist_product_result(tool_context, private_delivery.product_result)

        asset_resolution = await self._run_asset_resolution_phase(
            content_plan,
            requirement,
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            asset_resolver=asset_resolver,
        )
        resolved_plan = asset_resolution.content_plan
        route_execution = await self._run_route_execution_phase(
            requirement=requirement,
            content_plan=resolved_plan,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
        )
        delivery = await self._run_route_final_delivery_phase(
            requirement=requirement,
            content_plan=resolved_plan,
            route_execution=route_execution,
            tool_context=tool_context,
            after_confirmation=True,
        )
        workflow_state.update(
            {
                "stage": PPT_STAGE_COMPLETED,
                "deck_content_plan": resolved_plan.model_dump(mode="json"),
                "route_build": route_execution.route_build.model_dump(mode="json"),
                "system_selection": system_selection,
            }
        )
        _persist_ppt_workflow_state(tool_context.state, workflow_state)
        return self._persist_product_result(tool_context, delivery.product_result)

    async def _continue_from_adk_tool_confirmation(
        self,
        *,
        output: dict[str, Any],
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        source_converter: Any | None,
        content_plan_builder: Any | None,
        asset_resolver: Any | None,
    ) -> dict[str, Any]:
        """Resume a PPT confirmation gate from ADK's structured tool confirmation payload."""
        workflow_state = self._get_workflow_state(tool_context.state)
        if not workflow_state or not self._is_pending_confirmation_stage(workflow_state.get("stage")):
            result = PptProductResult(
                status="error",
                phase="ppt_workflow_resume",
                message="没有找到等待确认的 PPT 工作流，请重新发起 PPT 任务。",
                selected_route="html",
                warnings=["ADK tool confirmation arrived without pending ppt_workflow_state."],
                next_actions=["重新发起 PPT 任务。"],
            )
            return self._persist_product_result(tool_context, result)

        tool_confirmation = getattr(tool_context, "tool_confirmation", None)
        if not getattr(tool_confirmation, "confirmed", False):
            current_result = self._build_current_confirmation_result(workflow_state)
            result = current_result.model_copy(
                update={
                    "warnings": [
                        *current_result.warnings,
                        "ADK tool confirmation was rejected.",
                    ],
                    "next_actions": ["确认当前方案，或用 revise/message 提交修改意见。"],
                }
            )
            persisted = self._persist_product_result(tool_context, result)
            self._request_adk_tool_confirmation_if_needed(
                result=persisted,
                output=output,
                tool_context=tool_context,
            )
            return persisted

        raw_payload = getattr(tool_confirmation, "payload", {}) or {}
        try:
            response = PptAdkConfirmationResponse.model_validate(raw_payload)
            self._validate_adk_confirmation_response(response, tool_context.state)
            tool_context.state[PPT_ADK_CONFIRMATION_REQUEST_STATE_KEY] = {}
        except ValidationError as exc:
            return self._persist_invalid_adk_confirmation_response(
                tool_context=tool_context,
                workflow_state=workflow_state,
                output=output,
                warning=f"Invalid ADK PPT confirmation response: {exc}",
            )
        except ValueError as exc:
            return self._persist_invalid_adk_confirmation_response(
                tool_context=tool_context,
                workflow_state=workflow_state,
                output=output,
                warning=str(exc),
            )

        result = await self.continue_product_request(
            user_response=response.to_user_response(),
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            source_converter=source_converter,
            content_plan_builder=content_plan_builder,
            asset_resolver=asset_resolver,
            allow_same_turn_confirmation=True,
        )
        self._request_adk_tool_confirmation_if_needed(
            result=result,
            output=output,
            tool_context=tool_context,
        )
        return result

    def _persist_invalid_adk_confirmation_response(
        self,
        *,
        tool_context: ToolContext,
        workflow_state: dict[str, Any],
        output: dict[str, Any],
        warning: str,
    ) -> dict[str, Any]:
        """Keep the current gate open when an ADK confirmation payload is malformed."""
        current_result = self._build_current_confirmation_result(workflow_state)
        result = current_result.model_copy(
            update={
                "warnings": [*current_result.warnings, warning],
                "next_actions": [
                    "提交 {\"action\":\"confirm\"}，或 {\"action\":\"revise\",\"message\":\"...\"}。"
                ],
            }
        )
        persisted = self._persist_product_result(tool_context, result)
        self._request_adk_tool_confirmation_if_needed(
            result=persisted,
            output=output,
            tool_context=tool_context,
        )
        return persisted

    @staticmethod
    def _has_adk_tool_confirmation_response(tool_context: ToolContext) -> bool:
        """Return whether ADK resumed this product tool from a confirmation response."""
        return getattr(tool_context, "tool_confirmation", None) is not None

    @staticmethod
    def _should_use_adk_tool_confirmation(output: dict[str, Any]) -> bool:
        """Return whether this PPT request opted into ADK-native tool confirmation."""
        explicit_value = (
            output.get("adk_hitl")
            if "adk_hitl" in output
            else output.get("adk_tool_confirmation")
        )
        if isinstance(explicit_value, bool):
            return explicit_value
        raw_mode = output.get("confirmation_mode") or output.get("hitl_mode") or explicit_value
        return str(raw_mode or "").strip().lower() in {
            "adk_hitl",
            "adk_tool_confirmation",
            "tool_confirmation",
        }

    def _request_adk_tool_confirmation_if_needed(
        self,
        *,
        result: dict[str, Any],
        output: dict[str, Any],
        tool_context: ToolContext,
    ) -> None:
        """Ask ADK to pause the current product tool at a PPT confirmation gate."""
        if not self._should_use_adk_tool_confirmation(output):
            return
        if self._has_adk_tool_confirmation_response(tool_context):
            return
        if not self._is_pending_confirmation_stage(result.get("status")):
            return
        request_confirmation = getattr(tool_context, "request_confirmation", None)
        if not callable(request_confirmation):
            return

        workflow_state = self._get_workflow_state(tool_context.state)
        try:
            payload_model = self._build_adk_confirmation_request(result, workflow_state)
        except ValidationError as exc:
            tool_context.state["ppt_adk_confirmation_error"] = f"Invalid confirmation request: {exc}"
            return

        payload = payload_model.model_dump(mode="json")
        tool_context.state[PPT_ADK_CONFIRMATION_REQUEST_STATE_KEY] = payload
        hint = "\n\n".join(
            part
            for part in (
                payload_model.message,
                payload_model.expected_user_action,
            )
            if part
        )
        try:
            request_confirmation(hint=hint, payload=payload)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            tool_context.state["ppt_adk_confirmation_error"] = f"{type(exc).__name__}: {exc}"
            return

        actions = getattr(tool_context, "actions", None)
        if actions is not None:
            actions.skip_summarization = True

    @staticmethod
    def _build_adk_confirmation_request(
        result: dict[str, Any],
        workflow_state: dict[str, Any],
    ) -> PptAdkConfirmationRequest:
        """Build the structured payload ADK exposes for a PPT confirmation request."""
        confirmation_request = result.get("confirmation_request")
        if not isinstance(confirmation_request, dict):
            confirmation_request = {}
        stage = str(workflow_state.get("stage") or result.get("status") or "").strip()
        confirmation_type = str(confirmation_request.get("type") or "").strip()
        if not confirmation_type:
            confirmation_type = (
                "requirement"
                if stage == PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION
                else "content_plan"
            )
        return PptAdkConfirmationRequest(
            workflow_id=str(workflow_state.get("workflow_id") or confirmation_request.get("workflow_id") or ""),
            confirmation_id=str(workflow_state.get("confirmation_id") or ""),
            stage=stage,
            confirmation_type=confirmation_type,
            message=str(result.get("message") or ""),
            summary_markdown=str(confirmation_request.get("summary_markdown") or ""),
            expected_user_action=str(confirmation_request.get("expected_user_action") or ""),
        )

    @staticmethod
    def _validate_adk_confirmation_response(
        response: PptAdkConfirmationResponse,
        state: dict[str, Any],
    ) -> None:
        """Validate optional response correlation metadata against the pending request."""
        pending_request = state.get(PPT_ADK_CONFIRMATION_REQUEST_STATE_KEY)
        if not isinstance(pending_request, dict):
            return
        expected_confirmation_id = str(pending_request.get("confirmation_id") or "").strip()
        if (
            response.confirmation_id
            and expected_confirmation_id
            and response.confirmation_id != expected_confirmation_id
        ):
            raise ValueError("ADK PPT confirmation response does not match the pending confirmation_id.")
        expected_stage = str(pending_request.get("stage") or "").strip()
        if response.stage and expected_stage and response.stage != expected_stage:
            raise ValueError("ADK PPT confirmation response does not match the pending stage.")

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
        return _coerce_ppt_workflow_state_payload(state.get(PPT_WORKFLOW_STATE_KEY))

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

        explicit_route = self._select_explicit_route(output)
        if explicit_route is not None:
            route, route_confirmed = explicit_route
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
        rejected_topic_from_payload = False
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
                    if cls._is_invalid_public_topic(clean_value):
                        rejected_topic_from_payload = True
                        continue
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

        source_topic = cls._fallback_topic_from_source_inputs(fallback_requirement.source_inputs)
        if rejected_topic_from_payload and source_topic:
            merged["topic"] = source_topic
        elif (
            not str(merged.get("topic") or "").strip()
            or cls._is_invalid_public_topic(str(merged.get("topic") or ""))
        ):
            merged["topic"] = source_topic or fallback_requirement.topic or "论文组会汇报"
        if cls._is_invalid_public_topic(str(merged.get("topic") or "")):
            merged["topic"] = "论文组会汇报"

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
            "### 系统选择",
            "",
            "| 项目 | 当前值 |",
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

    async def _prepare_initial_requirement_phase(
        self,
        *,
        task: str,
        raw_inputs: list[Any],
        output: dict[str, Any],
        source_understanding: SourceUnderstanding,
        source_inputs: list[SourceInput] | None,
        tool_context: ToolContext,
        app_name: str,
        artifact_service: BaseArtifactService | None,
    ) -> PptRequirementAnalysisResult:
        """Run initial requirement analysis directly or as an ADK Workflow node."""
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_requirement_analysis_phase_workflow(
                manager=self,
                task=task,
                raw_inputs=raw_inputs,
                output=output,
                source_understanding=source_understanding,
                source_inputs=source_inputs,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
            )
        return await self._prepare_initial_requirement_phase_direct(
            task=task,
            raw_inputs=raw_inputs,
            output=output,
            source_understanding=source_understanding,
            source_inputs=source_inputs,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
        )

    async def _prepare_initial_requirement_phase_direct(
        self,
        *,
        task: str,
        raw_inputs: list[Any],
        output: dict[str, Any],
        source_understanding: SourceUnderstanding,
        source_inputs: list[SourceInput] | None,
        tool_context: ToolContext,
        app_name: str,
        artifact_service: BaseArtifactService | None,
    ) -> PptRequirementAnalysisResult:
        """Prepare and persist the initial PPT requirement as a typed phase result."""
        requirement = await self.prepare_confirmed_requirement_with_agent(
            task=task,
            inputs=raw_inputs,
            output=output,
            source_understanding=source_understanding,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        if source_inputs is not None:
            requirement = requirement.model_copy(update={"source_inputs": source_inputs})
            tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = requirement.model_dump(mode="json")
        result = PptRequirementAnalysisResult(
            confirmed_requirement=requirement,
            analysis_output=self._requirement_analysis_output_state(tool_context.state),
            agent_message=str(tool_context.state.get(PPT_REQUIREMENT_ANALYSIS_AGENT_MESSAGE_KEY) or ""),
        )
        self._persist_requirement_analysis_result(tool_context, result)
        return result

    @staticmethod
    def _persist_requirement_analysis_result(
        tool_context: ToolContext,
        result: PptRequirementAnalysisResult,
    ) -> None:
        """Persist the stable initial requirement-analysis phase result."""
        payload = result.model_dump(mode="json")
        tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = payload["confirmed_requirement"]
        tool_context.state[PPT_REQUIREMENT_ANALYSIS_OUTPUT_STATE_KEY] = payload["analysis_output"]
        tool_context.state[PPT_REQUIREMENT_ANALYSIS_RESULT_STATE_KEY] = payload

    async def _revise_requirement_phase(
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
    ) -> PptRequirementRevisionResult:
        """Run requirement revision directly or as an ADK Workflow node."""
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_requirement_revision_phase_workflow(
                manager=self,
                existing_requirement=existing_requirement,
                user_response=user_response,
                task=task,
                raw_inputs=raw_inputs,
                output=output,
                source_understanding=source_understanding,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
            )
        return await self._revise_requirement_phase_direct(
            existing_requirement=existing_requirement,
            user_response=user_response,
            task=task,
            raw_inputs=raw_inputs,
            output=output,
            source_understanding=source_understanding,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
        )

    async def _revise_requirement_phase_direct(
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
    ) -> PptRequirementRevisionResult:
        """Revise the confirmed PPT requirement and persist a typed phase result."""
        revised_requirement = await self.revise_confirmed_requirement_with_agent(
            existing_requirement=existing_requirement,
            user_response=user_response,
            task=task,
            raw_inputs=raw_inputs,
            output=output,
            source_understanding=source_understanding,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        result = PptRequirementRevisionResult(
            confirmed_requirement=revised_requirement,
            revision_output=self._requirement_analysis_output_state(tool_context.state),
            agent_message=str(tool_context.state.get(PPT_REQUIREMENT_ANALYSIS_AGENT_MESSAGE_KEY) or ""),
            user_revision=user_response,
        )
        self._persist_requirement_revision_result(tool_context, result)
        return result

    @staticmethod
    def _requirement_analysis_output_state(state: Any) -> dict[str, Any]:
        """Return the requirement-analysis output currently stored in state."""
        try:
            output = state.get(PPT_REQUIREMENT_ANALYSIS_OUTPUT_STATE_KEY)
        except Exception:
            output = None
        return copy.deepcopy(output) if isinstance(output, dict) else {}

    @staticmethod
    def _persist_requirement_revision_result(
        tool_context: ToolContext,
        result: PptRequirementRevisionResult,
    ) -> None:
        """Persist the stable requirement-revision phase result."""
        payload = result.model_dump(mode="json")
        tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = payload["confirmed_requirement"]
        tool_context.state[PPT_REQUIREMENT_REVISION_OUTPUT_STATE_KEY] = payload["revision_output"]
        tool_context.state[PPT_REQUIREMENT_REVISION_RESULT_STATE_KEY] = payload

    async def _select_ppt_system_phase(
        self,
        *,
        task: str,
        output: dict[str, Any],
        requirement: ConfirmedRequirement,
        tool_context: ToolContext,
        app_name: str,
        artifact_service: BaseArtifactService | None,
        system_selection_builder: Any | None = None,
    ) -> PptSystemSelectionResult:
        """Run system selection directly or as an ADK Workflow node."""
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_system_selection_phase_workflow(
                manager=self,
                task=task,
                output=output,
                requirement=requirement,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
                system_selection_builder=system_selection_builder,
            )
        return await self._select_ppt_system_phase_direct(
            task=task,
            output=output,
            requirement=requirement,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            system_selection_builder=system_selection_builder,
        )

    async def _select_ppt_system_phase_direct(
        self,
        *,
        task: str,
        output: dict[str, Any],
        requirement: ConfirmedRequirement,
        tool_context: ToolContext,
        app_name: str,
        artifact_service: BaseArtifactService | None,
        system_selection_builder: Any | None = None,
    ) -> PptSystemSelectionResult:
        """Choose the PPT delivery system and persist a typed phase result."""
        agent_tool_supported = _supports_agent_tool_context(tool_context)
        selection = await self.select_ppt_system_with_agent(
            task=task,
            output=output,
            requirement=requirement,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            system_selection_builder=system_selection_builder,
        )
        typed_selection = PptSystemSelection.model_validate(selection)
        result = PptSystemSelectionResult(
            system_selection=typed_selection,
            selection_output=self._build_system_selection_phase_output(
                typed_selection,
                tool_context=tool_context,
                agent_tool_supported=agent_tool_supported,
                injected_builder=system_selection_builder is not None,
            ),
            agent_message=str(tool_context.state.get(PPT_SYSTEM_SELECTION_AGENT_MESSAGE_KEY) or ""),
        )
        self._persist_system_selection_result(tool_context, result)
        return result

    def _build_system_selection_phase_output(
        self,
        selection: PptSystemSelection,
        *,
        tool_context: ToolContext,
        agent_tool_supported: bool,
        injected_builder: bool,
    ) -> dict[str, Any]:
        """Build compact diagnostics for the system-selection phase."""
        reason = selection.reason
        if injected_builder:
            source = "injected"
        elif "System selection agent fallback:" in reason:
            source = "deterministic_fallback"
        elif agent_tool_supported and tool_context.state.get(PPT_SYSTEM_SELECTION_AGENT_MESSAGE_KEY):
            source = "llm_agent"
        elif agent_tool_supported:
            source = "llm_agent"
        else:
            source = "deterministic_fallback"
        return {
            "status": "success",
            "message": "PPT system selection completed.",
            "source": source,
            "system_type": selection.system_type,
            "route": selection.route,
            "skill_name": selection.skill_name,
            "output_format": selection.output_format,
        }

    @staticmethod
    def _persist_system_selection_result(
        tool_context: ToolContext,
        result: PptSystemSelectionResult,
    ) -> None:
        """Persist the stable system-selection phase result."""
        payload = result.model_dump(mode="json")
        tool_context.state[PPT_SYSTEM_SELECTION_STATE_KEY] = payload["system_selection"]
        tool_context.state[PPT_SYSTEM_SELECTION_OUTPUT_STATE_KEY] = payload["selection_output"]
        tool_context.state[PPT_SYSTEM_SELECTION_RESULT_STATE_KEY] = payload

    async def _run_source_preparation_phase(
        self,
        *,
        raw_inputs: list[Any],
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        source_converter: Any | None,
    ) -> PptSourcePreparationResult:
        """Run source preparation directly or as an ADK Workflow node."""
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_source_preparation_phase_workflow(
                manager=self,
                raw_inputs=raw_inputs,
                tool_context=tool_context,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
                source_converter=source_converter,
            )
        return await self._prepare_source_materials_phase(
            raw_inputs=raw_inputs,
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            source_converter=source_converter,
        )

    async def _run_content_planning_phase(
        self,
        requirement: ConfirmedRequirement,
        *,
        tool_context: ToolContext,
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        expert_agents: dict[str, BaseAgent] | None = None,
        content_plan_builder: Any | None = None,
    ) -> PptContentPlanningResult:
        """Run content planning directly or as an ADK Workflow node."""
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_content_planning_phase_workflow(
                manager=self,
                requirement=requirement,
                tool_context=tool_context,
                expert_agents=expert_agents or {},
                app_name=app_name,
                artifact_service=artifact_service,
                content_plan_builder=content_plan_builder,
            )
        return await self._build_deck_content_plan_phase(
            requirement,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
            content_plan_builder=content_plan_builder,
        )

    async def _revise_content_plan_phase(
        self,
        *,
        requirement: ConfirmedRequirement,
        user_response: str,
        tool_context: ToolContext,
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        expert_agents: dict[str, BaseAgent] | None = None,
        content_plan_builder: Any | None = None,
    ) -> PptContentPlanRevisionResult:
        """Run content-plan revision directly or as an ADK Workflow node."""
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_content_plan_revision_phase_workflow(
                manager=self,
                requirement=requirement,
                user_response=user_response,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
                expert_agents=expert_agents or {},
                content_plan_builder=content_plan_builder,
            )
        return await self._revise_content_plan_phase_direct(
            requirement=requirement,
            user_response=user_response,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
            content_plan_builder=content_plan_builder,
        )

    async def _revise_content_plan_phase_direct(
        self,
        *,
        requirement: ConfirmedRequirement,
        user_response: str,
        tool_context: ToolContext,
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        expert_agents: dict[str, BaseAgent] | None = None,
        content_plan_builder: Any | None = None,
    ) -> PptContentPlanRevisionResult:
        """Revise the PPT content plan by regenerating planning from an updated requirement."""
        revised_requirement = requirement.model_copy(
            update={
                "request_brief": self._append_user_revision(
                    requirement.request_brief,
                    user_response,
                    label="Content plan revision",
                )
            }
        )
        planning = await self._run_content_planning_phase(
            revised_requirement,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
            content_plan_builder=content_plan_builder,
        )
        result = PptContentPlanRevisionResult(
            confirmed_requirement=revised_requirement,
            content_plan=planning.content_plan,
            deck_content_plan_markdown=planning.deck_content_plan_markdown,
            revision_output=self._build_content_plan_revision_output(planning),
            user_revision=user_response,
        )
        self._persist_content_plan_revision_result(tool_context, result)
        return result

    @staticmethod
    def _build_content_plan_revision_output(
        planning: PptContentPlanningResult,
    ) -> dict[str, Any]:
        """Build compact diagnostics for the content-plan revision phase."""
        output = copy.deepcopy(planning.planning_output)
        return {
            "status": str(output.get("status") or "success"),
            "message": "PPT content-plan revision completed.",
            "source": str(output.get("source") or ""),
            "page_count": len(planning.content_plan.pages),
            "planning_output": output,
        }

    @staticmethod
    def _persist_content_plan_revision_result(
        tool_context: ToolContext,
        result: PptContentPlanRevisionResult,
    ) -> None:
        """Persist the stable content-plan revision phase result."""
        payload = result.model_dump(mode="json")
        tool_context.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = payload["confirmed_requirement"]
        tool_context.state["ppt_deck_content_plan"] = payload["content_plan"]
        if payload.get("deck_content_plan_markdown") or "ppt_deck_content_plan_markdown" in tool_context.state:
            tool_context.state["ppt_deck_content_plan_markdown"] = payload["deck_content_plan_markdown"]
        tool_context.state[PPT_CONTENT_PLAN_REVISION_OUTPUT_STATE_KEY] = payload["revision_output"]
        tool_context.state[PPT_CONTENT_PLAN_REVISION_RESULT_STATE_KEY] = payload

    async def _run_asset_resolution_phase(
        self,
        content_plan: DeckContentPlan,
        requirement: ConfirmedRequirement,
        *,
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent] | None = None,
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        asset_resolver: Any | None = None,
    ) -> PptAssetResolutionResult:
        """Run asset resolution directly or as an ADK Workflow node."""
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_asset_resolution_phase_workflow(
                manager=self,
                content_plan=content_plan,
                requirement=requirement,
                tool_context=tool_context,
                expert_agents=expert_agents or {},
                app_name=app_name,
                artifact_service=artifact_service,
                asset_resolver=asset_resolver,
            )
        return await self._resolve_deck_assets_phase(
            content_plan,
            requirement,
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            asset_resolver=asset_resolver,
        )

    async def _run_route_execution_phase(
        self,
        *,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        tool_context: ToolContext,
        app_name: str = "creative_claw",
        artifact_service: InMemoryArtifactService | None = None,
        expert_agents: dict[str, BaseAgent] | None = None,
    ) -> PptRouteExecutionResult:
        """Run built-in route execution directly or as an ADK Workflow node."""
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_route_execution_phase_workflow(
                manager=self,
                requirement=requirement,
                content_plan=content_plan,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
                expert_agents=expert_agents or {},
            )
        return await self._execute_ppt_route_phase(
            requirement=requirement,
            content_plan=content_plan,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
        )

    async def _run_route_final_delivery_phase(
        self,
        *,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        route_execution: PptRouteExecutionResult,
        tool_context: ToolContext,
        after_confirmation: bool = False,
    ) -> PptFinalDeliveryResult:
        """Run route final delivery directly or as an ADK Workflow node."""
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_final_delivery_phase_workflow(
                manager=self,
                requirement=requirement,
                content_plan=content_plan,
                route_execution=route_execution,
                tool_context=tool_context,
                after_confirmation=after_confirmation,
            )
        return self._finalize_route_delivery_phase(
            requirement=requirement,
            content_plan=content_plan,
            route_execution=route_execution,
            tool_context=tool_context,
            after_confirmation=after_confirmation,
        )

    async def _run_private_skill_delivery_phase(
        self,
        *,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        system_selection: dict[str, Any],
        private_build: dict[str, Any],
        tool_context: ToolContext,
    ) -> PptPrivateSkillDeliveryResult:
        """Run private-skill delivery directly or as an ADK Workflow node."""
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_private_skill_delivery_phase_workflow(
                manager=self,
                requirement=requirement,
                content_plan=content_plan,
                system_selection=system_selection,
                private_build=private_build,
                tool_context=tool_context,
            )
        return self._finalize_private_skill_delivery_phase(
            requirement=requirement,
            content_plan=content_plan,
            system_selection=system_selection,
            private_build=private_build,
            tool_context=tool_context,
        )

    async def _run_private_skill_execution_phase(
        self,
        *,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        system_selection: dict[str, Any],
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: BaseArtifactService | None,
    ) -> PptPrivateSkillExecutionResult:
        """Run private-skill execution directly or as an ADK Workflow node."""
        if _supports_dynamic_workflow(tool_context):
            return await _run_ppt_private_skill_execution_phase_workflow(
                manager=self,
                requirement=requirement,
                content_plan=content_plan,
                system_selection=system_selection,
                tool_context=tool_context,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
            )
        return await self._execute_private_ppt_skill_phase(
            requirement=requirement,
            content_plan=content_plan,
            system_selection=system_selection,
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
        )

    async def _execute_private_ppt_skill_phase(
        self,
        *,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        system_selection: dict[str, Any],
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: BaseArtifactService | None,
    ) -> PptPrivateSkillExecutionResult:
        """Execute the selected private PPT skill and persist its typed phase result."""
        input_signature = self._private_skill_execution_input_signature(
            requirement=requirement,
            content_plan=content_plan,
            system_selection=system_selection,
        )
        reusable = self._load_reusable_private_skill_execution_result(
            tool_context.state,
            system_selection=system_selection,
            input_signature=input_signature,
        )
        if reusable is not None:
            self._restore_private_skill_execution_result_state(tool_context, reusable)
            self._persist_private_skill_execution_result(tool_context, reusable)
            return reusable

        private_build = await self.execute_private_ppt_skill(
            requirement=requirement,
            content_plan=content_plan,
            system_selection=system_selection,
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        result = PptPrivateSkillExecutionResult(
            skill_name=system_selection.get("skill_name"),
            output_format=system_selection.get("output_format") or requirement.output_format,
            input_signature=input_signature,
            private_build=copy.deepcopy(private_build),
            execution_output=self._private_skill_execution_output_state(tool_context.state),
            reused_existing_build=False,
        )
        self._persist_private_skill_execution_result(tool_context, result)
        return result

    def _private_skill_execution_input_signature(
        self,
        *,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        system_selection: dict[str, Any],
    ) -> str:
        """Return a stable signature for private-skill execution inputs."""
        selection = self._normalize_system_selection(
            system_selection,
            fallback_selection=self._build_default_system_selection(requirement),
            strict=False,
        )
        skill_name = str(selection.get("skill_name") or "").strip()
        try:
            skill_content = self.skill_registry.read_skill(skill_name)
        except Exception:
            skill_content = ""
        payload = {
            "requirement": requirement.model_dump(mode="json"),
            "content_plan": content_plan.model_dump(mode="json"),
            "system_selection": selection,
            "skill_content_sha256": hashlib.sha256(skill_content.encode("utf-8")).hexdigest(),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _load_reusable_private_skill_execution_result(
        self,
        state: dict[str, Any],
        *,
        system_selection: dict[str, Any],
        input_signature: str,
    ) -> PptPrivateSkillExecutionResult | None:
        """Return a reusable same-input private-skill execution result if its artifact still exists."""
        payload = state.get(PPT_PRIVATE_SKILL_EXECUTION_RESULT_STATE_KEY)
        if not isinstance(payload, dict):
            return None
        try:
            result = PptPrivateSkillExecutionResult.model_validate(payload)
        except Exception:
            return None
        if not result.input_signature or result.input_signature != input_signature:
            return None
        skill_name = str(system_selection.get("skill_name") or "").strip()
        if result.skill_name != skill_name:
            return None
        if not self._private_skill_execution_has_reusable_output(result.private_build):
            return None
        execution_output = copy.deepcopy(result.execution_output)
        execution_output["reused_existing_build"] = True
        return result.model_copy(
            update={
                "execution_output": execution_output,
                "reused_existing_build": True,
            }
        )

    @staticmethod
    def _private_skill_execution_has_reusable_output(private_build: dict[str, Any]) -> bool:
        """Return whether a private-skill build still has a reusable output artifact."""
        output_path = str(private_build.get("output_path") or "").strip()
        if not output_path:
            return False
        try:
            return resolve_workspace_path(output_path).is_file()
        except Exception:
            return False

    @staticmethod
    def _restore_private_skill_execution_result_state(
        tool_context: ToolContext,
        result: PptPrivateSkillExecutionResult,
    ) -> None:
        """Restore state needed by private-skill delivery when reusing execution output."""
        private_build = copy.deepcopy(result.private_build)
        output_files = list(private_build.get("output_files") or [])
        output_path = str(private_build.get("output_path") or "").strip()
        tool_context.state[PPT_PRIVATE_SKILL_BUILD_STATE_KEY] = private_build
        tool_context.state["ppt_private_skill_execution_output"] = copy.deepcopy(result.execution_output)
        if output_files:
            tool_context.state["new_files"] = copy.deepcopy(output_files)
            if not tool_context.state.get("generated"):
                tool_context.state["generated"] = copy.deepcopy(output_files)
            if not tool_context.state.get("files_history"):
                tool_context.state["files_history"] = [copy.deepcopy(output_files)]
        if output_path:
            tool_context.state["final_file_paths"] = [output_path]

    @staticmethod
    def _private_skill_execution_output_state(state: Any) -> dict[str, Any]:
        """Return the private-skill execution output currently stored in state."""
        try:
            output = state.get("ppt_private_skill_execution_output")
        except Exception:
            output = None
        return copy.deepcopy(output) if isinstance(output, dict) else {}

    @staticmethod
    def _persist_private_skill_execution_result(
        tool_context: ToolContext,
        result: PptPrivateSkillExecutionResult,
    ) -> None:
        """Persist the stable private-skill execution phase result."""
        tool_context.state[PPT_PRIVATE_SKILL_EXECUTION_RESULT_STATE_KEY] = result.model_dump(mode="json")

    async def _build_deck_content_plan_phase(
        self,
        requirement: ConfirmedRequirement,
        *,
        tool_context: ToolContext,
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        expert_agents: dict[str, BaseAgent] | None = None,
        content_plan_builder: Any | None = None,
    ) -> PptContentPlanningResult:
        """Build and persist a PPT deck content plan without resolving assets."""
        content_plan = await self.build_deck_content_plan(
            requirement,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
            content_plan_builder=content_plan_builder,
            resolve_assets=False,
        )
        planning_output = tool_context.state.get("ppt_content_planning_output")
        result = PptContentPlanningResult(
            content_plan=content_plan,
            deck_content_plan_markdown=str(tool_context.state.get("ppt_deck_content_plan_markdown") or ""),
            planning_output=dict(planning_output) if isinstance(planning_output, dict) else {},
        )
        self._persist_content_planning_result(tool_context, result)
        return result

    @staticmethod
    def _persist_content_planning_result(
        tool_context: ToolContext,
        result: PptContentPlanningResult,
    ) -> None:
        """Persist the stable content-planning state produced by the phase."""
        tool_context.state["ppt_deck_content_plan"] = result.content_plan.model_dump(mode="json")
        if result.deck_content_plan_markdown or "ppt_deck_content_plan_markdown" in tool_context.state:
            tool_context.state["ppt_deck_content_plan_markdown"] = result.deck_content_plan_markdown
        if result.planning_output or "ppt_content_planning_output" in tool_context.state:
            tool_context.state["ppt_content_planning_output"] = result.planning_output

    async def _resolve_deck_assets_phase(
        self,
        content_plan: DeckContentPlan,
        requirement: ConfirmedRequirement,
        *,
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent] | None = None,
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        asset_resolver: Any | None = None,
    ) -> PptAssetResolutionResult:
        """Resolve planned PPT assets and persist the resulting manifest."""
        input_signature = self._asset_resolution_input_signature(requirement, content_plan)
        reusable = self._load_reusable_asset_resolution_result(
            tool_context.state,
            input_signature=input_signature,
        )
        if reusable is not None:
            self._persist_asset_resolution_result(tool_context, reusable)
            return reusable

        resolved_plan = await self.content_planner.resolve_plan_assets(
            content_plan,
            requirement,
            tool_context=tool_context,
            expert_agents=expert_agents or {},
            app_name=app_name,
            artifact_service=artifact_service,
            asset_resolver=asset_resolver,
        )
        manifest = tool_context.state.get("ppt_resolved_asset_manifest")
        warnings = tool_context.state.get("ppt_content_planning_warnings")
        result = PptAssetResolutionResult(
            content_plan=resolved_plan,
            input_signature=input_signature,
            resolved_asset_manifest=dict(manifest) if isinstance(manifest, dict) else {},
            planning_warnings=list(warnings) if isinstance(warnings, list) else [],
            reused_existing_resolution=False,
        )
        self._persist_asset_resolution_result(tool_context, result)
        return result

    def _load_reusable_asset_resolution_result(
        self,
        state: dict[str, Any],
        *,
        input_signature: str,
    ) -> PptAssetResolutionResult | None:
        """Return a prior asset-resolution result when its ready files still exist."""
        payload = state.get(PPT_ASSET_RESOLUTION_RESULT_STATE_KEY)
        if not isinstance(payload, dict):
            return None
        try:
            result = PptAssetResolutionResult.model_validate(payload)
        except Exception:
            return None
        if not result.input_signature or result.input_signature != input_signature:
            return None
        if not self._asset_resolution_has_reusable_outputs(result.content_plan):
            return None
        return result.model_copy(update={"reused_existing_resolution": True})

    @staticmethod
    def _asset_resolution_input_signature(
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
    ) -> str:
        """Return a stable signature for inputs that affect asset resolution."""
        payload = {
            "requirement": requirement.model_dump(mode="json"),
            "content_plan": content_plan.model_dump(mode="json"),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _asset_resolution_has_reusable_outputs(content_plan: DeckContentPlan) -> bool:
        """Return whether all ready asset file references in a plan still exist."""
        for page in content_plan.pages:
            for asset in page.assets:
                asset = DeckPageAsset.model_validate(asset)
                if asset.status != "ready" or not str(asset.path or "").strip():
                    return False
                try:
                    if not resolve_workspace_path(asset.path).is_file():
                        return False
                except Exception:
                    return False
        return True

    @staticmethod
    def _persist_asset_resolution_result(
        tool_context: ToolContext,
        result: PptAssetResolutionResult,
    ) -> None:
        """Persist the stable asset-resolution state produced by the phase."""
        tool_context.state["ppt_deck_content_plan"] = result.content_plan.model_dump(mode="json")
        tool_context.state["ppt_resolved_asset_manifest"] = result.resolved_asset_manifest
        if result.planning_warnings or "ppt_content_planning_warnings" in tool_context.state:
            tool_context.state["ppt_content_planning_warnings"] = result.planning_warnings
        tool_context.state[PPT_ASSET_RESOLUTION_RESULT_STATE_KEY] = result.model_dump(mode="json")

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

        asset_resolution = await self._resolve_deck_assets_phase(
            plan,
            requirement,
            tool_context=tool_context,
            expert_agents=expert_agents or {},
            app_name=app_name,
            artifact_service=artifact_service,
            asset_resolver=asset_resolver,
        )
        return asset_resolution.content_plan

    async def _execute_ppt_route_phase(
        self,
        *,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        tool_context: ToolContext,
        app_name: str = "creative_claw",
        artifact_service: InMemoryArtifactService | None = None,
        expert_agents: dict[str, BaseAgent] | None = None,
    ) -> PptRouteExecutionResult:
        """Execute one built-in PPT route with conservative existing-build reuse."""
        output_dir = self._build_route_output_dir(tool_context.state, route=requirement.route)
        output_dir_ref = self._route_output_dir_reference(output_dir)
        input_signature = self._route_execution_input_signature(requirement, content_plan)
        reusable = self._load_reusable_route_execution_result(
            tool_context.state,
            route=requirement.route,
            output_dir_ref=output_dir_ref,
            input_signature=input_signature,
        )
        if reusable is not None:
            self._persist_route_execution_result(tool_context, reusable)
            return reusable

        route_build = await self._dispatch_ppt_route(
            requirement=requirement,
            content_plan=content_plan,
            output_dir=output_dir,
            tool_context=tool_context,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
        )
        result = PptRouteExecutionResult(
            route=requirement.route,
            output_dir=output_dir_ref,
            input_signature=input_signature,
            route_build=route_build,
            reused_existing_build=False,
        )
        self._persist_route_execution_result(tool_context, result)
        return result

    def _load_reusable_route_execution_result(
        self,
        state: dict[str, Any],
        *,
        route: str,
        output_dir_ref: str,
        input_signature: str,
    ) -> PptRouteExecutionResult | None:
        """Return a same-phase route result only when its final PPTX still exists."""
        payload = state.get(PPT_ROUTE_EXECUTION_RESULT_STATE_KEY)
        if not isinstance(payload, dict):
            return None
        try:
            result = PptRouteExecutionResult.model_validate(payload)
        except Exception:
            return None
        if result.route != str(route or "").strip().lower():
            return None
        if result.output_dir != output_dir_ref:
            return None
        if result.input_signature != input_signature:
            return None
        if not self._route_execution_has_reusable_output(result.route_build):
            return None
        return result.model_copy(update={"reused_existing_build": True})

    @staticmethod
    def _route_execution_input_signature(
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
    ) -> str:
        """Return a stable signature for route inputs that affect generated files."""
        payload = {
            "requirement": requirement.model_dump(mode="json"),
            "content_plan": content_plan.model_dump(mode="json"),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _route_execution_has_reusable_output(route_build: Any) -> bool:
        """Return whether a route build has a reusable final PPTX file."""
        pptx_path = str(getattr(route_build, "pptx_path", "") or "").strip()
        if not pptx_path:
            return False
        try:
            return resolve_workspace_path(pptx_path).is_file()
        except Exception:
            return False

    @staticmethod
    def _route_output_dir_reference(output_dir: Path) -> str:
        """Return the stable state reference for a route output directory."""
        try:
            return workspace_relative_path(output_dir)
        except Exception:
            return str(output_dir)

    @staticmethod
    def _persist_route_execution_result(
        tool_context: ToolContext,
        result: PptRouteExecutionResult,
    ) -> None:
        """Persist route execution state without registering final output files."""
        payload = result.model_dump(mode="json")
        tool_context.state[PPT_ROUTE_EXECUTION_RESULT_STATE_KEY] = payload
        tool_context.state[PPT_ROUTE_OUTPUT_DIR_STATE_KEY] = result.output_dir
        tool_context.state["ppt_route_build"] = payload["route_build"]

    def _finalize_route_delivery_phase(
        self,
        *,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        route_execution: PptRouteExecutionResult,
        tool_context: ToolContext,
        after_confirmation: bool = False,
    ) -> PptFinalDeliveryResult:
        """Register route artifacts and build the final PPT product result."""
        route_build = route_execution.route_build
        route_succeeded = bool(route_build.pptx_path)
        output_files = self._record_output_files(
            tool_context.state,
            self._route_build_output_paths(route_build),
        )
        delivery_manifest = DeliveryManifest(
            final_pptx=route_build.pptx_path,
            previews=route_build.preview_paths,
            quality_report=route_build.quality_report_path,
            build_log=route_build.build_log_path,
            intermediate_artifacts=self._route_build_intermediate_artifacts(route_build),
            output_files=output_files,
        )
        product_result = PptProductResult(
            status="success" if route_succeeded else "generation_failed",
            phase=f"{requirement.route}_route_delivery",
            message=self._build_route_delivery_message(
                requirement.route,
                route_succeeded,
                after_confirmation=after_confirmation,
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
            next_actions=self._build_route_next_actions(requirement.route, route_succeeded),
        )
        result = PptFinalDeliveryResult(
            product_result=product_result,
            delivery_manifest=delivery_manifest,
            output_files=output_files,
        )
        tool_context.state[PPT_FINAL_DELIVERY_RESULT_STATE_KEY] = result.model_dump(mode="json")
        return result

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
        skill_runtime_path = self._stage_private_skill_runtime_files(skill_name, tool_context.state)
        tool_context.state[PPT_PRODUCT_ACTIVE_SKILL_STATE_KEY] = {
            "name": skill_name,
            "content": skill_content,
            "runtime_path": skill_runtime_path,
        }

        if not _supports_agent_tool_context(tool_context):
            tool_context.state["ppt_private_skill_execution_output"] = {
                "status": "fallback",
                "message": "PptProductManager skill runner skipped because no ADK AgentTool-compatible context was available.",
                "source": "deterministic_fallback",
            }
            if not _private_skill_allows_html_fallback(requirement, selection):
                return _build_private_skill_failure(
                    skill_name=skill_name,
                    message=tool_context.state["ppt_private_skill_execution_output"]["message"],
                    output_format=str(selection.get("output_format") or requirement.output_format or "pptx"),
                )
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

        agent_state = _copy_state(tool_context.state)
        self._ensure_private_skill_source_files_visible(agent_state, requirement)
        agent_state["ppt_product_manager_skill_run_base"] = {
            "confirmed_requirement": requirement.model_dump(mode="json"),
            "deck_content_plan": content_plan.model_dump(mode="json"),
            "system_selection": selection,
            "active_skill": {
                "name": skill_name,
                "content": skill_content,
                "runtime_path": skill_runtime_path,
            },
            "available_experts": sorted((expert_agents or {}).keys()),
        }
        agent_state["ppt_private_skill_execution_base"] = {
            "confirmed_requirement": requirement.model_dump(mode="json"),
            "deck_content_plan": content_plan.model_dump(mode="json"),
            "system_selection": selection,
            "active_skill": {
                "name": skill_name,
                "content": skill_content,
                "runtime_path": skill_runtime_path,
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
        final_state: dict[str, Any] | None = None
        pptx_snapshot: WorkspacePptxSnapshot | None = None
        try:
            pptx_snapshot = _snapshot_workspace_pptx_files()
            await _run_ppt_internal_agent_tool(
                agent=self,
                request=_build_product_manager_skill_run_user_message(
                    requirement=requirement,
                    content_plan=content_plan,
                    system_selection=selection,
                    skill_content=skill_content,
                    available_experts=sorted((expert_agents or {}).keys()),
                ),
                tool_context=tool_context,
                initial_state=agent_state,
            )
            final_state = _copy_state(tool_context.state)
            private_build = self._resolve_or_recover_private_skill_build(
                final_state,
                skill_name=skill_name,
                before_snapshot=pptx_snapshot,
            )
            if not str(private_build.get("output_path") or "").strip():
                raise ValueError("PptProductManager did not save a private skill presentation artifact.")
            self._copy_private_skill_execution_state(
                parent_state=tool_context.state,
                child_state=final_state,
                private_build=private_build,
            )
            tool_context.state["ppt_private_skill_execution_output"] = {
                "status": "success",
                "message": "PptProductManager ran the selected private skill and saved the artifact.",
                "source": "ppt_product_manager",
            }
            return private_build
        except Exception as exc:
            if final_state is None:
                final_state = _copy_state(tool_context.state)
            if isinstance(final_state, dict):
                private_build = self._resolve_or_recover_private_skill_build(
                    final_state,
                    skill_name=skill_name,
                    before_snapshot=pptx_snapshot,
                )
                if str(private_build.get("output_path") or "").strip():
                    private_build = copy.deepcopy(private_build)
                    warning = (
                        "PptProductManager recovered a generated private-skill PPTX after "
                        f"a later execution error: {type(exc).__name__}: {exc}"
                    )
                    private_build["execution_warning"] = warning
                    self._copy_private_skill_execution_state(
                        parent_state=tool_context.state,
                        child_state=final_state,
                        private_build=private_build,
                    )
                    tool_context.state["ppt_private_skill_execution_output"] = {
                        "status": "success_with_warning",
                        "message": warning,
                        "source": "ppt_product_manager_recovery",
                    }
                    tool_context.state["ppt_private_skill_execution_warning"] = warning
                    return private_build
            if isinstance(final_state, dict):
                for key in (
                    "ppt_private_skill_execution_agent_message",
                    "ppt_skill_last_expert_result",
                    "active_product_ppt_skill_file",
                ):
                    if key in final_state:
                        tool_context.state[key] = copy.deepcopy(final_state[key])
            tool_context.state["ppt_private_skill_execution_output"] = {
                "status": "fallback",
                "message": f"PptProductManager private skill fallback: {type(exc).__name__}: {exc}",
                "source": "deterministic_fallback",
            }
            if not _private_skill_allows_html_fallback(requirement, selection):
                return _build_private_skill_failure(
                    skill_name=skill_name,
                    message=tool_context.state["ppt_private_skill_execution_output"]["message"],
                    output_format=str(selection.get("output_format") or requirement.output_format or "pptx"),
                )
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

    @classmethod
    def _resolve_or_recover_private_skill_build(
        cls,
        state: dict[str, Any],
        *,
        skill_name: str,
        before_snapshot: WorkspacePptxSnapshot | None,
    ) -> dict[str, Any]:
        """Resolve a registered private-skill artifact or recover a generated PPTX."""
        private_build = cls._resolve_private_skill_build_from_state(state, skill_name=skill_name)
        if str(private_build.get("output_path") or "").strip():
            return private_build
        if before_snapshot is None:
            return private_build
        recovered_build = cls._recover_unregistered_private_skill_pptx(
            state,
            skill_name=skill_name,
            before_snapshot=before_snapshot,
        )
        return recovered_build or private_build

    @staticmethod
    def _copy_private_skill_execution_state(
        *,
        parent_state: Any,
        child_state: dict[str, Any],
        private_build: dict[str, Any],
    ) -> None:
        """Copy the private-skill delivery state from the child run to the parent run."""
        for key in PPT_PRIVATE_SKILL_FORWARD_STATE_KEYS:
            if key in child_state:
                parent_state[key] = copy.deepcopy(child_state[key])
        parent_state[PPT_PRIVATE_SKILL_BUILD_STATE_KEY] = copy.deepcopy(private_build)
        for key in (
            "ppt_private_skill_execution_agent_message",
            "ppt_skill_last_expert_result",
            "active_product_ppt_skill_file",
        ):
            if key in child_state:
                parent_state[key] = copy.deepcopy(child_state[key])

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
        pptx_path = str(private_build.get("pptx_path") or "").strip()
        artifact_type = str(private_build.get("artifact_type") or "").strip() or ("pptx" if pptx_path else "html")
        output_files = list(private_build.get("output_files") or [])
        status = "success" if output_path else "generation_failed"
        execution_warning = str(private_build.get("execution_warning") or "").strip()
        private_skill_warning = (
            "Private skill delivered an editable PPTX through the SVG native DrawingML route."
            if artifact_type == "pptx"
            else "Private skill delivery may produce HTML instead of editable PPTX; no built-in PPTX export was claimed."
        )
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
                final_pptx=pptx_path,
                intermediate_artifacts=[] if pptx_path else [output_path] if output_path else [],
                output_files=output_files,
            ),
            output_files=output_files,
            warnings=[
                *list(requirement.source_understanding.extraction_warnings),
                private_skill_warning,
                *([execution_warning] if execution_warning else []),
            ],
            next_actions=(
                ["Review the generated private-skill presentation artifact."]
                if status == "success"
                else ["Retry with another PPT system or inspect the private skill execution output."]
            ),
        )

    def _finalize_private_skill_delivery_phase(
        self,
        *,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        system_selection: dict[str, Any],
        private_build: dict[str, Any],
        tool_context: ToolContext,
    ) -> PptPrivateSkillDeliveryResult:
        """Build and persist the private-skill delivery phase result."""
        product_result = self._build_private_skill_delivery_result(
            requirement=requirement,
            content_plan=content_plan,
            system_selection=system_selection,
            private_build=private_build,
        )
        result = PptPrivateSkillDeliveryResult(
            product_result=product_result,
            private_build=copy.deepcopy(private_build),
            delivery_manifest=product_result.delivery_manifest,
            output_files=list(product_result.output_files),
        )
        tool_context.state[PPT_PRIVATE_SKILL_DELIVERY_RESULT_STATE_KEY] = result.model_dump(mode="json")
        return result

    @staticmethod
    def _resolve_private_skill_build_from_state(
        state: dict[str, Any],
        *,
        skill_name: str,
    ) -> dict[str, Any]:
        """Return the final artifact saved by a private PPT skill run."""
        private_build = dict(state.get(PPT_PRIVATE_SKILL_BUILD_STATE_KEY) or {})
        if str(private_build.get("output_path") or "").strip():
            output_path = str(private_build.get("output_path") or "").strip()
            is_pptx = output_path.lower().endswith(".pptx") or str(
                private_build.get("output_format") or ""
            ).strip().lower() == "pptx"
            private_build.setdefault("artifact_type", "pptx" if is_pptx else "html")
            private_build.setdefault("output_format", "pptx" if is_pptx else "html")
            private_build.setdefault(
                "source",
                "save_ppt_private_skill_pptx" if is_pptx else "save_ppt_private_skill_html",
            )
            if is_pptx:
                private_build.setdefault("pptx_path", output_path)
            return private_build

        svg_export = state.get("ppt_svg_pptx_export") or {}
        if isinstance(svg_export, dict):
            pptx_path = str(svg_export.get("pptx_path") or "").strip()
            if pptx_path:
                return {
                    "status": svg_export.get("status") or "success",
                    "message": svg_export.get("message")
                    or f"Private PPT skill `{skill_name}` exported SVG pages to PPTX.",
                    "artifact_type": "pptx",
                    "output_format": "pptx",
                    "source": "export_ppt_svg_to_pptx",
                    "output_path": pptx_path,
                    "pptx_path": pptx_path,
                    "output_files": list(svg_export.get("output_files") or []),
                    "conversion_report": dict(svg_export.get("conversion_report") or {}),
                }

        route_build = state.get("ppt_route_build") or {}
        if isinstance(route_build, dict):
            pptx_path = str(route_build.get("pptx_path") or "").strip()
            if pptx_path:
                output_files = list(state.get("new_files") or [])
                return {
                    "status": "success",
                    "message": f"Private PPT skill `{skill_name}` dispatched the SVG route to PPTX.",
                    "artifact_type": "pptx",
                    "output_format": "pptx",
                    "source": "dispatch_ppt_route",
                    "output_path": pptx_path,
                    "pptx_path": pptx_path,
                    "output_files": output_files,
                    "route_build": dict(route_build),
                }

        return {}

    @classmethod
    def _recover_unregistered_private_skill_pptx(
        cls,
        state: dict[str, Any],
        *,
        skill_name: str,
        before_snapshot: WorkspacePptxSnapshot,
    ) -> dict[str, Any]:
        """Register a PPTX generated by a private skill that forgot the save tool."""
        source_path = _select_new_or_modified_workspace_pptx(state, before_snapshot=before_snapshot)
        if source_path is None:
            return {}

        output_dir = cls._build_private_skill_output_dir(state)
        output_path = _copy_pptx_to_private_skill_output(source_path, output_dir)
        relative_path = workspace_relative_path(output_path)
        output_files = cls._record_output_files(
            state,
            [relative_path],
            description=f"Private PPT skill `{skill_name}` recovered PPTX artifact.",
            final_file_paths=[relative_path],
        )
        result = {
            "status": "success",
            "message": f"Private PPT skill `{skill_name}` generated a PPTX artifact.",
            "artifact_type": "pptx",
            "output_format": "pptx",
            "source": "private_skill_pptx_recovery",
            "skill_name": str(skill_name or "").strip(),
            "output_path": relative_path,
            "pptx_path": relative_path,
            "output_files": output_files,
            "recovered_from_path": workspace_relative_path(source_path),
        }
        state[PPT_PRIVATE_SKILL_BUILD_STATE_KEY] = result
        state["current_output"] = result
        return result

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
        template = requirement.template_requirement
        if (
            route == "xml"
            and template.use_template
            and template.template_source == "user"
            and any(skill.name == "pptx" for skill in self.skill_registry.list_skills())
        ):
            return {
                "system_type": "private_skill",
                "route": route,
                "skill_name": "pptx",
                "output_format": "pptx",
                "reason": "User-uploaded PowerPoint templates are handled by the private `pptx` skill until the native XML route is implemented.",
            }
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

    def read_prepared_ppt_sources(
        self,
        max_chars: int = 12000,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Read current PPT prepared Markdown sources and material records.

        This tool is intentionally narrower than `read_file`: it reads only
        source-preparation outputs already associated with the active PPT task.
        """
        if tool_context is None:
            return {
                "status": "error",
                "message": "read_prepared_ppt_sources requires tool_context.",
            }

        state = tool_context.state
        source_records = self._prepared_markdown_source_records(state)
        figures = self._prepared_source_material_records(state, "figures")
        output_files = self._prepared_source_material_records(state, "output_files")
        warnings = self._prepared_source_warnings(state)
        try:
            requested_chars = int(max_chars)
        except (TypeError, ValueError):
            requested_chars = 12000
        remaining_chars = min(max(0, requested_chars), 24000)
        source_texts: list[dict[str, Any]] = []
        read_warnings: list[str] = []

        for source in source_records:
            output_path = str(source.get("output_path") or "").strip()
            if not output_path:
                read_warnings.append(f"Prepared source `{source.get('name', '')}` has no output_path.")
                continue
            try:
                markdown = resolve_workspace_path(output_path).read_text(encoding="utf-8")
            except Exception as exc:
                read_warnings.append(f"Could not read prepared source `{output_path}`: {exc}")
                continue

            normalized_markdown = normalize_workspace_markdown_image_paths(
                markdown,
                markdown_path=output_path,
            )
            clipped = normalized_markdown[:remaining_chars] if remaining_chars > 0 else ""
            remaining_chars = max(0, remaining_chars - len(clipped))
            related_figures = [
                figure
                for figure in figures
                if str(figure.get("markdown_output_path") or "").strip() == output_path
                or str(figure.get("source_name") or "").strip() == str(source.get("name") or "").strip()
            ]
            source_texts.append(
                {
                    "name": str(source.get("name") or output_path),
                    "source_path": str(source.get("source_path") or ""),
                    "method": str(source.get("method") or ""),
                    "output_path": output_path,
                    "text": clipped,
                    "truncated": len(clipped) < len(normalized_markdown),
                    "figure_count": len(related_figures),
                }
            )
            if remaining_chars <= 0:
                break

        all_warnings = _dedupe_strings([*warnings, *read_warnings])
        status = "success" if source_texts else "empty"
        return {
            "status": status,
            "message": (
                f"Read {len(source_texts)} prepared PPT Markdown source(s)."
                if source_texts
                else "No prepared PPT Markdown sources are available."
            ),
            "sources": source_texts,
            "figures": figures[:50],
            "figure_count": len(figures),
            "output_files": output_files,
            "warnings": all_warnings,
        }

    @staticmethod
    def _prepared_markdown_source_records(state: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect prepared Markdown source records already bound to this PPT task."""
        records: list[dict[str, Any]] = []

        def _extend(value: Any) -> None:
            if not isinstance(value, list):
                return
            for item in value:
                if isinstance(item, dict):
                    records.append(dict(item))

        _extend(state.get("ppt_source_markdown_sources"))
        source_materials = state.get("ppt_source_materials")
        if isinstance(source_materials, dict):
            _extend(source_materials.get("markdown_sources"))
        preparation_result = state.get(PPT_SOURCE_PREPARATION_RESULT_STATE_KEY)
        if isinstance(preparation_result, dict):
            result_materials = preparation_result.get("source_materials")
            if isinstance(result_materials, dict):
                _extend(result_materials.get("markdown_sources"))
        confirmed_requirement = state.get(PPT_CONFIRMED_REQUIREMENT_STATE_KEY)
        if isinstance(confirmed_requirement, dict):
            requirement_materials = confirmed_requirement.get("source_understanding")
            if isinstance(requirement_materials, dict):
                _extend(requirement_materials.get("markdown_sources"))

        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for record in records:
            key = str(record.get("output_path") or record.get("source_path") or record.get("name") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(record)
        return deduped

    @staticmethod
    def _prepared_source_material_records(state: dict[str, Any], field_name: str) -> list[dict[str, Any]]:
        """Collect one prepared source material record list from stable state locations."""
        records: list[dict[str, Any]] = []

        def _extend(value: Any) -> None:
            if not isinstance(value, list):
                return
            for item in value:
                if isinstance(item, dict):
                    records.append(dict(item))

        _extend(state.get(f"ppt_source_{field_name}"))
        source_materials = state.get("ppt_source_materials")
        if isinstance(source_materials, dict):
            _extend(source_materials.get(field_name))
        preparation_result = state.get(PPT_SOURCE_PREPARATION_RESULT_STATE_KEY)
        if isinstance(preparation_result, dict):
            result_materials = preparation_result.get("source_materials")
            if isinstance(result_materials, dict):
                _extend(result_materials.get(field_name))
        confirmed_requirement = state.get(PPT_CONFIRMED_REQUIREMENT_STATE_KEY)
        if isinstance(confirmed_requirement, dict):
            requirement_materials = confirmed_requirement.get("source_understanding")
            if isinstance(requirement_materials, dict):
                _extend(requirement_materials.get(field_name))

        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for record in records:
            key = str(record.get("path") or record.get("output_path") or record.get("name") or record).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(record)
        return deduped

    @staticmethod
    def _prepared_source_warnings(state: dict[str, Any]) -> list[str]:
        """Collect source-preparation warnings from current task state."""
        warnings: list[str] = []

        def _extend(value: Any) -> None:
            if isinstance(value, list):
                warnings.extend(str(item) for item in value if str(item or "").strip())

        source_materials = state.get("ppt_source_materials")
        if isinstance(source_materials, dict):
            _extend(source_materials.get("extraction_warnings"))
        preparation_result = state.get(PPT_SOURCE_PREPARATION_RESULT_STATE_KEY)
        if isinstance(preparation_result, dict):
            result_materials = preparation_result.get("source_materials")
            if isinstance(result_materials, dict):
                _extend(result_materials.get("extraction_warnings"))
        confirmed_requirement = state.get(PPT_CONFIRMED_REQUIREMENT_STATE_KEY)
        if isinstance(confirmed_requirement, dict):
            requirement_materials = confirmed_requirement.get("source_understanding")
            if isinstance(requirement_materials, dict):
                _extend(requirement_materials.get("extraction_warnings"))
        _extend(state.get("ppt_source_input_warnings"))
        return _dedupe_strings(warnings)

    def list_session_files(
        self,
        section: str = "all",
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """List workspace file records already tracked in the current PPT session."""
        if tool_context is None:
            return {
                "status": "error",
                "message": "list_session_files requires tool_context.",
            }
        normalized_section = str(section or "all").strip().lower() or "all"
        state = tool_context.state
        current_uploaded = list(state.get("uploaded") or state.get("input_files") or [])
        uploaded_history = list(state.get("uploaded_history") or [])
        uploaded = current_uploaded or _latest_uploaded_files_from_history(uploaded_history)
        generated = list(state.get("generated") or [])
        generated_history = list(state.get("generated_history") or [])
        files_history = list(state.get("files_history") or [])
        latest_output_files = _latest_generated_files_from_state(state)
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
            allowed = ", ".join(payload_by_section)
            return {
                "status": "error",
                "message": f"Unsupported section `{section}`. Allowed: {allowed}",
            }
        return {
            "status": "success",
            "section": normalized_section,
            **payload_by_section[normalized_section],
        }

    def list_dir(self, path: str = ".", tool_context: ToolContext | None = None) -> str:
        """List one workspace directory for a private PPT skill run."""
        return self._toolbox.list_dir(path)

    def glob(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 200,
        entry_type: str = "files",
        tool_context: ToolContext | None = None,
    ) -> str:
        """Find workspace files or directories matching a glob pattern."""
        return self._toolbox.glob(
            pattern,
            path=path,
            max_results=max_results,
            entry_type=entry_type,
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
        """Search workspace text files for a private PPT skill run."""
        return self._toolbox.grep(
            pattern,
            path=path,
            glob_pattern=glob_pattern,
            case_insensitive=case_insensitive,
            fixed_strings=fixed_strings,
            output_mode=output_mode,
            context_before=context_before,
            context_after=context_after,
            max_results=max_results,
        )

    def read_file(self, path: str, tool_context: ToolContext | None = None) -> str:
        """Read one UTF-8 text file inside the runtime workspace."""
        return self._toolbox.read_file(path)

    def write_file(
        self,
        path: str,
        content: str,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Write one UTF-8 text file inside the runtime workspace."""
        return self._toolbox.write_file(path, content)

    def edit_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Replace one exact text occurrence inside a workspace file."""
        return self._toolbox.edit_file(path, old_text, new_text)

    def exec_command(
        self,
        command: str,
        working_dir: str | None = None,
        timeout: int = 60,
        background: bool = False,
        yield_ms: int = 1000,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Run one workspace-scoped shell command for a private PPT skill."""
        return self._toolbox.exec_command(
            command,
            working_dir=working_dir,
            timeout=timeout,
            background=background,
            yield_ms=yield_ms,
            scope_key=self._resolve_tool_context_session_id(tool_context),
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
        """Inspect or manage background command sessions for a private PPT skill."""
        return self._toolbox.process_session(
            action=action,
            session_id=session_id,
            input_text=input_text,
            timeout_ms=timeout_ms,
            offset=offset,
            limit=limit,
            scope_key=self._resolve_tool_context_session_id(tool_context),
        )

    def list_ppt_experts(self, tool_context: ToolContext) -> dict[str, Any]:
        """List expert agents available to the current PPT skill run."""
        available_experts = self._skill_runtime_expert_agents or self._product_expert_agents
        expert_names = sorted(available_experts)
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
        available_experts = self._skill_runtime_expert_agents or self._product_expert_agents
        if not clean_agent_name:
            return {
                "status": "error",
                "message": "invoke_ppt_expert requires agent_name.",
            }
        if clean_agent_name not in available_experts:
            return {
                "status": "error",
                "message": (
                    f"Expert `{clean_agent_name}` is not available in this PPT run. "
                    f"Available experts: {', '.join(sorted(available_experts)) or 'none'}."
                ),
            }
        if not has_invocation_context(tool_context):
            return {
                "status": "error",
                "message": "invoke_ppt_expert requires an ADK invocation context.",
            }
        artifact_service = self._skill_runtime_artifact_service or InMemoryArtifactService()
        app_name = self._skill_runtime_app_name or invocation_app_name(tool_context)

        if clean_agent_name == PPT_HTML_PAGE_GENERATION_EXPERT_NAME:
            return await self._invoke_html_page_generation_expert(
                prompt=str(prompt or ""),
                tool_context=tool_context,
                page_generation_agent=available_experts[clean_agent_name],
                app_name=app_name,
                artifact_service=artifact_service,
            )

        try:
            invocation = await dispatch_expert_request(
                ExpertInvocationRequest(
                    agent_name=clean_agent_name,
                    prompt=str(prompt or ""),
                    tool_context=tool_context,
                    expert_agents=available_experts,
                )
            )
        except Exception as exc:
            payload = {
                "status": "error",
                "agent_name": clean_agent_name,
                "message": f"{type(exc).__name__}: {exc}",
                "current_output": {},
                "tool_result": {},
                "output_files": [],
            }
            tool_context.state["ppt_skill_last_expert_result"] = payload
            return payload
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

    async def _invoke_html_page_generation_expert(
        self,
        *,
        prompt: str,
        tool_context: ToolContext,
        page_generation_agent: BaseAgent,
        app_name: str,
        artifact_service: BaseArtifactService | None,
    ) -> dict[str, Any]:
        """Run the PM-managed HTML page expert with PPT-native state inputs."""
        skill_base = dict(tool_context.state.get("ppt_product_manager_skill_run_base") or {})
        content_plan_payload = (
            tool_context.state.get("ppt_deck_content_plan")
            or skill_base.get("deck_content_plan")
            or {}
        )
        if not content_plan_payload:
            return {
                "status": "error",
                "agent_name": PPT_HTML_PAGE_GENERATION_EXPERT_NAME,
                "message": "PptHtmlPageGenerationExpert requires ppt_deck_content_plan in PPT state.",
            }

        requirement_payload = (
            tool_context.state.get(PPT_CONFIRMED_REQUIREMENT_STATE_KEY)
            or skill_base.get("confirmed_requirement")
            or {}
        )
        aspect_ratio = "16:9"
        if requirement_payload:
            try:
                aspect_ratio = ConfirmedRequirement.model_validate(requirement_payload).aspect_ratio
            except Exception:
                aspect_ratio = "16:9"

        try:
            content_plan = DeckContentPlan.model_validate(content_plan_payload)
            template = prepare_html_template(template_id="", aspect_ratio=aspect_ratio).template
            html_pages = await run_html_page_generation_expert(
                content_plan=content_plan,
                template=template,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
                page_generation_agent=page_generation_agent,
            )
        except Exception as exc:
            payload = {
                "status": "error",
                "agent_name": PPT_HTML_PAGE_GENERATION_EXPERT_NAME,
                "message": f"PptHtmlPageGenerationExpert failed: {type(exc).__name__}: {exc}",
                "current_output": {},
                "tool_result": {},
                "output_files": [],
            }
            tool_context.state["ppt_skill_last_expert_result"] = payload
            return payload

        current_output = {
            "status": "success",
            "message": "PptHtmlPageGenerationExpert generated editable HTML slide fragments.",
            "html_pages": html_pages,
            "prompt": prompt,
        }
        tool_result = {
            "status": "success",
            "agent_name": PPT_HTML_PAGE_GENERATION_EXPERT_NAME,
            "html_pages": html_pages,
            "output_files": [],
        }
        payload = {
            "status": "success",
            "agent_name": PPT_HTML_PAGE_GENERATION_EXPERT_NAME,
            "current_output": current_output,
            "tool_result": tool_result,
            "output_files": [],
        }
        tool_context.state["ppt_html_page_generation_expert_result"] = current_output
        tool_context.state["ppt_skill_last_expert_result"] = payload
        tool_context.state["current_output"] = current_output
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
            "artifact_type": "html",
            "output_format": "html",
            "source": "save_ppt_private_skill_html",
            "output_path": relative_path,
            "output_files": output_files,
        }
        tool_context.state[PPT_PRIVATE_SKILL_BUILD_STATE_KEY] = result
        tool_context.state["current_output"] = result
        return result

    def save_ppt_private_skill_pptx(
        self,
        pptx_path: str,
        description: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Register one private-skill PPTX artifact as the final PPT delivery."""
        clean_path = str(pptx_path or "").strip()
        if not clean_path:
            return {
                "status": "error",
                "message": "save_ppt_private_skill_pptx requires pptx_path.",
            }
        if not clean_path.lower().endswith(".pptx"):
            return {
                "status": "error",
                "message": "save_ppt_private_skill_pptx only accepts .pptx files.",
            }
        try:
            resolved_path = resolve_workspace_path(clean_path)
        except Exception as exc:
            return {
                "status": "error",
                "message": f"PPTX path must stay inside the runtime workspace: {exc}",
            }
        if not resolved_path.is_file():
            return {
                "status": "error",
                "message": f"PPTX file does not exist: {clean_path}",
            }

        relative_path = workspace_relative_path(resolved_path)
        output_files = self._record_output_files(
            tool_context.state,
            [relative_path],
            description=(
                str(description or "").strip()
                or "PPT product private skill PPTX artifact."
            ),
            final_file_paths=[relative_path],
        )
        result = {
            "status": "success",
            "message": f"Registered private PPT skill PPTX at {relative_path}.",
            "artifact_type": "pptx",
            "output_format": "pptx",
            "source": "save_ppt_private_skill_pptx",
            "output_path": relative_path,
            "pptx_path": relative_path,
            "output_files": output_files,
        }
        tool_context.state[PPT_PRIVATE_SKILL_BUILD_STATE_KEY] = result
        tool_context.state["current_output"] = result
        return result

    def save_ppt_design_strategy(
        self,
        strategy_json: dict[str, Any],
        confirmation_json: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Validate and save the generic PPT design strategy for SVG-capable workflows."""
        strategy = PptDesignStrategy.model_validate(strategy_json or {})
        confirmation = PptDesignConfirmation.model_validate(confirmation_json or {})
        tool_context.state[PPT_DESIGN_STRATEGY_STATE_KEY] = strategy.model_dump(mode="json")
        tool_context.state[PPT_DESIGN_CONFIRMATION_STATE_KEY] = confirmation.model_dump(mode="json")
        result = {
            "status": "success",
            "message": "PPT design strategy saved.",
            "design_strategy": strategy.model_dump(mode="json"),
            "design_confirmation": confirmation.model_dump(mode="json"),
        }
        tool_context.state["current_output"] = result
        return result

    def save_ppt_svg_execution_plan(
        self,
        execution_plan_json: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Validate and save SVG route execution constraints."""
        current_payload = tool_context.state.get(PPT_SVG_EXECUTION_PLAN_STATE_KEY) or {}
        merged_payload = _merge_svg_execution_plan_payload(
            current_payload=current_payload,
            incoming_payload=execution_plan_json or {},
        )
        execution_plan = PptSvgExecutionPlan.model_validate(merged_payload)
        tool_context.state[PPT_SVG_EXECUTION_PLAN_STATE_KEY] = execution_plan.model_dump(mode="json")
        result = {
            "status": "success",
            "message": "PPT SVG execution plan saved.",
            "svg_execution_plan": execution_plan.model_dump(mode="json"),
        }
        tool_context.state["current_output"] = result
        return result

    def read_ppt_svg_execution_plan(self, tool_context: ToolContext) -> dict[str, Any]:
        """Read the saved SVG execution plan for a PPT SVG expert or skill."""
        execution_plan_payload = tool_context.state.get(PPT_SVG_EXECUTION_PLAN_STATE_KEY) or {}
        if not execution_plan_payload:
            return {
                "status": "error",
                "message": "No PPT SVG execution plan is saved in session state.",
            }
        execution_plan = PptSvgExecutionPlan.model_validate(execution_plan_payload)
        return {
            "status": "success",
            "svg_execution_plan": execution_plan.model_dump(mode="json"),
        }

    def save_ppt_svg_page(
        self,
        slide_number: int,
        svg_content: str,
        file_name: str,
        title: str,
        page_type: str,
        page_rhythm: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Save one generated SVG slide page into the current PPT route output directory."""
        try:
            normalized_slide_number = max(1, int(slide_number))
        except (TypeError, ValueError):
            raise ValueError("slide_number must be a positive integer.")

        clean_svg = _strip_svg_code_fence(svg_content)
        root = ET.fromstring(clean_svg)
        if str(root.tag or "").rsplit("}", 1)[-1] != "svg":
            raise ValueError("svg_content must have an <svg> root element.")

        output_dir = self._resolve_svg_route_output_dir(tool_context.state)
        svg_dir = output_dir / "svg_pages"
        svg_dir.mkdir(parents=True, exist_ok=True)
        execution_plan = PptSvgExecutionPlan.model_validate(
            tool_context.state.get(PPT_SVG_EXECUTION_PLAN_STATE_KEY) or {}
        )
        validation_issues = validate_svg_content(
            clean_svg,
            execution_plan=execution_plan,
            svg_dir=svg_dir,
            path_label=f"slide_{normalized_slide_number:03d}.svg",
        )
        validation_errors = [
            issue
            for issue in validation_issues
            if str(issue.get("severity") or "error") == "error"
        ]
        if validation_errors:
            details = "; ".join(str(issue.get("message") or "") for issue in validation_errors[:5])
            raise ValueError(f"SVG page does not match the native PPTX converter subset: {details}")
        clean_name = Path(str(file_name or "").strip()).name
        if not clean_name:
            clean_name = f"slide_{normalized_slide_number:03d}.svg"
        if not clean_name.lower().endswith(".svg"):
            clean_name = f"{Path(clean_name).stem or f'slide_{normalized_slide_number:03d}'}.svg"
        output_path = svg_dir / clean_name
        output_path.write_text(clean_svg, encoding="utf-8")
        relative_path = workspace_relative_path(output_path)
        page_result = PptSvgPageResult(
            slide_number=normalized_slide_number,
            title=str(title or "").strip(),
            svg_path=relative_path,
            page_type=str(page_type or "content").strip() or "content",
            page_rhythm=str(page_rhythm or "dense").strip() or "dense",
        )
        pages = list(tool_context.state.get(PPT_SVG_ROUTE_GENERATED_PAGES_KEY) or [])
        pages = [
            page
            for page in pages
            if isinstance(page, dict) and int(page.get("slide_number") or 0) != normalized_slide_number
        ]
        pages.append(page_result.model_dump(mode="json"))
        pages.sort(key=lambda item: int(item.get("slide_number") or 0))
        tool_context.state[PPT_SVG_ROUTE_GENERATED_PAGES_KEY] = pages
        result = {
            "status": "success",
            "message": f"Saved SVG slide {normalized_slide_number} at {relative_path}.",
            "svg_page": page_result.model_dump(mode="json"),
        }
        tool_context.state["current_output"] = result
        return result

    def check_ppt_svg_quality(
        self,
        svg_page_paths: list[str],
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Run the SVG route quality checker against saved SVG pages."""
        execution_plan = PptSvgExecutionPlan.model_validate(
            tool_context.state.get(PPT_SVG_EXECUTION_PLAN_STATE_KEY) or {}
        )
        if not svg_page_paths:
            svg_page_paths = [
                str(page.get("svg_path") or "").strip()
                for page in list(tool_context.state.get(PPT_SVG_ROUTE_GENERATED_PAGES_KEY) or [])
                if isinstance(page, dict)
            ]
        content_plan_payload = (
            tool_context.state.get("ppt_deck_content_plan")
            or tool_context.state.get(PPT_SVG_ROUTE_CONTENT_PLAN_KEY)
            or {}
        )
        expected_count = (
            len(content_plan_payload.get("pages") or [])
            if isinstance(content_plan_payload, dict)
            else len(svg_page_paths)
        )
        quality_report = check_svg_pages_quality(
            svg_page_paths=list(svg_page_paths or []),
            expected_page_count=expected_count,
            execution_plan=execution_plan,
        )
        result = {
            "status": "success" if quality_report.status in {"pass", "warning"} else "error",
            "message": f"PPT SVG quality check status: {quality_report.status}.",
            "quality_report": quality_report.model_dump(mode="json"),
        }
        tool_context.state["ppt_svg_quality_report"] = quality_report.model_dump(mode="json")
        tool_context.state["current_output"] = result
        return result

    def export_ppt_svg_to_pptx(
        self,
        pptx_file_name: str,
        svg_page_paths: list[str],
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Export saved SVG pages into an editable PPTX artifact."""
        execution_plan = PptSvgExecutionPlan.model_validate(
            tool_context.state.get(PPT_SVG_EXECUTION_PLAN_STATE_KEY) or {}
        )
        if not svg_page_paths:
            svg_page_paths = [
                str(page.get("svg_path") or "").strip()
                for page in list(tool_context.state.get(PPT_SVG_ROUTE_GENERATED_PAGES_KEY) or [])
                if isinstance(page, dict)
            ]
        output_dir = self._resolve_svg_route_output_dir(tool_context.state)
        clean_name = Path(str(pptx_file_name or "deck.pptx").strip() or "deck.pptx").name
        if not clean_name.lower().endswith(".pptx"):
            clean_name = f"{Path(clean_name).stem or 'deck'}.pptx"
        pptx_path = output_dir / clean_name
        export_result = export_svg_pages_to_pptx(
            svg_page_paths=list(svg_page_paths or []),
            pptx_path=pptx_path,
            execution_plan=execution_plan,
        )
        relative_path = workspace_relative_path(export_result.pptx_path) if export_result.pptx_path.exists() else ""
        output_files = self._record_output_files(
            tool_context.state,
            [relative_path],
            description="PPT product SVG route PPTX artifact.",
            final_file_paths=[relative_path] if relative_path else [],
        )
        result = {
            "status": "success" if relative_path else "error",
            "message": f"Exported PPT SVG pages to {relative_path}." if relative_path else "PPT SVG export did not produce a PPTX.",
            "pptx_path": relative_path,
            "conversion_report": export_result.conversion_report,
            "output_files": output_files,
        }
        tool_context.state["ppt_svg_pptx_export"] = result
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
        route_execution = await self._run_route_execution_phase(
            requirement=requirement,
            content_plan=content_plan,
            tool_context=tool_context,
            app_name=invocation_app_name(tool_context),
            artifact_service=None,
            expert_agents=self._resolve_ppt_expert_agents(),
        )
        delivery = await self._run_route_final_delivery_phase(
            requirement=requirement,
            content_plan=content_plan,
            route_execution=route_execution,
            tool_context=tool_context,
        )
        route_build = route_execution.route_build
        route_succeeded = bool(route_build.pptx_path)
        payload = {
            "status": "success" if route_succeeded else "generation_failed",
            "selected_route": requirement.route,
            "route_build": route_build.model_dump(mode="json"),
            "route_execution": route_execution.model_dump(mode="json"),
            "output_files": delivery.output_files,
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
        expert_agents: dict[str, BaseAgent] | None = None,
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
                page_generation_agent=(expert_agents or {}).get(PPT_HTML_PAGE_GENERATION_EXPERT_NAME),
            )
        if requirement.route == "svg":
            return await build_svg_route_with_agent(
                requirement=requirement,
                content_plan=content_plan,
                output_dir=output_dir,
                tool_context=tool_context,
                app_name=app_name,
                artifact_service=artifact_service,
                design_strategy_agent=(expert_agents or {}).get(PPT_DESIGN_STRATEGY_EXPERT_NAME),
                svg_executor_agent=(expert_agents or {}).get(PPT_SVG_DECK_EXECUTOR_EXPERT_NAME),
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
                "but not implemented yet. Use an implemented built-in route or a matching private PPT skill."
            ),
            selected_route=requirement.route,
            confirmed_requirement=requirement,
            delivery_manifest=DeliveryManifest(),
            warnings=[f"{requirement.route.upper()} route is registered but not available through built-in dispatch."],
            next_actions=["Use an implemented route or a matching private PPT skill for this request."],
        )

    @staticmethod
    def _build_route_output_dir(state: dict[str, Any], *, route: str = "html") -> Path:
        """Return a deterministic output directory for the current PPT route run."""
        session_id = str(state.get("sid") or "ppt-session").strip()
        turn_index = int(state.get("turn_index", 0) or 0)
        step = int(state.get("step", 0) or 0)
        clean_route = str(route or "html").strip().lower()
        if clean_route not in {"html", "svg", "xml"}:
            clean_route = "html"
        output_dir = generated_session_dir(session_id, turn_index=turn_index) / f"ppt_{clean_route}_route_step_{step}"
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _resolve_svg_route_output_dir(self, state: dict[str, Any]) -> Path:
        """Resolve the current SVG route output directory for expert tools."""
        raw_output_dir = str(state.get(PPT_SVG_ROUTE_OUTPUT_DIR_KEY) or "").strip()
        if raw_output_dir:
            output_dir = Path(raw_output_dir)
            if not output_dir.is_absolute():
                output_dir = resolve_workspace_path(raw_output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            state[PPT_SVG_ROUTE_OUTPUT_DIR_KEY] = str(output_dir)
            return output_dir
        output_dir = self._build_route_output_dir(state, route="svg")
        state[PPT_SVG_ROUTE_OUTPUT_DIR_KEY] = str(output_dir)
        return output_dir

    @staticmethod
    def _route_build_output_paths(route_build: Any) -> list[str]:
        """Return all route build artifact paths that should be recorded."""
        return [
            str(path or "").strip()
            for path in [
                getattr(route_build, "pptx_path", ""),
                getattr(route_build, "html_deck_path", ""),
                getattr(route_build, "quality_report_path", ""),
                getattr(route_build, "build_log_path", ""),
                *list(getattr(route_build, "preview_paths", []) or []),
                *list(getattr(route_build, "svg_page_paths", []) or []),
            ]
            if str(path or "").strip()
        ]

    @staticmethod
    def _route_build_intermediate_artifacts(route_build: Any) -> list[str]:
        """Return route intermediate artifacts for the delivery manifest."""
        return [
            str(path or "").strip()
            for path in [
                getattr(route_build, "html_deck_path", ""),
                *list(getattr(route_build, "svg_page_paths", []) or []),
            ]
            if str(path or "").strip()
        ]

    @staticmethod
    def _build_route_delivery_message(route: str, route_succeeded: bool, *, after_confirmation: bool = False) -> str:
        """Build a user-facing delivery message for a route run."""
        clean_route = str(route or "").strip().lower()
        if clean_route == "svg":
            if route_succeeded:
                return "SVG route generated SVG pages, checked them, and exported an editable PPTX."
            return "SVG route generated SVG pages, but failed to export an editable PPTX. See the build log for conversion findings."
        if route_succeeded:
            return (
                "HTML route generated the PPTX after requirement and content-plan confirmation."
                if after_confirmation
                else "HTML route MVP generated an HTML deck, PNG previews, and an editable PPTX."
            )
        return (
            "HTML route generated HTML and previews, but failed to export an editable PPTX after confirmation."
            if after_confirmation
            else "HTML route generated HTML and previews, but failed to export an editable PPTX. See the build log for conversion findings."
        )

    @staticmethod
    def _build_route_next_actions(route: str, route_succeeded: bool) -> list[str]:
        """Build next actions for a completed route run."""
        clean_route = str(route or "").strip().lower()
        if route_succeeded:
            if clean_route == "svg":
                return ["Review the generated PPTX and SVG page artifacts."]
            return ["Review the generated PPTX and previews."]
        if clean_route == "svg":
            return ["Fix the SVG quality or SVG-to-PPTX conversion findings and retry export."]
        return ["Fix the HTML-to-PPTX conversion findings and retry PPTX export."]

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
    def _resolve_tool_context_session_id(tool_context: ToolContext | None) -> str:
        """Safely extract one session id from a tool context-like object."""
        if tool_context is None:
            return ""
        session = getattr(tool_context, "session", None)
        session_id = str(getattr(session, "id", "") or "").strip()
        if session_id:
            return session_id
        state = getattr(tool_context, "state", {}) or {}
        return str(state.get("sid") or "").strip()

    def _stage_private_skill_runtime_files(self, skill_name: str, state: dict[str, Any]) -> str:
        """Copy one private product-ppt skill folder into the runtime workspace."""
        skill_root = self._resolve_private_skill_root(skill_name)
        output_dir = self._build_private_skill_output_dir(state)
        runtime_root = output_dir / "skill_runtime" / Path(skill_name).name
        if runtime_root.exists():
            shutil.rmtree(runtime_root)
        runtime_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            skill_root,
            runtime_root,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
        )
        relative_path = workspace_relative_path(runtime_root)
        state["ppt_private_skill_output_dir"] = workspace_relative_path(output_dir)
        state["ppt_private_skill_runtime_path"] = relative_path
        return relative_path

    def _resolve_private_skill_root(self, skill_name: str) -> Path:
        """Return the project-local root directory for one private product-ppt skill."""
        clean_name = str(skill_name or "").strip()
        for skill in self.skill_registry.list_skills():
            if skill.name == clean_name:
                return skill.path.parent.resolve()
        raise ValueError(f"Product PPT skill '{clean_name}' not found.")

    @staticmethod
    def _record_output_files(
        state: dict[str, Any],
        paths: list[str],
        *,
        description: str = "PPT product route artifact.",
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

        def _record_path(record: Any) -> str:
            if not isinstance(record, dict):
                return ""
            raw_path = str(record.get("path") or "").strip()
            if not raw_path:
                return ""
            try:
                return workspace_relative_path(raw_path)
            except Exception:
                return raw_path

        def _batch_paths(batch: Any) -> tuple[str, ...]:
            if not isinstance(batch, list):
                return ()
            return tuple(path for path in (_record_path(record) for record in batch) if path)

        generated = list(state.get("generated") or [])
        generated_paths = {_record_path(record) for record in generated}
        for record in records:
            path = _record_path(record)
            if path and path not in generated_paths:
                generated.append(record)
                generated_paths.add(path)
        files_history = list(state.get("files_history") or [])
        current_batch_paths = _batch_paths(records)
        if current_batch_paths and not any(_batch_paths(batch) == current_batch_paths for batch in files_history):
            files_history.append(records)
        state["generated"] = generated
        state["new_files"] = records
        state["files_history"] = files_history
        clean_final_file_paths = _normalize_ppt_final_file_paths(final_file_paths or [])
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
        if not source_path:
            return source_input
        if cls._looks_like_url(source_path):
            try:
                return cls._download_remote_source_input_for_workspace(source_input, state, index)
            except Exception as exc:
                warnings = list(state.get("ppt_source_input_warnings") or [])
                source_label = source_input.name or source_path
                warnings.append(f"Could not download remote source {source_label}: {type(exc).__name__}: {exc}")
                state["ppt_source_input_warnings"] = warnings
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

    @classmethod
    def _download_remote_source_input_for_workspace(
        cls,
        source_input: SourceInput,
        state: dict[str, Any],
        index: int,
    ) -> SourceInput:
        """Download one remote PPT source URL into the workspace and return a local SourceInput."""
        source_url = str(source_input.path or "").strip()
        request = Request(source_url, headers={"User-Agent": "Mozilla/5.0 CreativeClaw PptProductManager"})
        try:
            with urlopen(request, timeout=60) as response:
                content_length = cls._coerce_optional_int(response.headers.get("content-length"))
                if content_length is not None and content_length > PPT_REMOTE_SOURCE_MAX_BYTES:
                    raise ValueError(f"Remote source is too large: {content_length} bytes")
                content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                content_disposition = str(response.headers.get("content-disposition") or "")
                data = cls._read_remote_source_bytes(response)
        except (HTTPError, URLError) as exc:
            raise RuntimeError(str(exc)) from exc

        output_dir = cls._remote_source_output_dir(state)
        filename = cls._remote_source_filename(
            source_input,
            source_url=source_url,
            content_type=content_type,
            content_disposition=content_disposition,
            index=index,
        )
        output_path = cls._dedupe_path(output_dir / filename)
        output_path.write_bytes(data)
        relative_path = workspace_relative_path(output_path)

        downloads = list(state.get("ppt_remote_source_downloads") or [])
        downloads.append(
            {
                "source_url": source_url,
                "path": relative_path,
                "name": output_path.name,
                "mime_type": content_type,
            }
        )
        state["ppt_remote_source_downloads"] = downloads

        return source_input.model_copy(
            update={
                "name": output_path.name,
                "path": relative_path,
                "mime_type": source_input.mime_type or content_type,
            }
        )

    @staticmethod
    def _read_remote_source_bytes(response: Any) -> bytes:
        """Read a remote source response with a conservative size limit."""
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > PPT_REMOTE_SOURCE_MAX_BYTES:
                raise ValueError(f"Remote source is too large: over {PPT_REMOTE_SOURCE_MAX_BYTES} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _remote_source_output_dir(state: dict[str, Any]) -> Path:
        """Return the workspace directory used for downloaded remote PPT sources."""
        session_id = str(state.get("sid") or "ppt-session").strip()
        turn_index = PptProductManager._coerce_optional_int(state.get("turn_index"))
        output_dir = generated_session_dir(session_id, turn_index=turn_index) / "ppt_remote_sources"
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    @classmethod
    def _remote_source_filename(
        cls,
        source_input: SourceInput,
        *,
        source_url: str,
        content_type: str,
        content_disposition: str,
        index: int,
    ) -> str:
        """Infer a safe filename for one downloaded remote source."""
        parsed = urlparse(source_url)
        candidate = (
            cls._filename_from_content_disposition(content_disposition)
            or source_input.name
            or Path(unquote(parsed.path)).name
            or f"remote_source_{index:02d}"
        )
        candidate = Path(candidate).name
        original_suffix = Path(candidate).suffix.lower()
        suffix = original_suffix
        content_suffix = cls._extension_from_content_type(content_type)
        if content_suffix and suffix not in PPT_REMOTE_SOURCE_KNOWN_EXTENSIONS:
            suffix = content_suffix
        elif not suffix:
            suffix = content_suffix or ".bin"
        stem_source = Path(candidate).stem if original_suffix in PPT_REMOTE_SOURCE_KNOWN_EXTENSIONS else candidate
        stem = cls._safe_source_stem(stem_source.replace(".", "_"))
        return f"{index:02d}_{stem}{suffix}"

    @staticmethod
    def _filename_from_content_disposition(value: str) -> str:
        """Extract a filename from a Content-Disposition header when present."""
        header = str(value or "")
        filename_star = re.search(r"filename\*\s*=\s*(?:UTF-8''|)([^;]+)", header, flags=re.IGNORECASE)
        if filename_star:
            return unquote(filename_star.group(1).strip().strip('"'))
        filename = re.search(r"filename\s*=\s*([^;]+)", header, flags=re.IGNORECASE)
        if filename:
            return filename.group(1).strip().strip('"')
        return ""

    @staticmethod
    def _extension_from_content_type(content_type: str) -> str:
        """Return a conventional file extension for a response content type."""
        if not content_type:
            return ""
        extension = mimetypes.guess_extension(content_type)
        if extension == ".jpe":
            return ".jpg"
        return extension or ""

    @staticmethod
    def _dedupe_path(path: Path) -> Path:
        """Return a non-existing path by appending a numeric suffix when needed."""
        if not path.exists():
            return path
        for counter in range(2, 1000):
            candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
            if not candidate.exists():
                return candidate
        raise FileExistsError(f"Could not allocate a unique path for {path.name}")

    @classmethod
    def _ensure_private_skill_source_files_visible(
        cls,
        state: dict[str, Any],
        requirement: ConfirmedRequirement,
    ) -> list[dict[str, Any]]:
        """Expose confirmed source/template files as current inputs for child skill runs."""
        existing_paths: set[str] = set()

        def _normalize_existing_path(path: Any) -> str:
            raw_path = str(path or "").strip()
            if not raw_path:
                return ""
            try:
                return workspace_relative_path(raw_path)
            except Exception:
                return raw_path

        def _collect(file_group: Any) -> None:
            if not isinstance(file_group, list):
                return
            for file_info in file_group:
                if not isinstance(file_info, dict):
                    continue
                normalized_path = _normalize_existing_path(file_info.get("path"))
                if normalized_path:
                    existing_paths.add(normalized_path)

        _collect(state.get("uploaded"))
        _collect(state.get("input_files"))
        for entry in list(state.get("uploaded_history") or []):
            if isinstance(entry, dict):
                _collect(entry.get("files"))

        source_inputs = list(requirement.source_inputs or [])
        template_path = str(requirement.template_requirement.template_path or "").strip()
        if template_path and not any(str(item.path or "").strip() == template_path for item in source_inputs):
            source_inputs.append(
                SourceInput(
                    name=Path(template_path).name,
                    path=template_path,
                    role="template",
                    description="User PowerPoint template.",
                )
            )

        appended_records: list[dict[str, Any]] = []
        for source_input in source_inputs:
            source_path = str(source_input.path or "").strip()
            if not source_path or cls._looks_like_url(source_path):
                continue
            normalized_path = _normalize_existing_path(source_path)
            if not normalized_path or normalized_path in existing_paths:
                continue
            try:
                file_record = build_workspace_file_record(
                    normalized_path,
                    description=source_input.description or "Confirmed PPT source input.",
                    source="confirmed_requirement",
                    name=source_input.name or Path(normalized_path).name,
                    turn=cls._coerce_optional_int(state.get("turn_index")),
                    step=cls._coerce_optional_int(state.get("step")),
                )
            except Exception:
                continue
            appended_records.append(file_record)
            existing_paths.add(str(file_record.get("path") or normalized_path))

        if not appended_records:
            return []

        state["uploaded"] = [*list(state.get("uploaded") or []), *appended_records]
        state["input_files"] = [*list(state.get("input_files") or []), *appended_records]
        return appended_records

    @staticmethod
    def _coerce_optional_int(value: Any) -> int | None:
        """Coerce an optional session index value for workspace staging paths."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def _prepare_source_materials_phase(
        self,
        *,
        raw_inputs: list[Any],
        tool_context: ToolContext,
        expert_agents: dict[str, BaseAgent],
        app_name: str,
        artifact_service: InMemoryArtifactService | None,
        source_converter: Any | None,
    ) -> PptSourcePreparationResult:
        """Normalize, stage, convert, and persist source materials for PPT planning."""
        source_inputs = self._normalize_source_inputs(raw_inputs)
        resolved_converter = source_converter or self._build_source_converter(
            tool_context=tool_context,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        input_signature = self._source_preparation_input_signature(
            source_inputs,
            source_converter=resolved_converter,
        )
        reusable = self._load_reusable_source_preparation_result(
            tool_context.state,
            input_signature=input_signature,
        )
        if reusable is not None:
            self._persist_source_preparation_result(tool_context, reusable)
            return reusable

        staged_inputs = self._stage_source_inputs_for_workspace(source_inputs, tool_context.state)
        source_materials = await self._prepare_source_materials(
            staged_inputs,
            fallback_document_type=self._infer_document_type(staged_inputs),
            tool_context=tool_context,
            source_converter=resolved_converter,
        )
        result = PptSourcePreparationResult(
            source_inputs=staged_inputs,
            source_materials=source_materials,
            input_signature=input_signature,
            reused_existing_preparation=False,
        )
        self._persist_source_preparation_result(tool_context, result)
        return result

    def _load_reusable_source_preparation_result(
        self,
        state: dict[str, Any],
        *,
        input_signature: str,
    ) -> PptSourcePreparationResult | None:
        """Return a prior source-preparation result when all material files still exist."""
        payload = state.get(PPT_SOURCE_PREPARATION_RESULT_STATE_KEY)
        if not isinstance(payload, dict):
            return None
        try:
            result = PptSourcePreparationResult.model_validate(payload)
        except Exception:
            return None
        if not result.input_signature or result.input_signature != input_signature:
            return None
        if not self._source_preparation_has_reusable_outputs(result):
            return None
        return result.model_copy(update={"reused_existing_preparation": True})

    @classmethod
    def _source_preparation_input_signature(
        cls,
        source_inputs: list[SourceInput],
        *,
        source_converter: Any | None,
    ) -> str:
        """Return a stable signature for inputs that affect source preparation."""
        payload = {
            "source_inputs": [
                {
                    "source_input": source_input.model_dump(mode="json"),
                    "file_signature": cls._source_input_file_signature(source_input),
                }
                for source_input in source_inputs
            ],
            "source_converter": cls._source_converter_signature(source_converter),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _source_input_file_signature(cls, source_input: SourceInput) -> dict[str, Any]:
        """Return a conservative file fingerprint for one source input path."""
        raw_path = str(source_input.path or "").strip()
        if not raw_path:
            return {"kind": "empty"}
        if cls._looks_like_url(raw_path):
            return {"kind": "remote_url", "path": raw_path}
        try:
            file_path = resolve_workspace_path(raw_path)
        except ValueError:
            file_path = Path(raw_path).expanduser()
        except Exception:
            return {"kind": "unresolved", "path": raw_path}
        if not file_path.is_file():
            return {"kind": "missing", "path": raw_path}
        try:
            digest = hashlib.sha256()
            with file_path.open("rb") as file_obj:
                for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
                    digest.update(chunk)
            return {
                "kind": "file",
                "path": raw_path,
                "sha256": digest.hexdigest(),
            }
        except Exception:
            try:
                stat = file_path.stat()
                return {
                    "kind": "file_stat",
                    "path": raw_path,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            except Exception:
                return {"kind": "unreadable", "path": raw_path}

    @staticmethod
    def _source_converter_signature(source_converter: Any | None) -> str:
        """Return a stable identity string for the active source converter."""
        if source_converter is None:
            return "none"
        module = str(getattr(source_converter, "__module__", "") or type(source_converter).__module__)
        qualname = str(getattr(source_converter, "__qualname__", "") or type(source_converter).__qualname__)
        return f"{module}.{qualname}"

    @classmethod
    def _source_preparation_has_reusable_outputs(cls, result: PptSourcePreparationResult) -> bool:
        """Return whether a source-preparation result has reusable local files."""
        if not result.source_inputs:
            return True
        for source_input in result.source_inputs:
            if not cls._source_material_path_exists(str(source_input.path or "").strip()):
                return False

        material_paths: list[str] = []
        for markdown_source in result.source_materials.markdown_sources:
            if isinstance(markdown_source, dict):
                material_paths.append(str(markdown_source.get("output_path") or "").strip())
        for figure in result.source_materials.figures:
            if isinstance(figure, dict):
                material_paths.append(str(figure.get("path") or "").strip())
        for output_file in result.source_materials.output_files:
            if isinstance(output_file, dict):
                material_paths.append(str(output_file.get("path") or "").strip())

        material_paths = [path for path in material_paths if path]
        if not material_paths:
            return False
        return all(cls._source_material_path_exists(path) for path in material_paths)

    @classmethod
    def _source_material_path_exists(cls, path: str) -> bool:
        """Return whether a source-preparation path points to an existing local file."""
        raw_path = str(path or "").strip()
        if not raw_path or cls._looks_like_url(raw_path):
            return False
        try:
            return resolve_workspace_path(raw_path).is_file()
        except ValueError:
            return Path(raw_path).expanduser().is_file()
        except Exception:
            return False

    @staticmethod
    def _persist_source_preparation_result(
        tool_context: ToolContext,
        result: PptSourcePreparationResult,
    ) -> None:
        """Persist prepared PPT source references in stable session-state keys."""
        source_materials = result.source_materials
        tool_context.state["ppt_source_materials"] = source_materials.model_dump(mode="json")
        tool_context.state["ppt_source_markdown_sources"] = source_materials.markdown_sources
        tool_context.state["ppt_source_figures"] = source_materials.figures
        tool_context.state["ppt_source_output_files"] = source_materials.output_files
        tool_context.state[PPT_SOURCE_PREPARATION_RESULT_STATE_KEY] = result.model_dump(mode="json")

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
        warnings: list[str] = list(tool_context.state.get("ppt_source_input_warnings") or [])
        source_output_dir = self._build_source_output_dir(tool_context.state)

        for index, source_input in enumerate(source_inputs, start=1):
            source_path = str(source_input.path or "").strip()
            source_label = source_input.name or source_path or f"source_{index}"
            if not source_path:
                warnings.append(f"Source {source_label} has no path or URL.")
                continue
            if self._looks_like_url(source_path):
                warnings.append(
                    f"Remote source {source_label} was not downloaded into the workspace; "
                    "source conversion skipped."
                )
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

            results = dict(conversion.get("results") or {})
            output_path = str(results.get("output_path") or parameters.get("output_path") or "")
            if not output_path:
                warnings.append(f"Converted source {source_label} did not report a Markdown path.")
                continue
            markdown = self._normalize_converted_markdown_source(
                str(conversion.get("output_text") or "").strip(),
                output_path=output_path,
            )
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
        """Build AnythingToMD expert parameters for one local source file."""
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
        for match in re.finditer(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^\s)]+)(?:\s+[^)]*)?\)", markdown):
            raw_path = match.group("src").strip()
            figures.append(
                {
                    "source_name": source_name,
                    "alt": cls._clean_markdown_text(match.group("alt")),
                    "path": workspace_relative_file_reference(raw_path, base_path=markdown_output_path),
                    "markdown_output_path": markdown_output_path,
                }
            )
        return figures

    @staticmethod
    def _normalize_converted_markdown_source(markdown: str, *, output_path: str) -> str:
        """Normalize local image links inside one prepared Markdown source."""
        try:
            normalized = normalize_workspace_markdown_image_paths(markdown, markdown_path=output_path)
            output_file = resolve_workspace_path(output_path)
            if output_file.exists() and output_file.is_file():
                output_file.write_text(normalized, encoding="utf-8")
            return normalized
        except Exception:
            return markdown

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
            and has_invocation_context(tool_context)
        ):

            async def _dispatch_converter(source_input: SourceInput, parameters: dict[str, Any]) -> dict[str, Any]:
                invocation = await dispatch_expert_request(
                    ExpertInvocationRequest(
                        agent_name="AnythingToMD",
                        prompt=json.dumps(parameters, ensure_ascii=False),
                        tool_context=tool_context,
                        expert_agents=expert_agents,
                    )
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
        raw_inputs = self._normalize_raw_inputs(inputs)
        source_inputs = self._normalize_source_inputs(raw_inputs)
        reference_assets = self._normalize_reference_assets(raw_inputs)
        explicit_route = self._select_explicit_route(output_options)
        if explicit_route is not None:
            route, route_confirmed = explicit_route
        else:
            route = self._select_default_route_for_inputs(source_inputs)
            route_confirmed = False
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
    def _select_explicit_route(output: dict[str, Any]) -> tuple[str, bool] | None:
        """Return a structured user-selected route when output options specify one."""
        raw_route = str(output.get("route") or output.get("ppt_route") or "").strip().lower()
        if raw_route in {"html", "svg", "xml"}:
            return raw_route, True
        return None

    @staticmethod
    def _select_default_route_for_inputs(source_inputs: list[SourceInput]) -> str:
        """Select the default route from structured inputs, without task keyword matching."""
        return "xml" if PptProductManager._has_powerpoint_source(source_inputs) else "html"

    @staticmethod
    def _has_powerpoint_source(source_inputs: list[SourceInput]) -> bool:
        """Return whether source inputs include a PowerPoint file usable as a template/source deck."""
        return any(
            Path(item.path or item.name).suffix.lower() in {".pptx", ".pptm", ".potx", ".potm"}
            for item in source_inputs
        )

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
            r"(?:科普|介绍|讲解|讲(?!解)|说明|分享|培训|解读)(?P<topic>[A-Za-z0-9\u4e00-\u9fff][^，。,.；;：:]{0,50})",
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
    def _is_invalid_public_topic(topic: str) -> bool:
        """Return whether a topic is too generic or clearly produced by bad parsing."""
        normalized = re.sub(r"[\s，。,.！!？?：:；;、-]+", "", str(topic or "").strip().lower())
        return normalized in {
            "",
            "解",
            "讲",
            "讲解",
            "论文",
            "这个论文",
            "这篇论文",
            "素材",
            "这个素材",
            "ppt",
            "pptx",
        }

    @staticmethod
    def _fallback_topic_from_source_inputs(source_inputs: list[SourceInput]) -> str:
        """Infer a conservative display topic from uploaded source filenames."""
        for source_input in source_inputs:
            raw_name = str(source_input.name or source_input.path or "").strip()
            if not raw_name:
                continue
            stem = Path(raw_name.split("?", 1)[0]).stem.strip()
            stem = re.sub(r"[_-]+", " ", stem).strip()
            stem = re.sub(r"\s+", " ", stem)
            if stem and not PptProductManager._is_invalid_public_topic(stem):
                return stem[:60]
        return ""

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
        pptx_template_candidates = [
            item
            for item in source_inputs
            if Path(item.path or item.name).suffix.lower() in {".pptx", ".pptm", ".potx", ".potm"}
        ]
        has_pptx_source = bool(pptx_template_candidates)
        template_id = str(output.get("template_id") or output.get("template") or "").strip()
        template_path = str(output.get("template_path") or "").strip()
        if not template_path and pptx_template_candidates:
            template_path = str(pptx_template_candidates[0].path or pptx_template_candidates[0].name).strip()
        if route == "xml" and (has_pptx_source or template_path):
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
        if route == "svg" and template_id:
            return TemplateRequirement(
                use_template=True,
                template_source="system",
                template_id=template_id,
                notes="SVG route uses the explicitly selected system SVG layout template.",
            )
        if route == "svg":
            return TemplateRequirement(
                use_template=False,
                template_source="none",
                notes="SVG route may automatically select a system SVG layout template when there is a strong task match.",
            )
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


def _latest_generated_files_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the latest generated file batch from current-turn or historical PPT state."""
    final_files = _file_records_for_paths(list(state.get("final_file_paths") or []), state=state)
    if final_files:
        return final_files
    new_files = [
        file_info
        for file_info in list(state.get("new_files") or [])
        if isinstance(file_info, dict) and str(file_info.get("source", "")).strip() != "channel"
    ]
    if new_files:
        return new_files
    generated = list(state.get("generated") or [])
    if generated:
        return generated
    for entry in reversed(list(state.get("generated_history") or [])):
        if isinstance(entry, dict):
            files = list(entry.get("files") or [])
            if files:
                return files
    for file_group in reversed(list(state.get("files_history") or state.get("artifacts_history") or [])):
        if isinstance(file_group, list) and file_group:
            return [
                file_info
                for file_info in file_group
                if isinstance(file_info, dict) and str(file_info.get("source", "")).strip() != "channel"
            ]
    return []


def _file_records_for_paths(paths: list[str], *, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return session file records matching explicit workspace-relative paths."""
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


def _snapshot_workspace_pptx_files() -> WorkspacePptxSnapshot:
    """Return a lightweight fingerprint map for PPTX files currently in the workspace."""
    snapshot: WorkspacePptxSnapshot = {}
    for path in workspace_root().rglob("*.pptx"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            snapshot[workspace_relative_path(path)] = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            continue
    return snapshot


def _select_new_or_modified_workspace_pptx(
    state: dict[str, Any],
    *,
    before_snapshot: WorkspacePptxSnapshot,
) -> Path | None:
    """Return the best PPTX created or modified by the private-skill run."""
    excluded_paths = _uploaded_workspace_paths(state)
    workspace = workspace_root()
    output_dir = PptProductManager._build_private_skill_output_dir(state)
    session_dir = generated_session_dir(
        str(state.get("sid") or "default"),
        turn_index=int(state.get("turn_index", 0) or 0),
    )
    candidates: list[tuple[int, int, int, str, Path]] = []
    for path in workspace.rglob("*.pptx"):
        if not path.is_file():
            continue
        try:
            relative_path = workspace_relative_path(path)
            stat = path.stat()
        except OSError:
            continue
        if relative_path in excluded_paths or relative_path.startswith("inbox/"):
            continue
        if before_snapshot.get(relative_path) == (stat.st_mtime_ns, stat.st_size):
            continue
        priority = 3
        if path.is_relative_to(output_dir):
            priority = 0
        elif path.is_relative_to(session_dir):
            priority = 1
        elif relative_path.startswith("generated/"):
            priority = 2
        candidates.append((priority, -stat.st_mtime_ns, -stat.st_size, relative_path, path))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][4]


def _uploaded_workspace_paths(state: dict[str, Any]) -> set[str]:
    """Collect uploaded/input workspace paths that should not be treated as generated PPTX."""
    paths: set[str] = set()

    def _collect(file_group: list[dict[str, Any]]) -> None:
        for file_info in file_group:
            if not isinstance(file_info, dict):
                continue
            path = str(file_info.get("path") or "").strip()
            if path:
                try:
                    paths.add(workspace_relative_path(path))
                except Exception:
                    paths.add(path)

    _collect(list(state.get("uploaded") or []))
    _collect(list(state.get("input_files") or []))
    for entry in list(state.get("uploaded_history") or []):
        if isinstance(entry, dict):
            _collect(list(entry.get("files") or []))
    return paths


def _latest_uploaded_files_from_history(uploaded_history: list[Any]) -> list[dict[str, Any]]:
    """Return the latest non-empty uploaded file batch recorded in session history."""
    for entry in reversed(uploaded_history):
        if not isinstance(entry, dict):
            continue
        files = [file_info for file_info in list(entry.get("files") or []) if isinstance(file_info, dict)]
        if files:
            return files
    return []


def _copy_pptx_to_private_skill_output(source_path: Path, output_dir: Path) -> Path:
    """Copy one recovered PPTX into the canonical private-skill output directory."""
    source = resolve_workspace_path(source_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    if source.is_relative_to(output_dir):
        return source

    destination = output_dir / source.name
    if destination.exists():
        stem = destination.stem
        suffix = destination.suffix
        index = 2
        while True:
            candidate = output_dir / f"{stem}_{index}{suffix}"
            if not candidate.exists():
                destination = candidate
                break
            index += 1
    shutil.copy2(source, destination)
    return destination


def _private_skill_allows_html_fallback(
    requirement: ConfirmedRequirement,
    system_selection: dict[str, Any],
) -> bool:
    """Return whether a private skill may use deterministic HTML fallback."""
    output_format = str(system_selection.get("output_format") or "").strip().lower()
    skill_name = str(system_selection.get("skill_name") or "").strip().lower()
    if output_format == "html":
        return True
    if output_format == "pptx":
        return False
    if skill_name == "pptx":
        return False
    if requirement.template_requirement.use_template and requirement.template_requirement.template_source == "user":
        return False
    return requirement.output_format.lower() != "pptx"


def _build_private_skill_failure(
    *,
    skill_name: str,
    message: str,
    output_format: str = "pptx",
) -> dict[str, Any]:
    """Build a private-skill failure payload without writing a fallback artifact."""
    clean_format = str(output_format or "pptx").strip().lower() or "pptx"
    return {
        "status": "error",
        "message": message,
        "artifact_type": clean_format,
        "output_format": clean_format,
        "source": "private_skill_execution_failure",
        "skill_name": str(skill_name or "").strip(),
        "output_path": "",
        "pptx_path": "",
        "output_files": [],
    }


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
        "route_choice_policy": [
            "User-specified route/system wins when available.",
            "If source_inputs include PPTX/PPTM/POTX/POTM and the user did not specify another route, choose private skill `pptx`, route `xml`, output_format `pptx`.",
            "If there is no PowerPoint input and no explicit route, choose built-in `html` or `svg` from task fit.",
            "Do not use keyword matching as the decision mechanism.",
        ],
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
        "workspace_tool_contract": {
            "paths": (
                "Use workspace-relative paths. Prefer confirmed_requirement_json.source_inputs and "
                "template_requirement.template_path for uploaded templates/documents. "
                "list_session_files(section='uploaded') returns current uploaded files or the latest "
                "uploaded-history files when the run happens after user confirmation."
            ),
            "skill_runtime_path": "The selected skill folder is staged in state.active_product_ppt_skill.runtime_path and state.ppt_private_skill_runtime_path.",
            "skill_output_dir": "Write final private-skill artifacts under state.ppt_private_skill_output_dir when possible.",
            "tools": [
                "list_session_files",
                "list_dir",
                "glob",
                "grep",
                "read_file",
                "write_file",
                "edit_file",
                "exec_command",
                "process_session",
            ],
        },
        "user_template_pptx_workflow_checklist": [
            "Locate the PPTX/POTX template from confirmed_requirement_json.source_inputs or template_requirement.template_path first.",
            "If the template path is not obvious, call list_session_files(section='uploaded'), then fall back to uploaded_history or all.",
            "Analyze the template with thumbnail/text/XML inspection as preparation only.",
            "Map DeckContentPlan pages to reusable template slides or layouts.",
            "Create, edit, duplicate, delete, reorder, or pack slides into a new .pptx.",
            "Write the generated deck under ppt_private_skill_output_dir when possible.",
            "Verify the .pptx file exists before finishing.",
            "Call save_ppt_private_skill_pptx with the generated .pptx path immediately after verification, before optional previews, visual QA, or expert checks.",
            "Treat optional QA/expert failures as warnings after the .pptx is registered; do not let them block delivery.",
            "If blocked, report the concrete blocker instead of returning an empty artifact.",
        ],
        "output_contract": {
            "html_artifact": {
                "tool": "save_ppt_private_skill_html",
                "file_name": "index.html",
                "html_content": "complete standalone HTML presentation",
                "description": "short artifact description",
            },
            "pptx_artifact": {
                "tool_options": ["save_ppt_private_skill_pptx", "export_ppt_svg_to_pptx", "dispatch_ppt_route"],
                "pptx_file_name": "deck.pptx",
                "svg_page_paths": "pass [] to use pages saved in session state",
            },
        },
    }
    return (
        "Run the selected private PPT skill as PptProductManager.\n"
        "Let the selected skill content drive the workflow, layout choices, resources, and optional expert/tool use.\n"
        "Use the confirmed requirement and deck content plan as content truth.\n"
        "Read additional skill files with read_product_ppt_skill_file when the skill references them.\n"
        "Use confirmed_requirement_json.source_inputs and template_requirement.template_path first for uploaded template/source paths. "
        "Use list_session_files as a fallback or cross-check, then use workspace file/search/command tools for skill execution.\n"
        "Use the staged skill runtime path when running bundled scripts; keep command working directories and output files inside the workspace.\n"
        "Write the final private-skill deliverable under state.ppt_private_skill_output_dir when possible.\n"
        "Use list_ppt_experts and invoke_ppt_expert when the skill needs a registered expert; for experts that require structured parameters, pass a JSON object string in the prompt field.\n"
        "When the user uploaded or referenced a PPTX/POTX template and the selected skill is pptx, follow the user_template_pptx_workflow_checklist in the payload. "
        "Template thumbnails, Markdown extraction, XML inspection, and layout notes are analysis artifacts only, not final deliverables. "
        "Do not stop after thumbnail.py, markitdown, or template inspection when the requested deliverable is PPTX.\n"
        "For PPTX/template private skills, generate a real .pptx in the workspace, verify that it exists, and immediately register it with save_ppt_private_skill_pptx before any optional preview rendering, QA, or expert review.\n"
        "After a PPTX is registered, optional QA or expert failures may be recorded as warnings but must not block delivery.\n"
        "If a PPTX/template private skill is blocked, return a concrete blocker such as missing template path, template parse failure, command failure, missing dependency, write failure, or unsupported template structure.\n"
        "For HTML private skills, save the final artifact by calling save_ppt_private_skill_html.\n"
        "For SVG private skills, save SVG pages, run quality checks, then call export_ppt_svg_to_pptx, "
        "or call dispatch_ppt_route(route='svg') when the built-in SVG route is sufficient.\n\n"
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


def _strip_svg_code_fence(svg_content: str) -> str:
    """Remove a single Markdown code fence around generated SVG."""
    content = str(svg_content or "").strip()
    match = re.fullmatch(r"```(?:svg|xml)?\s*(?P<body>.*?)\s*```", content, flags=re.DOTALL | re.IGNORECASE)
    if match:
        content = str(match.group("body") or "").strip()
    return f"{content}\n" if content else ""


_SVG_EXECUTION_PLAN_PRESERVE_FIELDS = {
    "page_layouts",
    "page_rhythm_by_slide",
    "typography_ramp",
    "page_rhythm_guidance",
    "page_type_layout_guidance",
    "template_adherence_rules",
    "supported_svg_tags",
    "convertible_svg_tags",
    "forbidden_svg_tags",
    "forbidden_svg_attributes",
    "quality_constraints",
}


def _merge_svg_execution_plan_payload(
    *,
    current_payload: Any,
    incoming_payload: Any,
) -> dict[str, Any]:
    """Merge an agent-provided SVG execution plan with the current route lock."""
    current = copy.deepcopy(current_payload) if isinstance(current_payload, dict) else {}
    incoming = copy.deepcopy(incoming_payload) if isinstance(incoming_payload, dict) else {}
    merged = {**current, **incoming}
    for field_name in _SVG_EXECUTION_PLAN_PRESERVE_FIELDS:
        current_value = current.get(field_name)
        incoming_value = incoming.get(field_name)
        if _is_empty_svg_plan_value(incoming_value) and not _is_empty_svg_plan_value(current_value):
            merged[field_name] = current_value
            continue
        if isinstance(current_value, dict) and isinstance(incoming_value, dict) and current_value:
            merged[field_name] = {**current_value, **incoming_value}
    return merged


def _is_empty_svg_plan_value(value: Any) -> bool:
    """Return whether a plan field value should not wipe an existing lock field."""
    return value is None or value == "" or value == [] or value == {}


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


def _supports_agent_tool_context(tool_context: ToolContext) -> bool:
    """Return whether the context can safely run an ADK AgentTool child agent."""
    return supports_agent_tool_context(tool_context)


async def _run_ppt_internal_agent_tool(
    *,
    agent: LlmAgent,
    request: str,
    tool_context: ToolContext,
    initial_state: dict[str, Any] | None = None,
) -> None:
    """Run a small PPT product-internal agent through ADK AgentTool."""
    await run_agent_tool(
        agent=agent,
        request=request,
        tool_context=tool_context,
        initial_state=initial_state,
    )


def _supports_dynamic_workflow(tool_context: ToolContext) -> bool:
    """Return whether the current ADK context can run Workflow nodes directly."""
    return callable(getattr(tool_context, "run_node", None))


async def _run_ppt_auto_confirm_workflow(
    *,
    manager: PptProductManager,
    task: str,
    raw_inputs: list[Any],
    output: dict[str, Any],
    tool_context: ToolContext,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    source_converter: Any | None,
    content_plan_builder: Any | None,
    asset_resolver: Any | None,
    system_selection_builder: Any | None,
) -> dict[str, Any]:
    """Run a one-shot auto-confirm PPT request through ADK Workflow."""
    workflow = _build_ppt_auto_confirm_workflow(
        manager=manager,
        expert_agents=expert_agents,
        app_name=app_name,
        artifact_service=artifact_service,
        source_converter=source_converter,
        content_plan_builder=content_plan_builder,
        asset_resolver=asset_resolver,
        system_selection_builder=system_selection_builder,
    )
    result = await tool_context.run_node(
        workflow,
        node_input={
            "task": task,
            "raw_inputs": copy.deepcopy(raw_inputs),
            "output": copy.deepcopy(output),
        },
        use_sub_branch=True,
    )
    return dict(result) if isinstance(result, dict) else {}


def _build_ppt_auto_confirm_workflow(
    *,
    manager: PptProductManager,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    source_converter: Any | None,
    content_plan_builder: Any | None,
    asset_resolver: Any | None,
    system_selection_builder: Any | None,
) -> Workflow:
    """Build the ADK 2 dynamic Workflow for one-shot PPT delivery."""

    @node(name="PptAutoConfirmNode", rerun_on_resume=True)
    async def run_auto_confirm(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Run auto-confirm PPT delivery by composing explicit phase nodes."""
        try:
            product_result = await manager._build_auto_confirm_product_result(
                task=str(node_input.get("task") or ""),
                raw_inputs=list(node_input.get("raw_inputs") or []),
                output=dict(node_input.get("output") or {}),
                tool_context=ctx,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
                source_converter=source_converter,
                content_plan_builder=content_plan_builder,
                asset_resolver=asset_resolver,
                system_selection_builder=system_selection_builder,
            )
        except Exception as exc:
            product_result = manager._build_auto_confirm_error_result(exc)

        result = manager._persist_product_result(ctx, product_result)
        ctx.state[PPT_AUTO_CONFIRM_WORKFLOW_OUTPUT_KEY] = {
            "status": product_result.status,
            "source": "adk_workflow",
            "phase": product_result.phase,
            "branch": _ppt_auto_confirm_branch(product_result),
            "selected_route": product_result.selected_route,
            "output_file_count": len(product_result.output_files),
        }
        return result

    return Workflow(
        name="PptAutoConfirmWorkflow",
        description="Runs a one-shot PPT request through explicit ADK Workflow phases.",
        edges=[("START", run_auto_confirm)],
    )


def _ppt_auto_confirm_branch(product_result: PptProductResult) -> str:
    """Return the compact auto-confirm Workflow branch label for diagnostics."""
    if product_result.status == "needs_clarification":
        return "clarification"
    if product_result.status == "route_not_implemented":
        return "route_not_implemented"
    if product_result.status == "error":
        return "error"
    if product_result.phase == "private_skill_delivery":
        return "private_skill"
    if product_result.phase.endswith("_route_delivery"):
        return "built_in_route"
    return "unknown"


async def _run_ppt_initial_request_workflow(
    *,
    manager: PptProductManager,
    task: str,
    raw_inputs: list[Any],
    output: dict[str, Any],
    tool_context: ToolContext,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: BaseArtifactService | None,
    source_converter: Any | None,
    system_selection_builder: Any | None,
) -> dict[str, Any]:
    """Run the initial interactive PPT request through ADK Workflow."""
    workflow = _build_ppt_initial_request_workflow(
        manager=manager,
        expert_agents=expert_agents,
        app_name=app_name,
        artifact_service=artifact_service,
        source_converter=source_converter,
        system_selection_builder=system_selection_builder,
    )
    result = await tool_context.run_node(
        workflow,
        node_input={
            "task": task,
            "raw_inputs": copy.deepcopy(raw_inputs),
            "output": copy.deepcopy(output),
        },
        use_sub_branch=True,
    )
    return dict(result) if isinstance(result, dict) else {}


def _build_ppt_initial_request_workflow(
    *,
    manager: PptProductManager,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: BaseArtifactService | None,
    source_converter: Any | None,
    system_selection_builder: Any | None,
) -> Workflow:
    """Build the ADK 2 dynamic Workflow for an initial interactive PPT request."""

    @node(name="PptInitialRequestNode", rerun_on_resume=True)
    async def run_initial_request(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Start an interactive PPT workflow through explicit phase nodes."""
        task = str(node_input.get("task") or "")
        raw_inputs = list(node_input.get("raw_inputs") or [])
        output = dict(node_input.get("output") or {})
        source_preparation: PptSourcePreparationResult | None = None
        source_understanding = SourceUnderstanding(
            document_type=manager._infer_document_type(manager._normalize_source_inputs(raw_inputs)),
        )
        source_inputs: list[SourceInput] | None = None
        if raw_inputs:
            source_preparation = await manager._run_source_preparation_phase(
                raw_inputs=raw_inputs,
                tool_context=ctx,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
                source_converter=source_converter,
            )
            source_understanding = source_preparation.source_materials
            source_inputs = source_preparation.source_inputs

        requirement_phase = await manager._prepare_initial_requirement_phase(
            task=task,
            raw_inputs=raw_inputs,
            output=output,
            source_understanding=source_understanding,
            source_inputs=source_inputs,
            tool_context=ctx,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        requirement = requirement_phase.confirmed_requirement
        clarification_questions = manager.validate_confirmed_requirement(requirement)
        if clarification_questions:
            product_result = PptProductResult(
                status="needs_clarification",
                phase="requirement_confirmation",
                message="PptProductManager needs a clearer PPT topic or source material before generation.",
                selected_route=requirement.route,
                confirmed_requirement=requirement,
                delivery_manifest=DeliveryManifest(),
                warnings=[],
                next_actions=clarification_questions,
            )
            result = manager._persist_product_result(ctx, product_result)
            ctx.state[PPT_INITIAL_REQUEST_WORKFLOW_OUTPUT_KEY] = {
                "status": str(result.get("status") or ""),
                "source": "adk_workflow",
                "branch": "clarification",
                "analysis_source": requirement_phase.analysis_output.get("source") or "",
            }
            return result

        system_selection_phase = await manager._select_ppt_system_phase(
            task=task,
            output=output,
            requirement=requirement,
            tool_context=ctx,
            app_name=app_name,
            artifact_service=artifact_service,
            system_selection_builder=system_selection_builder,
        )
        system_selection = system_selection_phase.system_selection.model_dump(mode="json")
        requirement = manager._apply_system_selection_to_requirement(requirement, system_selection)
        ctx.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = requirement.model_dump(mode="json")
        workflow_state = {
            "workflow_id": manager._build_workflow_id(ctx.state),
            "stage": PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION,
            "revision": 1,
            "task": task,
            "raw_inputs": raw_inputs,
            "output": dict(output or {}),
            "confirmed_requirement": requirement.model_dump(mode="json"),
            "system_selection": system_selection,
        }
        if source_preparation is not None:
            workflow_state["source_inputs"] = [
                item.model_dump(mode="json") for item in source_preparation.source_inputs
            ]
            workflow_state["source_materials"] = source_preparation.source_materials.model_dump(mode="json")
        manager._mark_confirmation_waiting(workflow_state, ctx.state)
        _persist_ppt_workflow_state(ctx.state, workflow_state)
        product_result = manager._build_requirement_confirmation_result(requirement, workflow_state)
        result = manager._persist_product_result(ctx, product_result)
        ctx.state[PPT_INITIAL_REQUEST_WORKFLOW_OUTPUT_KEY] = {
            "status": str(result.get("status") or ""),
            "source": "adk_workflow",
            "branch": "requirement_confirmation",
            "stage": PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION,
            "analysis_source": requirement_phase.analysis_output.get("source") or "",
            "selection_source": system_selection_phase.selection_output.get("source") or "",
        }
        return result

    return Workflow(
        name="PptInitialRequestWorkflow",
        description="Starts an interactive PPT workflow and stops at requirement confirmation.",
        edges=[("START", run_initial_request)],
    )


async def _run_ppt_requirement_confirmation_workflow(
    *,
    manager: PptProductManager,
    user_response: str,
    workflow_state: dict[str, Any],
    tool_context: ToolContext,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    source_converter: Any | None,
    content_plan_builder: Any | None,
) -> dict[str, Any]:
    """Run the first PPT confirmation continuation through ADK Workflow."""
    workflow = _build_ppt_requirement_confirmation_workflow(
        manager=manager,
        expert_agents=expert_agents,
        app_name=app_name,
        artifact_service=artifact_service,
        source_converter=source_converter,
        content_plan_builder=content_plan_builder,
    )
    result = await tool_context.run_node(
        workflow,
        node_input={
            "user_response": user_response,
            "workflow_state": copy.deepcopy(workflow_state),
        },
        use_sub_branch=True,
    )
    return dict(result) if isinstance(result, dict) else {}


def _build_ppt_requirement_confirmation_workflow(
    *,
    manager: PptProductManager,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    source_converter: Any | None,
    content_plan_builder: Any | None,
) -> Workflow:
    """Build the ADK 2 dynamic Workflow for the first PPT confirmation gate."""

    @node(name="PptRequirementConfirmationNode", rerun_on_resume=True)
    async def run_requirement_confirmation(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Continue after requirement confirmation through explicit phase nodes."""
        user_response = str(node_input.get("user_response") or "")
        workflow_state = dict(node_input.get("workflow_state") or {})
        if not manager._is_confirmation_text(user_response):
            base_task = str(workflow_state.get("task") or "")
            raw_inputs = list(workflow_state.get("raw_inputs") or [])
            output = dict(workflow_state.get("output") or {})
            existing_requirement = ConfirmedRequirement.model_validate(
                workflow_state.get("confirmed_requirement") or {}
            )
            source_understanding = existing_requirement.source_understanding or SourceUnderstanding(
                document_type=manager._infer_document_type(manager._normalize_source_inputs(raw_inputs)),
            )
            revision = await manager._revise_requirement_phase(
                existing_requirement=existing_requirement,
                user_response=user_response,
                task=base_task,
                raw_inputs=raw_inputs,
                output=output,
                source_understanding=source_understanding,
                tool_context=ctx,
                app_name=app_name,
                artifact_service=artifact_service,
            )
            requirement = revision.confirmed_requirement
            system_selection_phase = await manager._select_ppt_system_phase(
                task=f"{base_task}\n{user_response}".strip(),
                output=output,
                requirement=requirement,
                tool_context=ctx,
                app_name=app_name,
                artifact_service=artifact_service,
            )
            system_selection = system_selection_phase.system_selection.model_dump(mode="json")
            requirement = manager._apply_system_selection_to_requirement(requirement, system_selection)
            workflow_state.update(
                {
                    "task": base_task,
                    "confirmed_requirement": requirement.model_dump(mode="json"),
                    "system_selection": system_selection,
                    "revision": int(workflow_state.get("revision", 1) or 1) + 1,
                    "stage": PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION,
                }
            )
            manager._mark_confirmation_waiting(workflow_state, ctx.state)
            ctx.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = requirement.model_dump(mode="json")
            _persist_ppt_workflow_state(ctx.state, workflow_state)
            product_result = manager._build_requirement_confirmation_result(requirement, workflow_state)
            result = manager._persist_product_result(ctx, product_result)
            ctx.state[PPT_REQUIREMENT_CONFIRMATION_WORKFLOW_OUTPUT_KEY] = {
                "status": str(result.get("status") or ""),
                "source": "adk_workflow",
                "stage": PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION,
                "branch": "revision",
                "revision_source": revision.revision_output.get("source") or "",
            }
            return result

        requirement = ConfirmedRequirement.model_validate(workflow_state.get("confirmed_requirement") or {})
        system_selection = manager._get_workflow_system_selection(workflow_state, requirement, ctx)
        requirement = manager._apply_system_selection_to_requirement(requirement, system_selection)
        route_registration = manager._route_registry.get(requirement.route)
        if (
            not manager._is_private_skill_selection(system_selection)
            and (route_registration is None or not route_registration.implemented)
        ):
            product_result = manager._build_route_not_implemented_result(requirement, route_registration)
            workflow_state["stage"] = PPT_STAGE_COMPLETED
            _persist_ppt_workflow_state(ctx.state, workflow_state)
            result = manager._persist_product_result(ctx, product_result)
            ctx.state[PPT_REQUIREMENT_CONFIRMATION_WORKFLOW_OUTPUT_KEY] = {
                "status": str(result.get("status") or ""),
                "source": "adk_workflow",
                "stage": PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION,
            }
            return result

        source_preparation = await manager._run_source_preparation_phase(
            raw_inputs=list(workflow_state.get("raw_inputs") or []),
            tool_context=ctx,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            source_converter=source_converter,
        )
        requirement = requirement.model_copy(
            update={
                "source_inputs": source_preparation.source_inputs,
                "source_understanding": source_preparation.source_materials,
            }
        )
        ctx.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = requirement.model_dump(mode="json")

        planning = await manager._run_content_planning_phase(
            requirement,
            tool_context=ctx,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
            content_plan_builder=content_plan_builder,
        )
        content_plan = planning.content_plan
        workflow_state.update(
            {
                "stage": PPT_STAGE_AWAITING_CONTENT_PLAN_CONFIRMATION,
                "confirmed_requirement": requirement.model_dump(mode="json"),
                "source_materials": source_preparation.source_materials.model_dump(mode="json"),
                "deck_content_plan": content_plan.model_dump(mode="json"),
                "deck_content_plan_markdown": planning.deck_content_plan_markdown,
                "system_selection": system_selection,
                "revision": int(workflow_state.get("revision", 1) or 1) + 1,
            }
        )
        manager._mark_confirmation_waiting(workflow_state, ctx.state)
        _persist_ppt_workflow_state(ctx.state, workflow_state)
        product_result = manager._build_content_plan_confirmation_result(requirement, content_plan, workflow_state)
        result = manager._persist_product_result(ctx, product_result)
        ctx.state[PPT_REQUIREMENT_CONFIRMATION_WORKFLOW_OUTPUT_KEY] = {
            "status": str(result.get("status") or ""),
            "source": "adk_workflow",
            "stage": PPT_STAGE_AWAITING_REQUIREMENT_CONFIRMATION,
        }
        return result

    return Workflow(
        name="PptRequirementConfirmationWorkflow",
        description="Continues a PPT workflow from requirement confirmation to content-plan confirmation.",
        edges=[("START", run_requirement_confirmation)],
    )


async def _run_ppt_content_plan_confirmation_workflow(
    *,
    manager: PptProductManager,
    user_response: str,
    workflow_state: dict[str, Any],
    tool_context: ToolContext,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    content_plan_builder: Any | None,
    asset_resolver: Any | None,
) -> dict[str, Any]:
    """Run the second PPT confirmation continuation through ADK Workflow."""
    workflow = _build_ppt_content_plan_confirmation_workflow(
        manager=manager,
        expert_agents=expert_agents,
        app_name=app_name,
        artifact_service=artifact_service,
        content_plan_builder=content_plan_builder,
        asset_resolver=asset_resolver,
    )
    result = await tool_context.run_node(
        workflow,
        node_input={
            "user_response": user_response,
            "workflow_state": copy.deepcopy(workflow_state),
        },
        use_sub_branch=True,
    )
    return dict(result) if isinstance(result, dict) else {}


def _build_ppt_content_plan_confirmation_workflow(
    *,
    manager: PptProductManager,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    content_plan_builder: Any | None,
    asset_resolver: Any | None,
) -> Workflow:
    """Build the ADK 2 dynamic Workflow for the second PPT confirmation gate."""

    @node(name="PptContentPlanConfirmationNode", rerun_on_resume=True)
    async def run_content_plan_confirmation(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Continue after content-plan confirmation through explicit phase nodes."""
        user_response = str(node_input.get("user_response") or "")
        workflow_state = dict(node_input.get("workflow_state") or {})
        requirement = ConfirmedRequirement.model_validate(workflow_state.get("confirmed_requirement") or {})
        system_selection = manager._get_workflow_system_selection(workflow_state, requirement, ctx)
        requirement = manager._apply_system_selection_to_requirement(requirement, system_selection)
        if not manager._is_confirmation_text(user_response):
            revision = await manager._revise_content_plan_phase(
                requirement=requirement,
                user_response=user_response,
                tool_context=ctx,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
                content_plan_builder=content_plan_builder,
            )
            revised_requirement = revision.confirmed_requirement
            content_plan = revision.content_plan
            workflow_state.update(
                {
                    "stage": PPT_STAGE_AWAITING_CONTENT_PLAN_CONFIRMATION,
                    "confirmed_requirement": revised_requirement.model_dump(mode="json"),
                    "deck_content_plan": content_plan.model_dump(mode="json"),
                    "deck_content_plan_markdown": revision.deck_content_plan_markdown,
                    "system_selection": system_selection,
                    "revision": int(workflow_state.get("revision", 1) or 1) + 1,
                }
            )
            manager._mark_confirmation_waiting(workflow_state, ctx.state)
            ctx.state[PPT_CONFIRMED_REQUIREMENT_STATE_KEY] = revised_requirement.model_dump(mode="json")
            _persist_ppt_workflow_state(ctx.state, workflow_state)
            product_result = manager._build_content_plan_confirmation_result(
                revised_requirement,
                content_plan,
                workflow_state,
            )
            result = manager._persist_product_result(ctx, product_result)
            ctx.state[PPT_CONTENT_PLAN_CONFIRMATION_WORKFLOW_OUTPUT_KEY] = {
                "status": str(result.get("status") or ""),
                "source": "adk_workflow",
                "stage": PPT_STAGE_AWAITING_CONTENT_PLAN_CONFIRMATION,
                "branch": "revision",
                "revision_source": revision.revision_output.get("source") or "",
            }
            return result

        content_plan = DeckContentPlan.model_validate(workflow_state.get("deck_content_plan") or {})

        if manager._is_private_skill_selection(system_selection):
            private_execution = await manager._run_private_skill_execution_phase(
                requirement=requirement,
                content_plan=content_plan,
                system_selection=system_selection,
                tool_context=ctx,
                expert_agents=expert_agents,
                app_name=app_name,
                artifact_service=artifact_service,
            )
            private_build = private_execution.private_build
            workflow_state.update(
                {
                    "stage": PPT_STAGE_COMPLETED,
                    "deck_content_plan": content_plan.model_dump(mode="json"),
                    "private_skill_build": private_build,
                    "system_selection": system_selection,
                }
            )
            _persist_ppt_workflow_state(ctx.state, workflow_state)
            private_delivery = await manager._run_private_skill_delivery_phase(
                requirement=requirement,
                content_plan=content_plan,
                system_selection=system_selection,
                private_build=private_build,
                tool_context=ctx,
            )
            result = manager._persist_product_result(ctx, private_delivery.product_result)
            ctx.state[PPT_CONTENT_PLAN_CONFIRMATION_WORKFLOW_OUTPUT_KEY] = {
                "status": str(result.get("status") or ""),
                "source": "adk_workflow",
                "stage": PPT_STAGE_AWAITING_CONTENT_PLAN_CONFIRMATION,
            }
            return result

        asset_resolution = await manager._run_asset_resolution_phase(
            content_plan,
            requirement,
            tool_context=ctx,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            asset_resolver=asset_resolver,
        )
        resolved_plan = asset_resolution.content_plan
        route_execution = await manager._run_route_execution_phase(
            requirement=requirement,
            content_plan=resolved_plan,
            tool_context=ctx,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
        )
        delivery = await manager._run_route_final_delivery_phase(
            requirement=requirement,
            content_plan=resolved_plan,
            route_execution=route_execution,
            tool_context=ctx,
            after_confirmation=True,
        )
        workflow_state.update(
            {
                "stage": PPT_STAGE_COMPLETED,
                "deck_content_plan": resolved_plan.model_dump(mode="json"),
                "route_build": route_execution.route_build.model_dump(mode="json"),
                "system_selection": system_selection,
            }
        )
        _persist_ppt_workflow_state(ctx.state, workflow_state)
        result = manager._persist_product_result(ctx, delivery.product_result)
        ctx.state[PPT_CONTENT_PLAN_CONFIRMATION_WORKFLOW_OUTPUT_KEY] = {
            "status": str(result.get("status") or ""),
            "source": "adk_workflow",
            "stage": PPT_STAGE_AWAITING_CONTENT_PLAN_CONFIRMATION,
        }
        return result

    return Workflow(
        name="PptContentPlanConfirmationWorkflow",
        description="Continues a PPT workflow from content-plan confirmation to final delivery.",
        edges=[("START", run_content_plan_confirmation)],
    )


async def _run_ppt_requirement_revision_phase_workflow(
    *,
    manager: PptProductManager,
    existing_requirement: ConfirmedRequirement,
    user_response: str,
    task: str,
    raw_inputs: list[Any],
    output: dict[str, Any],
    source_understanding: SourceUnderstanding,
    tool_context: ToolContext,
    app_name: str,
    artifact_service: BaseArtifactService | None,
) -> PptRequirementRevisionResult:
    """Run PPT requirement revision as an ADK Workflow node."""
    workflow = _build_ppt_requirement_revision_phase_workflow(
        manager=manager,
        app_name=app_name,
        artifact_service=artifact_service,
    )
    result = await tool_context.run_node(
        workflow,
        node_input={
            "existing_requirement": existing_requirement.model_dump(mode="json"),
            "user_response": user_response,
            "task": task,
            "raw_inputs": copy.deepcopy(raw_inputs),
            "output": copy.deepcopy(output),
            "source_understanding": source_understanding.model_dump(mode="json"),
        },
        use_sub_branch=True,
    )
    return PptRequirementRevisionResult.model_validate(result or {})


def _build_ppt_requirement_revision_phase_workflow(
    *,
    manager: PptProductManager,
    app_name: str,
    artifact_service: BaseArtifactService | None,
) -> Workflow:
    """Build the ADK Workflow node for PPT requirement revision."""

    @node(name="PptRequirementRevisionPhaseNode", rerun_on_resume=True)
    async def run_requirement_revision(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Revise the confirmed PPT requirement as an explicit workflow phase."""
        existing_requirement = ConfirmedRequirement.model_validate(
            node_input.get("existing_requirement") or {}
        )
        source_understanding = SourceUnderstanding.model_validate(
            node_input.get("source_understanding") or {}
        )
        result = await manager._revise_requirement_phase_direct(
            existing_requirement=existing_requirement,
            user_response=str(node_input.get("user_response") or ""),
            task=str(node_input.get("task") or ""),
            raw_inputs=list(node_input.get("raw_inputs") or []),
            output=dict(node_input.get("output") or {}),
            source_understanding=source_understanding,
            tool_context=ctx,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        ctx.state[PPT_REQUIREMENT_REVISION_WORKFLOW_OUTPUT_KEY] = {
            "status": result.revision_output.get("status") or "success",
            "source": "adk_workflow",
            "revision_source": result.revision_output.get("source") or "",
            "has_agent_message": bool(result.agent_message),
            "topic": result.confirmed_requirement.topic,
            "slide_count_target": result.confirmed_requirement.slide_count_policy.target,
        }
        return result.model_dump(mode="json")

    return Workflow(
        name="PptRequirementRevisionPhaseWorkflow",
        description="Revises a confirmed PPT requirement as an explicit workflow phase.",
        edges=[("START", run_requirement_revision)],
    )


async def _run_ppt_requirement_analysis_phase_workflow(
    *,
    manager: PptProductManager,
    task: str,
    raw_inputs: list[Any],
    output: dict[str, Any],
    source_understanding: SourceUnderstanding,
    source_inputs: list[SourceInput] | None,
    tool_context: ToolContext,
    app_name: str,
    artifact_service: BaseArtifactService | None,
) -> PptRequirementAnalysisResult:
    """Run initial PPT requirement analysis as an ADK Workflow node."""
    workflow = _build_ppt_requirement_analysis_phase_workflow(
        manager=manager,
        app_name=app_name,
        artifact_service=artifact_service,
    )
    result = await tool_context.run_node(
        workflow,
        node_input={
            "task": task,
            "raw_inputs": copy.deepcopy(raw_inputs),
            "output": copy.deepcopy(output),
            "source_understanding": source_understanding.model_dump(mode="json"),
            "source_inputs": (
                [source_input.model_dump(mode="json") for source_input in source_inputs]
                if source_inputs is not None
                else None
            ),
        },
        use_sub_branch=True,
    )
    return PptRequirementAnalysisResult.model_validate(result or {})


def _build_ppt_requirement_analysis_phase_workflow(
    *,
    manager: PptProductManager,
    app_name: str,
    artifact_service: BaseArtifactService | None,
) -> Workflow:
    """Build the ADK Workflow node for initial PPT requirement analysis."""

    @node(name="PptRequirementAnalysisPhaseNode", rerun_on_resume=True)
    async def run_requirement_analysis(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Prepare the initial PPT requirement as an explicit workflow phase."""
        source_understanding = SourceUnderstanding.model_validate(
            node_input.get("source_understanding") or {}
        )
        source_inputs_payload = node_input.get("source_inputs")
        source_inputs = (
            [SourceInput.model_validate(item) for item in source_inputs_payload]
            if isinstance(source_inputs_payload, list)
            else None
        )
        result = await manager._prepare_initial_requirement_phase_direct(
            task=str(node_input.get("task") or ""),
            raw_inputs=list(node_input.get("raw_inputs") or []),
            output=dict(node_input.get("output") or {}),
            source_understanding=source_understanding,
            source_inputs=source_inputs,
            tool_context=ctx,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        ctx.state[PPT_REQUIREMENT_ANALYSIS_WORKFLOW_OUTPUT_KEY] = {
            "status": result.analysis_output.get("status") or "success",
            "source": "adk_workflow",
            "analysis_source": result.analysis_output.get("source") or "",
            "has_agent_message": bool(result.agent_message),
            "topic": result.confirmed_requirement.topic,
            "slide_count_target": result.confirmed_requirement.slide_count_policy.target,
        }
        return result.model_dump(mode="json")

    return Workflow(
        name="PptRequirementAnalysisPhaseWorkflow",
        description="Prepares the initial PPT requirement as an explicit workflow phase.",
        edges=[("START", run_requirement_analysis)],
    )


async def _run_ppt_content_plan_revision_phase_workflow(
    *,
    manager: PptProductManager,
    requirement: ConfirmedRequirement,
    user_response: str,
    tool_context: ToolContext,
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    expert_agents: dict[str, BaseAgent],
    content_plan_builder: Any | None,
) -> PptContentPlanRevisionResult:
    """Run PPT content-plan revision as an ADK Workflow node."""
    workflow = _build_ppt_content_plan_revision_phase_workflow(
        manager=manager,
        expert_agents=expert_agents,
        app_name=app_name,
        artifact_service=artifact_service,
        content_plan_builder=content_plan_builder,
    )
    result = await tool_context.run_node(
        workflow,
        node_input={
            "requirement": requirement.model_dump(mode="json"),
            "user_response": user_response,
        },
        use_sub_branch=True,
    )
    return PptContentPlanRevisionResult.model_validate(result or {})


def _build_ppt_content_plan_revision_phase_workflow(
    *,
    manager: PptProductManager,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    content_plan_builder: Any | None,
) -> Workflow:
    """Build the ADK Workflow node for PPT content-plan revision."""

    @node(name="PptContentPlanRevisionPhaseNode", rerun_on_resume=True)
    async def run_content_plan_revision(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Revise the PPT content plan as an explicit workflow phase."""
        requirement = ConfirmedRequirement.model_validate(node_input.get("requirement") or {})
        result = await manager._revise_content_plan_phase_direct(
            requirement=requirement,
            user_response=str(node_input.get("user_response") or ""),
            tool_context=ctx,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
            content_plan_builder=content_plan_builder,
        )
        ctx.state[PPT_CONTENT_PLAN_REVISION_WORKFLOW_OUTPUT_KEY] = {
            "status": result.revision_output.get("status") or "success",
            "source": "adk_workflow",
            "revision_source": result.revision_output.get("source") or "",
            "page_count": len(result.content_plan.pages),
            "has_planning_markdown": bool(result.deck_content_plan_markdown),
        }
        return result.model_dump(mode="json")

    return Workflow(
        name="PptContentPlanRevisionPhaseWorkflow",
        description="Revises a PPT content plan as an explicit workflow phase.",
        edges=[("START", run_content_plan_revision)],
    )


async def _run_ppt_system_selection_phase_workflow(
    *,
    manager: PptProductManager,
    task: str,
    output: dict[str, Any],
    requirement: ConfirmedRequirement,
    tool_context: ToolContext,
    app_name: str,
    artifact_service: BaseArtifactService | None,
    system_selection_builder: Any | None,
) -> PptSystemSelectionResult:
    """Run PPT system selection as an ADK Workflow node."""
    workflow = _build_ppt_system_selection_phase_workflow(
        manager=manager,
        app_name=app_name,
        artifact_service=artifact_service,
        system_selection_builder=system_selection_builder,
    )
    result = await tool_context.run_node(
        workflow,
        node_input={
            "task": task,
            "output": copy.deepcopy(output),
            "requirement": requirement.model_dump(mode="json"),
        },
        use_sub_branch=True,
    )
    return PptSystemSelectionResult.model_validate(result or {})


def _build_ppt_system_selection_phase_workflow(
    *,
    manager: PptProductManager,
    app_name: str,
    artifact_service: BaseArtifactService | None,
    system_selection_builder: Any | None,
) -> Workflow:
    """Build the ADK Workflow node for PPT system selection."""

    @node(name="PptSystemSelectionPhaseNode", rerun_on_resume=True)
    async def run_system_selection(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Select the PPT delivery system as an explicit workflow phase."""
        requirement = ConfirmedRequirement.model_validate(node_input.get("requirement") or {})
        result = await manager._select_ppt_system_phase_direct(
            task=str(node_input.get("task") or ""),
            output=dict(node_input.get("output") or {}),
            requirement=requirement,
            tool_context=ctx,
            app_name=app_name,
            artifact_service=artifact_service,
            system_selection_builder=system_selection_builder,
        )
        ctx.state[PPT_SYSTEM_SELECTION_WORKFLOW_OUTPUT_KEY] = {
            "status": result.selection_output.get("status") or "success",
            "source": "adk_workflow",
            "system_type": result.system_selection.system_type,
            "route": result.system_selection.route,
            "skill_name": result.system_selection.skill_name,
            "selection_source": result.selection_output.get("source") or "",
        }
        return result.model_dump(mode="json")

    return Workflow(
        name="PptSystemSelectionPhaseWorkflow",
        description="Selects the PPT delivery system as an explicit workflow phase.",
        edges=[("START", run_system_selection)],
    )


async def _run_ppt_source_preparation_phase_workflow(
    *,
    manager: PptProductManager,
    raw_inputs: list[Any],
    tool_context: ToolContext,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    source_converter: Any | None,
) -> PptSourcePreparationResult:
    """Run PPT source preparation as an ADK Workflow node."""
    workflow = _build_ppt_source_preparation_phase_workflow(
        manager=manager,
        expert_agents=expert_agents,
        app_name=app_name,
        artifact_service=artifact_service,
        source_converter=source_converter,
    )
    result = await tool_context.run_node(
        workflow,
        node_input={"raw_inputs": copy.deepcopy(raw_inputs)},
        use_sub_branch=True,
    )
    return PptSourcePreparationResult.model_validate(result or {})


def _build_ppt_source_preparation_phase_workflow(
    *,
    manager: PptProductManager,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    source_converter: Any | None,
) -> Workflow:
    """Build the ADK Workflow node for PPT source preparation."""

    @node(name="PptSourcePreparationPhaseNode", rerun_on_resume=True)
    async def run_source_preparation(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Normalize, stage, and convert source materials."""
        result = await manager._prepare_source_materials_phase(
            raw_inputs=list(node_input.get("raw_inputs") or []),
            tool_context=ctx,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            source_converter=source_converter,
        )
        ctx.state[PPT_SOURCE_PREPARATION_WORKFLOW_OUTPUT_KEY] = {
            "status": "success",
            "source": "adk_workflow",
            "source_input_count": len(result.source_inputs),
            "markdown_source_count": len(result.source_materials.markdown_sources),
            "reused_existing_preparation": result.reused_existing_preparation,
        }
        return result.model_dump(mode="json")

    return Workflow(
        name="PptSourcePreparationPhaseWorkflow",
        description="Prepares PPT source inputs as an explicit workflow phase.",
        edges=[("START", run_source_preparation)],
    )


async def _run_ppt_content_planning_phase_workflow(
    *,
    manager: PptProductManager,
    requirement: ConfirmedRequirement,
    tool_context: ToolContext,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    content_plan_builder: Any | None,
) -> PptContentPlanningResult:
    """Run PPT content planning as an ADK Workflow node."""
    workflow = _build_ppt_content_planning_phase_workflow(
        manager=manager,
        expert_agents=expert_agents,
        app_name=app_name,
        artifact_service=artifact_service,
        content_plan_builder=content_plan_builder,
    )
    result = await tool_context.run_node(
        workflow,
        node_input={"requirement": requirement.model_dump(mode="json")},
        use_sub_branch=True,
    )
    return PptContentPlanningResult.model_validate(result or {})


def _build_ppt_content_planning_phase_workflow(
    *,
    manager: PptProductManager,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    content_plan_builder: Any | None,
) -> Workflow:
    """Build the ADK Workflow node for PPT content planning."""

    @node(name="PptContentPlanningPhaseNode", rerun_on_resume=True)
    async def run_content_planning(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Build the deck content plan without resolving assets."""
        requirement = ConfirmedRequirement.model_validate(node_input.get("requirement") or {})
        result = await manager._build_deck_content_plan_phase(
            requirement,
            tool_context=ctx,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
            content_plan_builder=content_plan_builder,
        )
        ctx.state[PPT_CONTENT_PLANNING_WORKFLOW_OUTPUT_KEY] = {
            "status": "success",
            "source": "adk_workflow",
            "page_count": len(result.content_plan.pages),
        }
        return result.model_dump(mode="json")

    return Workflow(
        name="PptContentPlanningPhaseWorkflow",
        description="Builds a PPT content plan as an explicit workflow phase.",
        edges=[("START", run_content_planning)],
    )


async def _run_ppt_asset_resolution_phase_workflow(
    *,
    manager: PptProductManager,
    content_plan: DeckContentPlan,
    requirement: ConfirmedRequirement,
    tool_context: ToolContext,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    asset_resolver: Any | None,
) -> PptAssetResolutionResult:
    """Run PPT asset resolution as an ADK Workflow node."""
    workflow = _build_ppt_asset_resolution_phase_workflow(
        manager=manager,
        expert_agents=expert_agents,
        app_name=app_name,
        artifact_service=artifact_service,
        asset_resolver=asset_resolver,
    )
    result = await tool_context.run_node(
        workflow,
        node_input={
            "content_plan": content_plan.model_dump(mode="json"),
            "requirement": requirement.model_dump(mode="json"),
        },
        use_sub_branch=True,
    )
    return PptAssetResolutionResult.model_validate(result or {})


def _build_ppt_asset_resolution_phase_workflow(
    *,
    manager: PptProductManager,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    asset_resolver: Any | None,
) -> Workflow:
    """Build the ADK Workflow node for PPT asset resolution."""

    @node(name="PptAssetResolutionPhaseNode", rerun_on_resume=True)
    async def run_asset_resolution(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Resolve generated and source-matched deck assets."""
        content_plan = DeckContentPlan.model_validate(node_input.get("content_plan") or {})
        requirement = ConfirmedRequirement.model_validate(node_input.get("requirement") or {})
        result = await manager._resolve_deck_assets_phase(
            content_plan,
            requirement,
            tool_context=ctx,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
            asset_resolver=asset_resolver,
        )
        ctx.state[PPT_ASSET_RESOLUTION_WORKFLOW_OUTPUT_KEY] = {
            "status": "success",
            "source": "adk_workflow",
            "ready_asset_count": int(result.resolved_asset_manifest.get("ready_asset_count") or 0),
            "reused_existing_resolution": result.reused_existing_resolution,
        }
        return result.model_dump(mode="json")

    return Workflow(
        name="PptAssetResolutionPhaseWorkflow",
        description="Resolves PPT deck assets as an explicit workflow phase.",
        edges=[("START", run_asset_resolution)],
    )


async def _run_ppt_route_execution_phase_workflow(
    *,
    manager: PptProductManager,
    requirement: ConfirmedRequirement,
    content_plan: DeckContentPlan,
    tool_context: ToolContext,
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    expert_agents: dict[str, BaseAgent],
) -> PptRouteExecutionResult:
    """Run PPT route execution as an ADK Workflow node."""
    workflow = _build_ppt_route_execution_phase_workflow(
        manager=manager,
        app_name=app_name,
        artifact_service=artifact_service,
        expert_agents=expert_agents,
    )
    result = await tool_context.run_node(
        workflow,
        node_input={
            "requirement": requirement.model_dump(mode="json"),
            "content_plan": content_plan.model_dump(mode="json"),
        },
        use_sub_branch=True,
    )
    return PptRouteExecutionResult.model_validate(result or {})


def _build_ppt_route_execution_phase_workflow(
    *,
    manager: PptProductManager,
    app_name: str,
    artifact_service: InMemoryArtifactService | None,
    expert_agents: dict[str, BaseAgent],
) -> Workflow:
    """Build the ADK Workflow node for PPT route execution."""

    @node(name="PptRouteExecutionPhaseNode", rerun_on_resume=True)
    async def run_route_execution(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Render the selected built-in route."""
        requirement = ConfirmedRequirement.model_validate(node_input.get("requirement") or {})
        content_plan = DeckContentPlan.model_validate(node_input.get("content_plan") or {})
        result = await manager._execute_ppt_route_phase(
            requirement=requirement,
            content_plan=content_plan,
            tool_context=ctx,
            app_name=app_name,
            artifact_service=artifact_service,
            expert_agents=expert_agents,
        )
        ctx.state[PPT_ROUTE_EXECUTION_WORKFLOW_OUTPUT_KEY] = {
            "status": "success" if result.route_build.pptx_path else "generation_failed",
            "source": "adk_workflow",
            "route": result.route,
            "reused_existing_build": result.reused_existing_build,
        }
        return result.model_dump(mode="json")

    return Workflow(
        name="PptRouteExecutionPhaseWorkflow",
        description="Executes a PPT built-in route as an explicit workflow phase.",
        edges=[("START", run_route_execution)],
    )


async def _run_ppt_final_delivery_phase_workflow(
    *,
    manager: PptProductManager,
    requirement: ConfirmedRequirement,
    content_plan: DeckContentPlan,
    route_execution: PptRouteExecutionResult,
    tool_context: ToolContext,
    after_confirmation: bool,
) -> PptFinalDeliveryResult:
    """Run PPT route final delivery as an ADK Workflow node."""
    workflow = _build_ppt_final_delivery_phase_workflow(manager=manager)
    result = await tool_context.run_node(
        workflow,
        node_input={
            "requirement": requirement.model_dump(mode="json"),
            "content_plan": content_plan.model_dump(mode="json"),
            "route_execution": route_execution.model_dump(mode="json"),
            "after_confirmation": after_confirmation,
        },
        use_sub_branch=True,
    )
    return PptFinalDeliveryResult.model_validate(result or {})


def _build_ppt_final_delivery_phase_workflow(*, manager: PptProductManager) -> Workflow:
    """Build the ADK Workflow node for PPT route final delivery."""

    @node(name="PptFinalDeliveryPhaseNode", rerun_on_resume=True)
    async def run_final_delivery(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Register route artifacts and assemble the final product result."""
        requirement = ConfirmedRequirement.model_validate(node_input.get("requirement") or {})
        content_plan = DeckContentPlan.model_validate(node_input.get("content_plan") or {})
        route_execution = PptRouteExecutionResult.model_validate(node_input.get("route_execution") or {})
        result = manager._finalize_route_delivery_phase(
            requirement=requirement,
            content_plan=content_plan,
            route_execution=route_execution,
            tool_context=ctx,
            after_confirmation=bool(node_input.get("after_confirmation")),
        )
        ctx.state[PPT_FINAL_DELIVERY_WORKFLOW_OUTPUT_KEY] = {
            "status": result.product_result.status,
            "source": "adk_workflow",
            "final_pptx": result.delivery_manifest.final_pptx,
        }
        return result.model_dump(mode="json")

    return Workflow(
        name="PptFinalDeliveryPhaseWorkflow",
        description="Packages PPT route delivery as an explicit workflow phase.",
        edges=[("START", run_final_delivery)],
    )


async def _run_ppt_private_skill_execution_phase_workflow(
    *,
    manager: PptProductManager,
    requirement: ConfirmedRequirement,
    content_plan: DeckContentPlan,
    system_selection: dict[str, Any],
    tool_context: ToolContext,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: BaseArtifactService | None,
) -> PptPrivateSkillExecutionResult:
    """Run PPT private-skill execution as an ADK Workflow node."""
    workflow = _build_ppt_private_skill_execution_phase_workflow(
        manager=manager,
        expert_agents=expert_agents,
        app_name=app_name,
        artifact_service=artifact_service,
    )
    result = await tool_context.run_node(
        workflow,
        node_input={
            "requirement": requirement.model_dump(mode="json"),
            "content_plan": content_plan.model_dump(mode="json"),
            "system_selection": copy.deepcopy(system_selection),
        },
        use_sub_branch=True,
    )
    return PptPrivateSkillExecutionResult.model_validate(result or {})


def _build_ppt_private_skill_execution_phase_workflow(
    *,
    manager: PptProductManager,
    expert_agents: dict[str, BaseAgent],
    app_name: str,
    artifact_service: BaseArtifactService | None,
) -> Workflow:
    """Build the ADK Workflow node for PPT private-skill execution."""

    @node(name="PptPrivateSkillExecutionPhaseNode", rerun_on_resume=True)
    async def run_private_skill_execution(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Execute the selected private PPT skill as an explicit workflow phase."""
        requirement = ConfirmedRequirement.model_validate(node_input.get("requirement") or {})
        content_plan = DeckContentPlan.model_validate(node_input.get("content_plan") or {})
        system_selection = dict(node_input.get("system_selection") or {})
        result = await manager._execute_private_ppt_skill_phase(
            requirement=requirement,
            content_plan=content_plan,
            system_selection=system_selection,
            tool_context=ctx,
            expert_agents=expert_agents,
            app_name=app_name,
            artifact_service=artifact_service,
        )
        ctx.state[PPT_PRIVATE_SKILL_EXECUTION_WORKFLOW_OUTPUT_KEY] = {
            "status": str(result.execution_output.get("status") or result.private_build.get("status") or ""),
            "source": "adk_workflow",
            "skill_name": result.skill_name,
            "output_format": result.output_format,
        }
        return result.model_dump(mode="json")

    return Workflow(
        name="PptPrivateSkillExecutionPhaseWorkflow",
        description="Executes a PPT private skill as an explicit workflow phase.",
        edges=[("START", run_private_skill_execution)],
    )


async def _run_ppt_private_skill_delivery_phase_workflow(
    *,
    manager: PptProductManager,
    requirement: ConfirmedRequirement,
    content_plan: DeckContentPlan,
    system_selection: dict[str, Any],
    private_build: dict[str, Any],
    tool_context: ToolContext,
) -> PptPrivateSkillDeliveryResult:
    """Run PPT private-skill final delivery as an ADK Workflow node."""
    workflow = _build_ppt_private_skill_delivery_phase_workflow(manager=manager)
    result = await tool_context.run_node(
        workflow,
        node_input={
            "requirement": requirement.model_dump(mode="json"),
            "content_plan": content_plan.model_dump(mode="json"),
            "system_selection": copy.deepcopy(system_selection),
            "private_build": copy.deepcopy(private_build),
        },
        use_sub_branch=True,
    )
    return PptPrivateSkillDeliveryResult.model_validate(result or {})


def _build_ppt_private_skill_delivery_phase_workflow(*, manager: PptProductManager) -> Workflow:
    """Build the ADK Workflow node for PPT private-skill final delivery."""

    @node(name="PptPrivateSkillDeliveryPhaseNode", rerun_on_resume=True)
    async def run_private_skill_delivery(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        """Assemble the final product result for a private-skill artifact."""
        requirement = ConfirmedRequirement.model_validate(node_input.get("requirement") or {})
        content_plan = DeckContentPlan.model_validate(node_input.get("content_plan") or {})
        system_selection = dict(node_input.get("system_selection") or {})
        private_build = dict(node_input.get("private_build") or {})
        result = manager._finalize_private_skill_delivery_phase(
            requirement=requirement,
            content_plan=content_plan,
            system_selection=system_selection,
            private_build=private_build,
            tool_context=ctx,
        )
        ctx.state[PPT_PRIVATE_SKILL_DELIVERY_WORKFLOW_OUTPUT_KEY] = {
            "status": result.product_result.status,
            "source": "adk_workflow",
            "artifact_type": str(private_build.get("artifact_type") or ""),
            "output_path": str(private_build.get("output_path") or ""),
        }
        return result.model_dump(mode="json")

    return Workflow(
        name="PptPrivateSkillDeliveryPhaseWorkflow",
        description="Packages PPT private-skill delivery as an explicit workflow phase.",
        edges=[("START", run_private_skill_delivery)],
    )


def _normalize_ppt_final_file_paths(paths: list[Any]) -> list[str]:
    """Normalize final PPT product file paths into workspace-relative strings."""
    normalized: list[str] = []
    for path in paths:
        clean_path = str(path or "").strip()
        if not clean_path:
            continue
        try:
            normalized_path = workspace_relative_path(clean_path)
        except Exception:
            continue
        if normalized_path and normalized_path not in normalized:
            normalized.append(normalized_path)
    return normalized
