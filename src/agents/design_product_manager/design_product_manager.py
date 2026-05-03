"""Resource-aware DesignProductManager for Creative Claw design tasks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools.tool_context import ToolContext

from conf.llm import build_llm
from conf.path import PROJECT_PATH
from src.agents.design_product_manager.schema_validation import (
    validate_design_brief_contract,
    validate_design_result_contract,
)
from src.agents.design_product_manager.validation import validate_design_artifacts
from src.runtime.code_artifacts import generate_code_artifact
from src.runtime.workspace import build_workspace_file_record

_RESOURCE_ROOT = Path("skills/design-knowledge-and-skills")
_MANIFEST_PATH = _RESOURCE_ROOT / "resource-manifest.json"
DESIGN_BRIEF_SCHEMA_VERSION = "design-brief-v1"
DESIGN_RESULT_SCHEMA_VERSION = "design-product-result-v1"

_SURFACE_KEYWORDS = {
    "dashboard": (
        "dashboard",
        "analytics",
        "admin",
        "console",
        "operation",
        "运营",
        "数据",
        "看板",
        "指标",
        "后台",
        "工作台",
    ),
    "landing_page": (
        "landing",
        "homepage",
        "marketing",
        "pricing",
        "saas",
        "落地页",
        "官网",
        "首页",
        "营销页",
        "定价页",
        "价格页",
    ),
    "docs_page": (
        "docs",
        "documentation",
        "api docs",
        "developer docs",
        "knowledge base",
        "文档",
        "开发者文档",
    ),
    "mobile_app": (
        "mobile",
        "app",
        "ios",
        "android",
        "phone",
        "移动",
        "手机",
        "小程序",
    ),
    "deck": (
        "deck",
        "slide",
        "presentation",
        "readout",
        "ppt",
        "演示",
        "汇报",
        "周报",
    ),
    "social_carousel": (
        "carousel",
        "social",
        "instagram",
        "xiaohongshu",
        "小红书",
        "社媒",
        "轮播",
        "长图",
        "卡片",
    ),
    "poster": (
        "poster",
        "magazine poster",
        "editorial poster",
        "launch poster",
        "海报",
        "杂志海报",
        "活动海报",
    ),
    "wireframe": (
        "wireframe",
        "low fidelity",
        "lofi",
        "ux sketch",
        "线框图",
        "低保真",
        "草图",
    ),
}

_DEFAULT_FRAME_BY_SURFACE = {
    "dashboard": "browser-chrome",
    "docs_page": "browser-chrome",
    "landing_page": "browser-chrome",
    "mobile_app": "iphone-15-pro",
    "wireframe": "browser-chrome",
}


@dataclass(frozen=True, slots=True)
class DesignResourceSelection:
    """Selected resources for one design task."""

    surface: str
    brief_schema_id: str
    task_skill: str
    design_system: str
    device_frame: str
    context_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DesignProductBrief:
    """Prepared design brief plus design artifact generation request."""

    user_prompt: str
    selection: DesignResourceSelection
    questions: tuple[dict[str, str], ...]
    missing_fields: tuple[str, ...]
    assumptions: dict[str, Any]
    design_brief: dict[str, Any]
    needs_clarification: bool
    generation_prompt: str
    code_generation_request: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation for tool responses and tests."""
        return {
            "user_prompt": self.user_prompt,
            "selection": {
                "surface": self.selection.surface,
                "brief_schema_id": self.selection.brief_schema_id,
                "task_skill": self.selection.task_skill,
                "design_system": self.selection.design_system,
                "device_frame": self.selection.device_frame,
                "context_files": list(self.selection.context_files),
            },
            "questions": list(self.questions),
            "missing_fields": list(self.missing_fields),
            "assumptions": self.assumptions,
            "design_brief": self.design_brief,
            "needs_clarification": self.needs_clarification,
            "generation_prompt": self.generation_prompt,
            "code_generation_request": self.code_generation_request,
        }


