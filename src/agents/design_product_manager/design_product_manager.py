"""Resource-aware DesignProductManager for Creative Claw design tasks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.adk.agents import LlmAgent

from conf.llm import build_llm
from conf.path import PROJECT_PATH

_RESOURCE_ROOT = Path("skills/design-knowledge-and-skills")
_MANIFEST_PATH = _RESOURCE_ROOT / "resource-manifest.json"

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
}

_DEFAULT_FRAME_BY_SURFACE = {
    "dashboard": "browser-chrome",
    "landing_page": "browser-chrome",
    "mobile_app": "iphone-15-pro",
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
    """Prepared design brief plus CodeGenerationExpert request."""

    user_prompt: str
    selection: DesignResourceSelection
    questions: tuple[dict[str, str], ...]
    missing_fields: tuple[str, ...]
    assumptions: dict[str, Any]
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

Your job is to turn a user's design request into one focused design production brief:
- Search design-knowledge-and-skills before choosing resources.
- Select one primary task skill, at most one primary design system, and only useful device frame context.
- Ask for scenario-specific missing design elements from the matching brief-elements schema when needed.
- If the user asks to proceed without clarification, use explicit assumptions from the selected schema defaults.
- Use CodeGenerationExpert for code-backed design artifacts such as dashboards, landing pages, mobile prototypes, and HTML decks.
- Keep the final brief concrete enough for a coding agent to generate one runnable artifact.
""".strip()

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
        generation_prompt = self._build_generation_prompt(
            user_prompt=clean_prompt,
            surface=surface,
            brief_schema=brief_schema,
            assumptions=assumptions,
            questions=questions,
            needs_clarification=needs_clarification,
        )
        code_generation_request = {
            "prompt": generation_prompt,
            "language": self._normalize_output_format(output_format),
            "output_path": str(output_path or "").strip(),
            "context_files": list(context_files),
            "constraints": self._build_constraints(surface=surface, output_format=output_format),
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
            needs_clarification=needs_clarification,
            generation_prompt=generation_prompt,
            code_generation_request=code_generation_request,
        )

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
            if resource.get("type") == resource_type and resource.get("runtimeEnabled", True)
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
        explicit_surface = self._detect_surface(normalized_text)
        for resource in brief_resources:
            if str(resource.get("surface", "")).strip() == explicit_surface:
                return resource
        for resource in brief_resources:
            if self._resource_matches(resource, normalized_text):
                return resource
        return next(
            (
                resource
                for resource in brief_resources
                if str(resource.get("surface", "")).strip() == "dashboard"
            ),
            brief_resources[0],
        )

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
        matched_slug = self._best_matching_resource_slug(resources_by_slug, normalized_text)
        if matched_slug:
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
        """Build project-relative context file paths for CodeGenerationExpert."""
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
    def _build_generation_prompt(
        *,
        user_prompt: str,
        surface: str,
        brief_schema: dict[str, Any],
        assumptions: dict[str, Any],
        questions: tuple[dict[str, str], ...],
        needs_clarification: bool,
    ) -> str:
        """Build the final prompt passed to CodeGenerationExpert."""
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
        if output_format.lower() != "html":
            constraints.append(f"Respect the requested output format: {output_format}.")
        return constraints

    @staticmethod
    def _normalize_output_format(output_format: str) -> str:
        """Normalize product output format for CodeGenerationExpert."""
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
    def _best_matching_resource_slug(
        resources_by_slug: dict[str, dict[str, Any]],
        normalized_text: str,
    ) -> str:
        """Return the slug with the most specific trigger match."""
        best_slug = ""
        best_score = 0
        for slug, resource in resources_by_slug.items():
            score = DesignProductManager._resource_match_score(resource, normalized_text)
            if score > best_score:
                best_slug = slug
                best_score = score
        return best_slug

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
        if len(tokens) > 1 and all(token in normalized_text for token in tokens):
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
