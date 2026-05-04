"""ADK-native product manager skeleton for Creative Claw PPT tasks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent, SequentialAgent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.tools.tool_context import ToolContext
from pydantic import PrivateAttr

from conf.llm import build_llm
from conf.path import PROJECT_PATH
from src.productions.ppt.planning import PptContentPlanner
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
from src.runtime.workspace import (
    build_workspace_file_record,
    generated_session_dir,
    resolve_workspace_path,
    workspace_relative_path,
)

PPT_CONFIRMED_REQUIREMENT_STATE_KEY = "ppt_confirmed_requirement"
PPT_PRODUCT_RESULT_STATE_KEY = "ppt_product_result"


class PptProductManager(LlmAgent):
    """ADK LlmAgent that owns PPT product-line requests."""

    _project_root: Path = PrivateAttr()
    _content_planner: PptContentPlanner = PrivateAttr()
    _route_registry: dict[str, PptRouteRegistration] = PrivateAttr(default_factory=dict)

    def __init__(
        self,
        project_root: str | Path | None = None,
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
            **kwargs,
        )
        self.project_root = Path(project_root or PROJECT_PATH).resolve()
        self._content_planner = PptContentPlanner()
        self._route_registry = dict(route_registry or build_default_ppt_route_registry())
        if provided_tools is None:
            self.tools = [self.dispatch_ppt_route]

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
- Dispatch exactly one route pipeline per task.
- Prefer the HTML route first for the MVP, then add SVG and XML as separate route pipelines.
- Do not expose route-internal editing tools at the top product-manager layer.

# Route policy
- HTML route: first route to implement; system HTML templates only; export to PPTX with explicit editability caveats.
- SVG route: later route for high-control SVG pages and SVG-to-PPTX.
- XML route: later route for user-uploaded PPTX templates and native OOXML editing.

# Result policy
Return structured status, current phase, selected route, warnings, next actions, and delivery manifest. Do not claim PPTX generation succeeded unless a route pipeline produced and validated a file.
""".strip()

    def build_agent(self, *, tools: list[Any] | None = None) -> LlmAgent:
        """Return this product manager as the ADK LlmAgent instance."""
        if tools is not None:
            self.tools = tools
        return self

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
        )
        return SequentialAgent(
            name="PptHtmlMvpSequentialAgent",
            sub_agents=[requirement_agent, content_agent, quality_agent],
        )

    async def run_product_request(
        self,
        *,
        task: str,
        inputs: list[Any] | None = None,
        output: dict[str, Any] | None = None,
        tool_context: ToolContext | None = None,
        expert_agents: dict[str, BaseAgent] | None = None,
        app_name: str = "creative_claw",
        artifact_service: InMemoryArtifactService | None = None,
        source_converter: Any | None = None,
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
        try:
            source_inputs = self._normalize_source_inputs(inputs or [])
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
            requirement = self.prepare_confirmed_requirement(
                task=clean_task,
                inputs=inputs or [],
                output=output_options,
                source_understanding=source_materials,
            )
            clarification_questions = self.validate_confirmed_requirement(requirement)
            route_registration = self._route_registry.get(requirement.route)
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
            elif route_registration is None or not route_registration.implemented:
                result = self._build_route_not_implemented_result(requirement, route_registration)
            else:
                content_plan = self.build_initial_deck_content_plan(requirement)
                output_dir = self._build_route_output_dir(tool_context.state)
                route_build = self._dispatch_ppt_route(
                    requirement=requirement,
                    content_plan=content_plan,
                    output_dir=output_dir,
                )
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
                    status="success",
                    phase="html_route_delivery",
                    message="HTML route MVP generated an HTML deck, PNG previews, and an editable PPTX.",
                    selected_route=requirement.route,
                    confirmed_requirement=requirement,
                    deck_content_plan=content_plan,
                    route_build=route_build,
                    quality_review=QualityReviewResult(
                        status="pass",
                        page_count_ok=True,
                        file_open_ok=True,
                        text_complete_ok=True,
                        assets_ok=True,
                        placeholder_free_ok=True,
                        overflow_ok=None,
                        style_consistency_ok=True,
                    ),
                    delivery_manifest=delivery_manifest,
                    output_files=output_files,
                    warnings=[
                        *list(route_build.warnings),
                        *list(requirement.source_understanding.extraction_warnings),
                    ],
                    next_actions=["Review the generated PPTX and previews; improve HTML template fidelity next."],
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

    def build_initial_deck_content_plan(self, requirement: ConfirmedRequirement) -> DeckContentPlan:
        """Build a template-independent content plan for the HTML MVP."""
        return self.content_planner.build_plan(requirement)

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
        route_build = self._dispatch_ppt_route(
            requirement=requirement,
            content_plan=content_plan,
            output_dir=self._build_route_output_dir(tool_context.state),
        )
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
            "status": "success",
            "selected_route": requirement.route,
            "route_build": route_build.model_dump(mode="json"),
            "output_files": output_files,
        }
        tool_context.state["ppt_route_build"] = payload["route_build"]
        return payload

    def _dispatch_ppt_route(
        self,
        *,
        requirement: ConfirmedRequirement,
        content_plan: DeckContentPlan,
        output_dir: Path,
    ) -> Any:
        """Dispatch one confirmed PPT request to the registered route workflow."""
        registration = self._route_registry.get(requirement.route)
        if registration is None:
            raise ValueError(f"Unknown PPT route `{requirement.route}`.")
        if not registration.implemented or registration.handler is None:
            raise NotImplementedError(f"PPT route `{requirement.route}` is not implemented.")
        template_id = requirement.template_requirement.template_id or "clean_business"
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
    def _record_output_files(
        state: dict[str, Any],
        paths: list[str],
    ) -> list[dict[str, Any]]:
        """Record PPT product output files in session state."""
        current_turn = int(state.get("turn_index", 0) or 0)
        current_step = int(state.get("step", 0) or 0)
        records = [
            build_workspace_file_record(
                path,
                description="PPT product HTML route artifact.",
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
        final_pptx = next((record["path"] for record in records if record["path"].endswith(".pptx")), "")
        if final_pptx:
            state["final_file_paths"] = [final_pptx]
        return records

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
        inputs: list[Any] | None = None,
        output: dict[str, Any] | None = None,
        source_understanding: SourceUnderstanding | None = None,
    ) -> ConfirmedRequirement:
        """Create the first deterministic ConfirmedRequirement draft."""
        clean_task = str(task or "").strip()
        if not clean_task:
            raise ValueError("task is required")

        output_options = dict(output or {})
        route, route_confirmed = self._select_route(clean_task, output_options)
        source_inputs = self._normalize_source_inputs(inputs or [])
        reference_assets = self._normalize_reference_assets(inputs or [])
        slide_policy = self._infer_slide_count_policy(clean_task)
        source_understanding = source_understanding or SourceUnderstanding(
            document_type=self._infer_document_type(source_inputs),
        )

        return ConfirmedRequirement(
            route=route,
            topic=self._infer_topic(clean_task),
            audience="",
            scenario="",
            slide_count_policy=slide_policy,
            language=self._infer_language(clean_task),
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
            style_requirement=StyleRequirement(style_keywords=self._infer_style_keywords(clean_task)),
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
    def _normalize_source_inputs(inputs: list[Any]) -> list[SourceInput]:
        """Normalize raw tool inputs into SourceInput records."""
        source_inputs: list[SourceInput] = []
        for index, item in enumerate(inputs, start=1):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or item.get("type") or "source").strip() or "source"
            if role == "reference":
                continue
            source_inputs.append(
                SourceInput(
                    name=str(item.get("name") or item.get("filename") or f"input_{index}"),
                    path=str(item.get("path") or item.get("url") or ""),
                    mime_type=str(item.get("mime_type") or item.get("mime") or ""),
                    role=role,
                    description=str(item.get("description") or ""),
                )
            )
        return source_inputs

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
                    path=str(item.get("path") or item.get("url") or ""),
                    asset_type=str(item.get("asset_type") or item.get("mime_type") or ""),
                    role="reference",
                    description=str(item.get("description") or ""),
                )
            )
        return reference_assets

    @staticmethod
    def _infer_slide_count_policy(task: str) -> SlideCountPolicy:
        """Infer a conservative slide count policy from the task text."""
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
        """Use the concise task text as the initial topic."""
        return task[:120].strip()

    @staticmethod
    def _infer_language(task: str) -> str:
        """Infer output language from the task text."""
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
        if route == "html":
            return TemplateRequirement(
                use_template=True,
                template_source="system",
                template_id=template_id,
                notes="HTML route uses system templates only.",
            )
        if route == "svg":
            return TemplateRequirement(use_template=False, template_source="none", notes="SVG route can later opt into system SVG templates.")
        return TemplateRequirement()

    @staticmethod
    def _infer_style_keywords(task: str) -> list[str]:
        """Extract a small set of style keywords from common request language."""
        style_keywords: list[str] = []
        keyword_map = {
            "business": ("商务", "汇报", "executive", "business"),
            "editorial": ("杂志", "editorial", "magazine"),
            "academic": ("学术", "答辩", "academic"),
            "minimal": ("极简", "minimal"),
            "playful": ("活泼", "playful"),
        }
        normalized = task.lower()
        for label, keywords in keyword_map.items():
            if any(keyword in normalized for keyword in keywords):
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