class DesignProductManager:
    """Select design resources and prepare code-backed design generation tasks."""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root or PROJECT_PATH).resolve()
        self.resource_root = self.project_root / _RESOURCE_ROOT
        self._manifest: dict[str, Any] | None = None

    def build_agent(self, *, tools: list[Any] | None = None) -> LlmAgent:
        """Build the LLM-facing DesignProductManager agent shell."""
        return LlmAgent(
            name="DesignProductManager",
            model=build_llm(),
            instruction=self.build_instruction(),
            tools=tools or [],
        )

    def build_instruction(self) -> str:
        """Return the product-manager prompt used by the LLM agent shell."""
        return """
You are Creative Claw's DesignProductManager.

# Role
Turn a user's design request into one focused, resource-grounded production brief.

# Discovery
- Search design-knowledge-and-skills before choosing resources.
- Read the matching brief-elements schema before asking questions.
- Ask scenario-specific missing design elements when the brief is not executable.
- If the user asks to proceed without clarification, use explicit assumptions from the selected schema defaults.

# Resource selection
- Select exactly one primary task skill.
- Select at most one primary design system.
- Add a device frame only when it helps the requested surface.
- Do not use runtime-disabled or reference-only resources for execution context.

# Capability orchestration
- Own code-backed design artifacts such as dashboards, landing pages, mobile prototypes, social carousels, and HTML decks.
- Use deterministic bottom capabilities for file inspection, generation, editing, validation, and future export work.

# Result policy
- Keep the final brief concrete enough for a coding agent to generate one runnable artifact.
- Interpret generation, editing, and validation outputs into DesignProductManager statuses and next actions.
""".strip()

    async def run(
        self,
        *,
        task: str,
        inputs: list[Any] | None = None,
        output: dict[str, Any] | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Run one autonomous design product task from request to result."""
        if tool_context is None:
            return {
                "status": "error",
                "message": "DesignProductManager requires tool context.",
            }

        clean_task = str(task or "").strip()
        if not clean_task:
            return {
                "status": "error",
                "message": "DesignProductManager requires a non-empty task.",
            }

        output_options = dict(output or {})
        output_format = str(output_options.get("format") or output_options.get("output_format") or "html")
        output_path = str(output_options.get("path") or output_options.get("output_path") or "")
        allow_assumptions = self._should_allow_assumptions(
            task=clean_task,
            inputs=inputs or [],
            output=output_options,
        )

        try:
            brief = self.prepare_brief(
                prompt=clean_task,
                output_format=output_format,
                allow_assumptions=allow_assumptions,
                output_path=output_path,
            )
        except Exception as exc:
            return {
                "status": "error",
                "message": f"DesignProductManager failed to prepare the brief: {type(exc).__name__}: {exc}",
            }

        tool_context.state["design_product_brief"] = brief.to_dict()
        if brief.needs_clarification:
            result = self.build_clarification_result(brief)
            tool_context.state["design_product_result"] = result
            return result

        code_generation_result = await self.generate_design_artifact(
            tool_context,
            brief=brief,
            inputs=inputs or [],
            output=output_options,
        )
        output_paths = [
            str(file_info.get("path", "")).strip()
            for file_info in code_generation_result.get("output_files", []) or []
            if isinstance(file_info, dict) and str(file_info.get("path", "")).strip()
        ]
        design_validation = self.validate_generated_artifacts(
            output_paths,
            browser_preview=bool(output_options.get("browser_preview_validation", False)),
        )
        result = self.build_generation_result(
            brief=brief,
            code_generation_result=code_generation_result,
            design_validation=design_validation,
        )
        tool_context.state["design_product_result"] = result
        return result

    async def generate_design_artifact(
        self,
        runtime_context: Any,
        *,
        brief: DesignProductBrief,
        inputs: list[Any] | None = None,
        output: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate one code-backed design artifact for a prepared design brief."""
        request = dict(brief.code_generation_request)
        output_options = dict(output or {})
        context_files = list(request.get("context_files") or [])
        context_files.extend(self._input_context_files(inputs or []))
        result = await generate_code_artifact(
            runtime_context,
            prompt=str(request.get("prompt", "") or ""),
            language=str(request.get("language", "") or "html"),
            output_path=str(output_options.get("path") or output_options.get("output_path") or request.get("output_path") or ""),
            context_files=context_files,
            constraints=[str(item) for item in request.get("constraints", []) or []],
            output_type="design",
            output_description="Design artifact generated by DesignProductManager.",
            output_source="design_product_manager",
        )
        if str(result.get("status", "")).strip().lower() == "success":
            result["output_files"] = self._record_output_files(
                getattr(runtime_context, "state", {}),
                list(result.get("output_files") or []),
            )
        return result

    def prepare_brief(
        self,
        *,
        prompt: str,
        scenario: str = "",
        output_format: str = "html",
        allow_assumptions: bool = True,
        design_system: str = "",
        task_skill: str = "",
        device_frame: str = "",
        output_path: str = "",
        max_questions: int = 6,
    ) -> DesignProductBrief:
        """Prepare one resource-grounded design brief and code generation request."""
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise ValueError("DesignProductManager requires a non-empty prompt.")

        manifest = self._load_manifest()
        brief_resource = self._select_brief_resource(manifest, prompt=clean_prompt, scenario=scenario)
        brief_schema = self._load_resource_json(str(brief_resource["path"]))
        defaults = dict(brief_schema.get("defaults") or brief_resource.get("defaults") or {})
        surface = str(brief_schema.get("surface") or brief_resource.get("surface") or "dashboard").strip()

        selected_task_skill = self._select_task_skill(
            manifest,
            prompt=clean_prompt,
            override=task_skill,
            defaults=defaults,
        )
        selected_design_system = self._select_design_system(
            manifest,
            prompt=clean_prompt,
            override=design_system,
            defaults=defaults,
        )
        selected_device_frame = self._select_device_frame(
            manifest,
            prompt=clean_prompt,
            override=device_frame,
            defaults=defaults,
            surface=surface,
        )
        context_files = self._build_context_files(
            brief_resource=brief_resource,
            task_skill=selected_task_skill,
            design_system=selected_design_system,
            device_frame=selected_device_frame,
            manifest=manifest,
        )
        questions = tuple(self._select_questions(brief_schema, max_questions=max_questions))
        missing_fields = tuple(str(field) for field in brief_schema.get("required_fields", []) if str(field).strip())
        needs_clarification = bool(not allow_assumptions and questions)
        assumptions = self._build_assumptions(
            defaults=defaults,
            selected_task_skill=selected_task_skill,
            selected_design_system=selected_design_system,
            selected_device_frame=selected_device_frame,
        )
        design_brief = self._build_design_brief(
            user_prompt=clean_prompt,
            surface=surface,
            brief_schema=brief_schema,
            assumptions=assumptions,
            selected_task_skill=selected_task_skill,
            selected_design_system=selected_design_system,
            selected_device_frame=selected_device_frame,
            output_format=output_format,
        )
        validate_design_brief_contract(design_brief, project_root=self.project_root)
        generation_prompt = self._build_generation_prompt(
            user_prompt=clean_prompt,
            surface=surface,
            brief_schema=brief_schema,
            assumptions=assumptions,
            design_brief=design_brief,
            questions=questions,
            needs_clarification=needs_clarification,
        )
        code_generation_request = {
            "prompt": generation_prompt,
            "language": self._normalize_output_format(output_format),
            "output_path": str(output_path or "").strip(),
            "context_files": list(context_files),
            "constraints": self._build_constraints(surface=surface, output_format=output_format),
            "design_brief": design_brief,
        }

        return DesignProductBrief(
            user_prompt=clean_prompt,
            selection=DesignResourceSelection(
                surface=surface,
                brief_schema_id=str(brief_schema.get("id") or brief_resource.get("id") or ""),
                task_skill=selected_task_skill,
                design_system=selected_design_system,
                device_frame=selected_device_frame,
                context_files=context_files,
            ),
            questions=questions,
            missing_fields=missing_fields,
            assumptions=assumptions,
            design_brief=design_brief,
            needs_clarification=needs_clarification,
            generation_prompt=generation_prompt,
            code_generation_request=code_generation_request,
        )

    def build_clarification_result(self, brief: DesignProductBrief) -> dict[str, Any]:
        """Build a structured result when the design brief needs user input."""
        brief_payload = brief.to_dict()
        result = {
            "result_schema_version": DESIGN_RESULT_SCHEMA_VERSION,
            "status": "needs_clarification",
            "message": "Design brief needs user clarification before generation.",
            "brief": brief_payload,
            "resource_selection": brief_payload["selection"],
            "questions": brief_payload["questions"],
            "missing_fields": brief_payload["missing_fields"],
            "design_issues": [
                {
                    "source": "brief",
                    "severity": "info",
                    "message": "Missing scenario-specific design elements should be answered before generation.",
                }
            ],
            "next_action": "ask_user",
            "code_generation": None,
            "design_validation": [],
            "output_files": [],
        }
        validate_design_result_contract(result, project_root=self.project_root)
        return result

    def build_generation_result(
        self,
        *,
        brief: DesignProductBrief,
        code_generation_result: dict[str, Any],
        design_validation: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Interpret code-generation and artifact-validation results for the Design product line."""
        brief_payload = brief.to_dict()
        output_files = list(code_generation_result.get("output_files") or [])
        design_issues = self._build_design_issues(
            code_generation_result=code_generation_result,
            design_validation=design_validation,
        )
        status, next_action = self._select_result_status_and_action(
            code_generation_result=code_generation_result,
            output_files=output_files,
            design_issues=design_issues,
        )
        result = {
            "result_schema_version": DESIGN_RESULT_SCHEMA_VERSION,
            "status": status,
            "message": self._build_result_message(
                status=status,
                code_generation_result=code_generation_result,
                design_issues=design_issues,
            ),
            "brief": brief_payload,
            "resource_selection": brief_payload["selection"],
            "code_generation": code_generation_result,
            "design_validation": design_validation,
            "design_issues": design_issues,
            "output_files": output_files,
            "next_action": next_action,
        }
        validate_design_result_contract(result, project_root=self.project_root)
        return result

    def validate_generated_artifacts(
        self,
        output_paths: list[str],
        *,
        browser_preview: bool = False,
    ) -> list[dict[str, Any]]:
        """Run the current narrow artifact validation checks for generated design files."""
        return validate_design_artifacts(output_paths, browser_preview=browser_preview)

    @staticmethod
    def _should_allow_assumptions(
        *,
        task: str,
        inputs: list[Any],
        output: dict[str, Any],
    ) -> bool:
        """Decide whether the task is concrete enough to proceed without clarification."""
        explicit = output.get("allow_assumptions")
        if isinstance(explicit, bool):
            return explicit

        normalized_task = DesignProductManager._normalize_match_text(task)
        proceed_markers = (
            "直接做",
            "先做",
            "不用问",
            "不需要澄清",
            "按默认",
            "use defaults",
            "make assumptions",
            "proceed",
        )
        if any(marker in normalized_task for marker in proceed_markers):
            return True
        if inputs:
            return True

        concrete_markers = (
            "展示",
            "包含",
            "用于",
            "突出",
            "dau",
            "gmv",
            "roi",
            "conversion",
            "retention",
            "pricing",
            "deck",
            "landing",
        )
        if any(marker in normalized_task for marker in concrete_markers):
            return True
        return len(normalized_task) >= 40

    @staticmethod
    def _input_context_files(inputs: list[Any]) -> list[str]:
        """Extract workspace file paths from concise DesignProductManager inputs."""
        context_files: list[str] = []
        for item in inputs:
            if isinstance(item, str) and item.strip():
                context_files.append(item.strip())
                continue
            if not isinstance(item, dict):
                continue
            for key in ("path", "input_path", "workspace_path"):
                value = str(item.get(key, "") or "").strip()
                if value:
                    context_files.append(value)
                    break
        return context_files

    @staticmethod
    def _record_output_files(
        state: Any,
        output_files: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Record DesignProductManager output files in session state."""
        if not output_files:
            return []

        current_turn = int(state.get("turn_index", 0) or 0)
        current_step = int(state.get("step", 0) or 0)
        current_expert_step = int(state.get("expert_step", 0) or 0)
        file_records: list[dict[str, Any]] = []
        for file_info in output_files:
            path = str(file_info.get("path", "") or "").strip()
            if not path:
                continue
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
        if not file_records:
            return []

        generated = list(state.get("generated") or [])
        generated.extend(file_records)
        state["generated"] = generated
        history = list(state.get("files_history", []))
        history.append(file_records)
        state["new_files"] = file_records
        state["files_history"] = history
        return file_records

    def _load_manifest(self) -> dict[str, Any]:
        """Load the design resource manifest once per manager instance."""
        if self._manifest is not None:
            return self._manifest
        manifest_path = self.project_root / _MANIFEST_PATH
        self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return self._manifest

    def _load_resource_json(self, resource_path: str) -> dict[str, Any]:
        """Read one JSON resource under design-knowledge-and-skills."""
        path = (self.resource_root / resource_path).resolve()
        path.relative_to(self.resource_root)
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _resources_by_type(manifest: dict[str, Any], resource_type: str) -> list[dict[str, Any]]:
        """Return runtime-enabled resources of one manifest type."""
        return [
            resource
            for resource in list(manifest.get("resources") or [])
            if (
                resource.get("type") == resource_type
                and resource.get("runtimeEnabled", True)
                and not resource.get("referenceOnly", False)
            )
        ]

    def _select_brief_resource(
        self,
        manifest: dict[str, Any],
        *,
        prompt: str,
        scenario: str,
    ) -> dict[str, Any]:
        """Select the most relevant brief element schema."""
        brief_resources = self._resources_by_type(manifest, "brief_element_schema")
        normalized_text = self._normalize_match_text(f"{scenario} {prompt}")
        matched_resource = self._best_matching_resource(brief_resources, normalized_text)
        if matched_resource:
            return matched_resource
        explicit_surface = self._detect_surface(normalized_text)
        for resource in brief_resources:
            if str(resource.get("surface", "")).strip() == explicit_surface:
                return resource
        return next(
            (
                resource
                for resource in brief_resources
                if str(resource.get("surface", "")).strip() == "dashboard"
            ),
            brief_resources[0],
        )

    @staticmethod
    def _build_design_issues(
        *,
        code_generation_result: dict[str, Any],
        design_validation: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return DesignProductManager-owned issues from bottom capability results."""
        issues: list[dict[str, Any]] = []
        if str(code_generation_result.get("status", "")).strip().lower() != "success":
            issues.append(
                {
                    "source": "code_generation",
                    "severity": "error",
                    "message": str(code_generation_result.get("message", "") or "Code generation failed."),
                    "error_type": str(code_generation_result.get("error_type", "") or ""),
                    "retryable": bool(code_generation_result.get("retryable", False)),
                }
            )

        for validation in design_validation:
            path = str(validation.get("path", "") or "")
            for message in validation.get("errors", []) or []:
                issues.append(
                    {
                        "source": "design_validation",
                        "severity": "error",
                        "path": path,
                        "message": str(message),
                    }
                )
            for message in validation.get("warnings", []) or []:
                issues.append(
                    {
                        "source": "design_validation",
                        "severity": "warning",
                        "path": path,
                        "message": str(message),
                    }
                )
        return issues

    @staticmethod
    def _select_result_status_and_action(
        *,
        code_generation_result: dict[str, Any],
        output_files: list[dict[str, Any]],
        design_issues: list[dict[str, Any]],
    ) -> tuple[str, str]:
        """Return product-line result status and recommended next action."""
        code_status = str(code_generation_result.get("status", "")).strip().lower()
        if code_status != "success":
            if bool(code_generation_result.get("retryable", False)):
                return "generation_failed", "user_can_retry_generation"
            return "generation_failed", "inspect_generation_setup"
        if not output_files:
            return "generation_failed", "inspect_generation_result"

        has_validation_error = any(
            issue["source"] == "design_validation" and issue["severity"] == "error"
            for issue in design_issues
        )
        if has_validation_error:
            return "validation_failed", "user_can_request_regeneration"

        has_warning = any(issue["severity"] == "warning" for issue in design_issues)
        if has_warning:
            return "warning", "review_validation_warnings"

        return "success", "deliver_artifact"

    @staticmethod
    def _build_result_message(
        *,
        status: str,
        code_generation_result: dict[str, Any],
        design_issues: list[dict[str, Any]],
    ) -> str:
        """Build a concise product-line message from result status."""
        if status == "success":
            return str(code_generation_result.get("message", "") or "Design artifact generated.")
        if status == "warning":
            return "Design artifact generated with validation warnings."
        if status == "validation_failed":
            return "Design artifact was generated but failed basic artifact validation."
        first_issue = design_issues[0]["message"] if design_issues else "Design generation failed."
        return str(first_issue)

    def _select_task_skill(
        self,
        manifest: dict[str, Any],
        *,
        prompt: str,
        override: str,
        defaults: dict[str, Any],
    ) -> str:
        """Select one task skill from override, prompt match, or schema defaults."""
        task_skills = self._resources_by_type(manifest, "task_skill")
        return self._select_resource_slug(
            task_skills,
            prompt=prompt,
            override=override,
            candidates=[
                str(defaults.get("recommended_skill") or "").strip(),
                str(defaults.get("fallback_skill") or "").strip(),
            ],
            fallback="web-prototype",
        )

    def _select_design_system(
        self,
        manifest: dict[str, Any],
        *,
        prompt: str,
        override: str,
        defaults: dict[str, Any],
    ) -> str:
        """Select one design system from override, prompt match, or schema defaults."""
        design_systems = self._resources_by_type(manifest, "design_system")
        candidates = [str(item).strip() for item in defaults.get("recommended_design_system_candidates", [])]
        return self._select_resource_slug(
            design_systems,
            prompt=prompt,
            override=override,
            candidates=candidates,
            fallback="default",
        )

    def _select_device_frame(
        self,
        manifest: dict[str, Any],
        *,
        prompt: str,
        override: str,
        defaults: dict[str, Any],
        surface: str,
    ) -> str:
        """Select one optional device frame."""
        device_frames = self._resources_by_type(manifest, "device_frame")
        candidates = [
            str(defaults.get("device_frame") or "").strip(),
            _DEFAULT_FRAME_BY_SURFACE.get(surface, ""),
        ]
        return self._select_resource_slug(
            device_frames,
            prompt=prompt,
            override=override,
            candidates=candidates,
            fallback="",
        )

    def _select_resource_slug(
        self,
        resources: list[dict[str, Any]],
        *,
        prompt: str,
        override: str,
        candidates: list[str],
        fallback: str,
    ) -> str:
        """Select one resource slug using override, prompt triggers, defaults, then fallback."""
        resources_by_slug = {self._slug_from_resource(resource): resource for resource in resources}
        override_slug = self._normalize_slug(override)
        if override_slug in resources_by_slug:
            return override_slug

        normalized_text = self._normalize_match_text(prompt)
        matched_slug, matched_score = self._best_matching_resource_slug(resources_by_slug, normalized_text)
        if matched_slug:
            for candidate in candidates:
                candidate_slug = self._normalize_slug(candidate)
                candidate_resource = resources_by_slug.get(candidate_slug)
                if not candidate_resource:
                    continue
                candidate_score = self._resource_match_score(candidate_resource, normalized_text)
                if candidate_score == matched_score or matched_score <= 2:
                    return candidate_slug
            return matched_slug

        for candidate in candidates:
            candidate_slug = self._normalize_slug(candidate)
            if candidate_slug in resources_by_slug:
                return candidate_slug

        fallback_slug = self._normalize_slug(fallback)
        if fallback_slug in resources_by_slug:
            return fallback_slug
        return ""

    def _build_context_files(
        self,
        *,
        brief_resource: dict[str, Any],
        task_skill: str,
        design_system: str,
        device_frame: str,
        manifest: dict[str, Any],
    ) -> tuple[str, ...]:
        """Build project-relative context file paths for design artifact generation."""
        context_paths: list[str] = []

        def _append_resource_path(resource: dict[str, Any]) -> None:
            raw_path = str(resource.get("path", "")).strip()
            if raw_path:
                context_paths.append(str(_RESOURCE_ROOT / raw_path))

        _append_resource_path(brief_resource)
        for resource_type, slug in (
            ("task_skill", task_skill),
            ("design_system", design_system),
            ("device_frame", device_frame),
        ):
            if not slug:
                continue
            resource = self._find_resource_by_slug(manifest, resource_type, slug)
            if resource:
                _append_resource_path(resource)

        seen: set[str] = set()
        deduped: list[str] = []
        for path in context_paths:
            if path in seen:
                continue
            seen.add(path)
            deduped.append(path)
        return tuple(deduped)

    @staticmethod
    def _select_questions(brief_schema: dict[str, Any], *, max_questions: int) -> list[dict[str, str]]:
        """Select the first few scenario-specific clarification questions."""
        questions: list[dict[str, str]] = []
        for item in list(brief_schema.get("question_templates") or [])[: max(1, max_questions)]:
            field = str(item.get("field", "")).strip()
            question = str(item.get("question", "")).strip()
            if field and question:
                questions.append({"field": field, "question": question})
        return questions

    @staticmethod
    def _build_assumptions(
        *,
        defaults: dict[str, Any],
        selected_task_skill: str,
        selected_design_system: str,
        selected_device_frame: str,
    ) -> dict[str, Any]:
        """Build explicit assumptions used when the user skips clarification."""
        assumptions = dict(defaults)
        assumptions["selected_task_skill"] = selected_task_skill
        assumptions["selected_design_system"] = selected_design_system
        if selected_device_frame:
            assumptions["selected_device_frame"] = selected_device_frame
        return assumptions

    @staticmethod
    def _build_design_brief(
        *,
        user_prompt: str,
        surface: str,
        brief_schema: dict[str, Any],
        assumptions: dict[str, Any],
        selected_task_skill: str,
        selected_design_system: str,
        selected_device_frame: str,
        output_format: str,
    ) -> dict[str, Any]:
        """Build the stable DesignProductManager brief contract."""
        required_fields = [str(field) for field in brief_schema.get("required_fields", []) if str(field).strip()]
        raw_interactions = assumptions.get("interactions") or assumptions.get("interaction_requirements") or []
        if isinstance(raw_interactions, str):
            interactions = [raw_interactions] if raw_interactions.strip() else []
        else:
            interactions = [str(item) for item in list(raw_interactions or []) if str(item).strip()]
        return {
            "schema_version": DESIGN_BRIEF_SCHEMA_VERSION,
            "surface": surface,
            "scenario": list(brief_schema.get("scenarios") or []),
            "source_brief_schema_id": str(brief_schema.get("id", "") or ""),
            "user_prompt": user_prompt,
            "primary_user": str(assumptions.get("primary_user", "") or ""),
            "business_domain": str(assumptions.get("business_domain", "") or ""),
            "goal": str(assumptions.get("decision_goal") or assumptions.get("conversion_goal") or user_prompt),
            "content_requirements": required_fields,
            "visual_direction": str(assumptions.get("visual_direction", "") or ""),
            "design_system": selected_design_system,
            "device_frame": selected_device_frame,
            "interactions": interactions,
            "output_format": DesignProductManager._normalize_output_format(output_format),
            "constraints": {
                "output_contract": str(assumptions.get("output_contract", "") or ""),
                "density": str(assumptions.get("density", "") or ""),
                "platform": str(assumptions.get("platform", "") or ""),
                "task_skill": selected_task_skill,
            },
            "assumptions": assumptions,
        }

    @staticmethod
    def _build_generation_prompt(
        *,
        user_prompt: str,
        surface: str,
        brief_schema: dict[str, Any],
        assumptions: dict[str, Any],
        design_brief: dict[str, Any],
        questions: tuple[dict[str, str], ...],
        needs_clarification: bool,
    ) -> str:
        """Build the final prompt for code-backed design artifact generation."""
        lines = [
            "Create one polished, production-quality design artifact for Creative Claw.",
            f"Design surface: {surface}",
            f"Output contract: {assumptions.get('output_contract', 'standalone responsive HTML artifact')}",
            "",
            "# User request",
            user_prompt,
            "",
            "# Required design elements from selected brief schema",
            ", ".join(str(field) for field in brief_schema.get("required_fields", [])),
            "",
            "# Assumptions to use unless the user has already specified otherwise",
            json.dumps(assumptions, ensure_ascii=False, indent=2),
            "",
            "# Structured design brief contract",
            json.dumps(design_brief, ensure_ascii=False, indent=2),
        ]
        if questions:
            lines.extend(
                [
                    "",
                    "# Clarification questions available to the product manager",
                    json.dumps(list(questions), ensure_ascii=False, indent=2),
                ]
            )
        if needs_clarification:
            lines.extend(
                [
                    "",
                    "The user should normally answer the clarification questions before generation. "
                    "If generation is still requested, mark unspecified details as assumptions in the artifact copy.",
                ]
            )
        lines.extend(
            [
                "",
                "# Implementation requirements",
                "- Generate a complete standalone HTML file with embedded CSS and JavaScript when useful.",
                "- Use real interface structure: navigation, content hierarchy, controls, data states, and responsive behavior.",
                "- Do not include visible instructional copy explaining how the artifact was made.",
                "- Keep typography, spacing, states, and visual hierarchy coherent across desktop and mobile sizes.",
                "- Prefer accessible contrast and semantic HTML.",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _build_constraints(*, surface: str, output_format: str) -> list[str]:
        """Return stable constraints for code-backed design generation."""
        constraints = [
            "Return exactly one complete file.",
            "The artifact must run by opening the file directly in a browser when language is HTML.",
            "Use embedded CSS; avoid external package managers or build steps.",
            "Use realistic placeholder data when real data is not provided.",
        ]
        if surface == "dashboard":
            constraints.append("Design for scanning, comparison, filtering, and repeated operational use.")
        elif surface == "landing_page":
            constraints.append("Make the first viewport communicate the product and primary offer immediately.")
        elif surface == "mobile_app":
            constraints.append("Constrain the main UI to a realistic mobile viewport and use the selected device frame context if provided.")
        elif surface == "deck":
            constraints.append("Create slide-like sections with a clear narrative arc.")
        elif surface == "social_carousel":
            constraints.append("Create swipeable social-card sections with clear hierarchy and shareable content rhythm.")
        elif surface == "docs_page":
            constraints.append(
                "Create a structured documentation page with persistent navigation, readable examples, and clear content hierarchy."
            )
        elif surface == "poster":
            constraints.append(
                "Create a poster-like composition with a stable aspect ratio, strong editorial hierarchy, and no decorative app chrome."
            )
        elif surface == "wireframe":
            constraints.append(
                "Create a low-fidelity wireframe using neutral styling, explicit layout regions, and clear interaction placeholders."
            )
        if output_format.lower() != "html":
            constraints.append(f"Respect the requested output format: {output_format}.")
        return constraints

    @staticmethod
    def _normalize_output_format(output_format: str) -> str:
        """Normalize product output format for design artifact generation."""
        value = str(output_format or "html").strip().lower()
        if value in {"prototype", "web", "page", "dashboard", "landing"}:
            return "html"
        return value or "html"

    def _find_resource_by_slug(
        self,
        manifest: dict[str, Any],
        resource_type: str,
        slug: str,
    ) -> dict[str, Any] | None:
        """Find one manifest resource by type and slug."""
        normalized_slug = self._normalize_slug(slug)
        for resource in self._resources_by_type(manifest, resource_type):
            if self._slug_from_resource(resource) == normalized_slug:
                return resource
        return None

    @staticmethod
    def _slug_from_resource(resource: dict[str, Any]) -> str:
        """Extract the slug portion from a manifest resource id."""
        resource_id = str(resource.get("id", "")).strip()
        if "." in resource_id:
            return DesignProductManager._normalize_slug(resource_id.rsplit(".", 1)[-1])
        return DesignProductManager._normalize_slug(str(resource.get("title", "")))

    @staticmethod
    def _resource_matches(resource: dict[str, Any], normalized_text: str) -> bool:
        """Return whether prompt text matches resource id, title, or triggers."""
        return DesignProductManager._resource_match_score(resource, normalized_text) > 0

    @staticmethod
    def _best_matching_resource(
        resources: list[dict[str, Any]],
        normalized_text: str,
    ) -> dict[str, Any] | None:
        """Return the best matching resource by trigger specificity."""
        best_resource: dict[str, Any] | None = None
        best_score = 0
        for resource in resources:
            score = DesignProductManager._resource_match_score(resource, normalized_text)
            if score > best_score:
                best_resource = resource
                best_score = score
        return best_resource

    @staticmethod
    def _best_matching_resource_slug(
        resources_by_slug: dict[str, dict[str, Any]],
        normalized_text: str,
    ) -> tuple[str, int]:
        """Return the slug and score with the most specific trigger match."""
        best_slug = ""
        best_score = 0
        for slug, resource in resources_by_slug.items():
            score = DesignProductManager._resource_match_score(resource, normalized_text)
            if score > best_score:
                best_slug = slug
                best_score = score
        return best_slug, best_score

    @staticmethod
    def _resource_match_score(resource: dict[str, Any], normalized_text: str) -> int:
        """Score prompt/resource matches by the longest matching trigger text."""
        candidates = [
            str(resource.get("id", "")),
            str(resource.get("title", "")),
            *[str(item) for item in resource.get("triggers", [])],
            *[str(item) for item in resource.get("scenario", [])],
            *[str(item) for item in resource.get("scenarios", [])],
        ]
        return max(
            (
                DesignProductManager._candidate_match_score(normalized_candidate, normalized_text)
                for candidate in candidates
                if (normalized_candidate := DesignProductManager._normalize_match_text(candidate))
            ),
            default=0,
        )

    @staticmethod
    def _candidate_match_score(normalized_candidate: str, normalized_text: str) -> int:
        """Score exact phrase and token-set matches for one resource trigger."""
        if normalized_candidate in normalized_text:
            return len(normalized_candidate)
        tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", normalized_candidate)
        meaningful_tokens = [token for token in tokens if len(token) > 1 or re.search(r"[\u4e00-\u9fff]", token)]
        text_tokens = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", normalized_text))
        if len(meaningful_tokens) > 1 and all(
            token in normalized_text if re.search(r"[\u4e00-\u9fff]", token) else token in text_tokens
            for token in meaningful_tokens
        ):
            return len(normalized_candidate) + 5
        return 0

    @staticmethod
    def _detect_surface(normalized_text: str) -> str:
        """Detect the design surface from prompt and scenario keywords."""
        for surface, keywords in _SURFACE_KEYWORDS.items():
            if any(
                DesignProductManager._normalize_match_text(keyword) in normalized_text
                for keyword in keywords
            ):
                return surface
        return ""

    @staticmethod
    def _normalize_slug(value: str) -> str:
        """Normalize resource names to manifest slugs."""
        return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")

    @staticmethod
    def _normalize_match_text(value: str) -> str:
        """Normalize free text for lightweight trigger matching."""
        return re.sub(r"\s+", " ", str(value or "").strip().lower())
