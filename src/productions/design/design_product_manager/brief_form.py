"""Web-only design brief question-form support for DesignProductManager."""

from __future__ import annotations

import json
import random
import re
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from conf.llm import build_llm
from src.productions.design.design_systems import DesignSystemSummary, list_design_systems

DESIGN_BRIEF_FORM_STATE_KEY = "design_product_brief_form"
DESIGN_BRIEF_FORM_ANSWERS_STATE_KEY = "design_product_brief_form_answers"
DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY = "design_product_brief_form_pending_task"
DESIGN_BRIEF_FORM_SCHEMA_VERSION = "design-brief-form-v1"
DESIGN_SYSTEM_QUESTION_ID = "design_system_reference"
DESIGN_SYSTEM_RECOMMENDATION_COUNT = 6

QUESTION_FORM_OPEN_RE = re.compile(r"<cc-question-form(?:\s+[^>]*)?>", re.IGNORECASE)
QUESTION_FORM_CLOSE = "</cc-question-form>"
FORM_ANSWERS_RE = re.compile(
    r"\[cc-form-answers\s+id=\"(?P<id>[^\"]+)\"(?:\s+version=\"(?P<version>[^\"]+)\")?\]\s*"
    r"(?P<body>\{.*?\})\s*"
    r"\[/cc-form-answers\]",
    re.IGNORECASE | re.DOTALL,
)


class DesignBriefFormExpert(LlmAgent):
    """Private LLM agent that creates and validates Web design brief forms."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the private design brief form expert."""
        super().__init__(
            name=kwargs.pop("name", "DesignBriefFormExpert"),
            model=kwargs.pop("model", build_llm()),
            description=kwargs.pop(
                "description",
                "Creates Web question-form JSON schemas for design brief clarification.",
            ),
            instruction=kwargs.pop("instruction", type(self).build_instruction()),
            **kwargs,
        )

    @staticmethod
    def build_instruction() -> str:
        """Return the LLM instruction for generating one Web question-form schema."""
        return (
            "You are a private Creative Claw expert that creates concise Web UI forms for "
            "clarifying design briefs before generation.\n\n"
            "Return exactly one <cc-question-form> block and no extra prose.\n"
            "The content inside the block must be valid JSON matching this shape:\n"
            "{\n"
            '  "id": "kebab-case-string",\n'
            '  "version": "design-brief-form-v1",\n'
            '  "title": "short Chinese title",\n'
            '  "description": "one short Chinese sentence",\n'
            '  "submitLabel": "short Chinese button label",\n'
            '  "questions": [\n'
            "    {\n"
            '      "id": "snake_case_string",\n'
            '      "label": "Chinese label",\n'
            '      "type": "single_choice | multi_choice | short_text | long_text | range",\n'
            '      "presentation": "optional presentation hint, e.g. design_system_picker",\n'
            '      "resource": "optional resource hint, e.g. design_systems",\n'
            '      "required": true,\n'
            '      "placeholder": "optional string",\n'
            '      "maxSelections": 2,\n'
            '      "allowOther": true,\n'
            '      "min": 3,\n'
            '      "max": 12,\n'
            '      "default": 6,\n'
            '      "options": [\n'
            '        {"value": "stable_machine_value", "label": "Chinese option", "description": "optional short text"}\n'
            "      ]\n"
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Question strategy:\n"
            "- Treat the schema shape as fixed, but tailor the actual questions and options to the user brief.\n"
            "- Ask only one round of questions. Do not ask the user to answer another follow-up round later.\n"
            "- Keep the form reasonably compact, but do not use a hard total question limit. Ask enough questions to cover the design brief in one round.\n"
            "- Order tag/choice questions by decision dependency: content, mode, workflow, platform, content treatment, scale, and interaction first; visual style, color, design system, and adjustable visual details second; free-form notes last.\n"
            "- Do not place visual style, color, typography, or design system questions before the core content/mode questions unless the user brief is only about restyling an existing design.\n"
            "- Use the cross-task common question framework below as the default coverage framework. Cover these questions unless the user brief already answers them or the item is clearly irrelevant:\n"
            "  1. Design scope or key pages/sections/modules.\n"
            "  2. Primary user scenario or core workflow.\n"
            "  3. Target platform or device when the deliverable depends on it.\n"
            "  4. Domain content or content treatment, such as data, media, product/menu items, or placeholder strategy.\n"
            "  5. Interface language when product text or locale matters.\n"
            "  6. Output scale, such as screen count, section count, or variant count.\n"
            "  7. Prototype interaction level: static, simple clickable, or complete interactive prototype.\n"
            "  8. Visual style direction.\n"
            "  9. Color direction when visual identity matters.\n"
            "  10. Design system reference.\n"
            "  11. Adjustable design aspects, such as color, type, density, corner radius, or dark/light mode, only for advanced requests.\n"
            "  12. Special requirements, references, brand names, required content, or inspiration.\n"
            "- For app, website, dashboard, product UI, and prototype requests, normally include interface language, target platform/device, output scale, and prototype interaction level even when they seem implicit.\n"
            "- Use a visual direction question when the user has not provided a clear brand/style or asks to explore style. Prefer id \"visual_direction\". Useful option values include: \"decide_for_me\", \"editorial_monocle\", \"modern_minimal\", \"warm_soft\", \"tech_utility\", \"brutalist_experimental\", and \"explore_multiple\".\n"
            "- When style exploration is relevant, ask how many directions to explore with id \"style_exploration\" or fold it into visual_direction. Use options like \"one_refined_direction\", \"explore_2_3_directions\", \"explore_4_plus_directions\", and \"decide_for_me\".\n"
            "- Treat visual directions as design posture packages, not generic adjectives: each selected direction should imply palette, type personality, density, border/radius style, image strategy, and restraint level.\n"
            "- Include one design system reference question for Web design tasks. Use id \"design_system_reference\", type \"single_choice\", presentation \"design_system_picker\", resource \"design_systems\", required false, and allowOther true.\n"
            "- Place the design system reference question after visual style and color questions, and before the final long_text notes question.\n"
            "- For design_system_reference.options, include exactly 6 recommended design systems from the provided catalog, followed by {\"value\":\"decide_for_me\",\"label\":\"为我决定\"} as the last option. Use the catalog id as value, the catalog title as label, and a short Simplified Chinese recommendation reason as description.\n"
            "- Add up to 5 task-specific questions when useful. These should be invented from the brief, such as industry positioning, domain objects, content treatment, business-specific functions, or domain-specific visual semantics.\n"
            "- Do not hard-code restaurant, ecommerce, SaaS, dashboard, or landing-page questions. Infer the domain from the current task.\n"
            "- Keep task-specific details broad enough to help design direction, not implementation details.\n"
            "- Use single_choice or multi_choice when options are clear; use text fields for user-specific details.\n"
            "- Use range only for bounded numeric preferences such as screen count or variant count.\n"
            "- For multi_choice, include maxSelections only when there is a real limit.\n"
            "- Include an option with value \"decide_for_me\" and label \"为我决定\" as the last option in every key choice question.\n"
            "- Use allowOther true on choice questions when custom answers are likely useful.\n"
            "- Keep specific brand names, copywriting, constraints, or extra notes in one optional long_text question when needed.\n"
            "- Make most questions optional when \"为我决定\" is available, so users can submit quickly.\n"
            "- Do not ask about implementation details unless the brief explicitly requires them.\n"
            "- Do not invent design system ids. Choose design_system_reference options only from the provided available design system catalog; users may still select \"为我决定\".\n"
            "- Use Simplified Chinese for user-facing labels.\n"
            "- Ensure IDs are stable ASCII identifiers.\n"
        )

    async def generate_form(
        self,
        *,
        task: str,
        app_name: str,
        user_id: str,
    ) -> str:
        """Run this private LLM agent and return one validated form block."""
        design_system_catalog = _format_design_system_catalog_for_prompt()
        session_service = InMemorySessionService()
        session = await session_service.create_session(app_name=app_name, user_id=user_id, state={})
        runner = Runner(
            app=App(name=app_name, root_agent=self),
            app_name=app_name,
            session_service=session_service,
            artifact_service=InMemoryArtifactService(),
            memory_service=InMemoryMemoryService(),
        )
        chunks: list[str] = []
        try:
            async for event in runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=Content(
                    role="user",
                    parts=[
                        Part(
                            text=(
                                "Create the question form for this design request.\n\n"
                                f"# User design request\n{str(task or '').strip()}"
                                "\n\n# Available design system catalog\n"
                                f"{design_system_catalog}"
                            )
                        )
                    ],
                ),
            ):
                content = getattr(event, "content", None)
                for part in list(getattr(content, "parts", []) or []):
                    text = getattr(part, "text", None)
                    if text:
                        chunks.append(str(text))
        finally:
            await runner.close()

        raw = "\n".join(chunks).strip()
        return self.normalize_question_form_block(raw)

    @staticmethod
    def normalize_question_form_block(raw: str) -> str:
        """Return one canonical question-form block after validating its JSON body."""
        body = DesignBriefFormExpert.extract_question_form_json(raw)
        form = DesignBriefFormExpert.validate_question_form_schema(body)
        return "<cc-question-form>\n" + json.dumps(form, ensure_ascii=False, indent=2) + "\n</cc-question-form>"

    @staticmethod
    def extract_question_form_json(raw: str) -> dict[str, Any]:
        """Extract the JSON object from a `<cc-question-form>` block or raw JSON text."""
        text = str(raw or "").strip()
        text = _strip_markdown_fence(text)
        open_match = QUESTION_FORM_OPEN_RE.search(text)
        if open_match:
            close_index = text.lower().find(QUESTION_FORM_CLOSE, open_match.end())
            if close_index < 0:
                raise ValueError("Question form block is missing </cc-question-form>.")
            text = text[open_match.end() : close_index].strip()
            text = _strip_markdown_fence(text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Question form body is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Question form JSON must be an object.")
        return parsed

    @staticmethod
    def validate_question_form_schema(form: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize the Web question-form schema."""
        normalized = dict(form)
        normalized["id"] = _required_text(normalized, "id")
        normalized["version"] = str(normalized.get("version") or DESIGN_BRIEF_FORM_SCHEMA_VERSION)
        normalized["title"] = _required_text(normalized, "title")
        normalized["description"] = str(normalized.get("description") or "").strip()
        normalized["submitLabel"] = str(normalized.get("submitLabel") or "确认并继续").strip()
        questions = normalized.get("questions")
        if not isinstance(questions, list) or not questions:
            raise ValueError("Question form must include at least one question.")
        normalized["questions"] = _order_questions_by_decision_flow(
            _ensure_design_system_question([_validate_question(item) for item in questions])
        )
        return normalized

    @staticmethod
    def parse_form_answers(text: str) -> dict[str, Any] | None:
        """Parse a submitted Web form-answer block from user text."""
        match = FORM_ANSWERS_RE.search(str(text or ""))
        if not match:
            return None
        try:
            answers = json.loads(match.group("body"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Form answers body is not valid JSON: {exc}") from exc
        if not isinstance(answers, dict):
            raise ValueError("Form answers JSON must be an object.")
        return {
            "id": match.group("id"),
            "version": match.group("version") or "",
            "answers": answers,
        }

    @staticmethod
    def build_task_with_form_answers(*, original_task: str, answer_payload: dict[str, Any]) -> str:
        """Combine the original design request with submitted form answers."""
        return "\n".join(
            [
                str(original_task or "").strip(),
                "",
                "# Confirmed design brief answers",
                json.dumps(answer_payload, ensure_ascii=False, indent=2),
            ]
        ).strip()


def build_design_brief_form_agent() -> LlmAgent:
    """Create the private LLM agent that produces Web question-form schemas."""
    return DesignBriefFormExpert()


async def generate_design_brief_form(
    *,
    task: str,
    app_name: str,
    user_id: str,
) -> str:
    """Run the private form expert and return a validated form block."""
    return await DesignBriefFormExpert().generate_form(
        task=task,
        app_name=app_name,
        user_id=user_id,
    )


def normalize_question_form_block(raw: str) -> str:
    """Return one canonical question-form block after validating its JSON body."""
    return DesignBriefFormExpert.normalize_question_form_block(raw)


def extract_question_form_json(raw: str) -> dict[str, Any]:
    """Extract the JSON object from a `<cc-question-form>` block or raw JSON text."""
    return DesignBriefFormExpert.extract_question_form_json(raw)


def _strip_markdown_fence(text: str) -> str:
    """Remove a single surrounding Markdown code fence when present."""
    stripped = str(text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def validate_question_form_schema(form: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the Web question-form schema."""
    return DesignBriefFormExpert.validate_question_form_schema(form)


def parse_form_answers(text: str) -> dict[str, Any] | None:
    """Parse a submitted Web form-answer block from user text."""
    return DesignBriefFormExpert.parse_form_answers(text)


def build_task_with_form_answers(*, original_task: str, answer_payload: dict[str, Any]) -> str:
    """Combine the original design request with submitted form answers."""
    return DesignBriefFormExpert.build_task_with_form_answers(
        original_task=original_task,
        answer_payload=answer_payload,
    )


def _validate_question(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Each question must be an object.")
    question = dict(value)
    question["id"] = _required_text(question, "id")
    question["label"] = _required_text(question, "label")
    question_type = _required_text(question, "type")
    allowed_types = {"single_choice", "multi_choice", "short_text", "long_text", "range"}
    if question_type not in allowed_types:
        raise ValueError(f"Unsupported question type: {question_type}")
    question["type"] = question_type
    question["required"] = bool(question.get("required", False))
    if question_type in {"single_choice", "multi_choice"}:
        options = question.get("options")
        if _is_design_system_question(question) and (not isinstance(options, list) or not options):
            options = [{"value": "decide_for_me", "label": "为我决定"}]
        if not isinstance(options, list) or not options:
            raise ValueError(f"Question {question['id']} must include options.")
        question["options"] = _move_decide_options_last([_validate_option(option) for option in options])
        question["allowOther"] = bool(question.get("allowOther", False))
    elif question_type == "range":
        question.pop("options", None)
        question["min"] = int(question.get("min", 0))
        question["max"] = int(question.get("max", 10))
        if question["max"] <= question["min"]:
            raise ValueError(f"Question {question['id']} range max must be greater than min.")
        default = question.get("default", question["min"])
        question["default"] = min(max(int(default), question["min"]), question["max"])
    elif "options" in question:
        question.pop("options", None)
    if "maxSelections" in question and question["maxSelections"] is not None:
        question["maxSelections"] = int(question["maxSelections"])
    if "placeholder" in question:
        question["placeholder"] = str(question.get("placeholder") or "")
    if "presentation" in question:
        question["presentation"] = str(question.get("presentation") or "").strip()
    if "resource" in question:
        question["resource"] = str(question.get("resource") or "").strip()
    return question


def _ensure_design_system_question(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure Web design brief forms always expose the design-system picker."""
    normalized: list[dict[str, Any]] = []
    found = False
    for question in questions:
        if _is_design_system_question(question):
            normalized.append(_normalize_design_system_question(question))
            found = True
        else:
            normalized.append(question)
    if found:
        return normalized

    insert_at = next(
        (index for index, question in enumerate(normalized) if question.get("type") == "long_text"),
        len(normalized),
    )
    normalized.insert(insert_at, _normalize_design_system_question({}))
    return normalized


def _order_questions_by_decision_flow(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order questions so users decide content/mode before visual styling."""
    content_questions: list[dict[str, Any]] = []
    style_questions: list[dict[str, Any]] = []
    final_notes: list[dict[str, Any]] = []
    for question in questions:
        if question.get("type") == "long_text":
            final_notes.append(question)
        elif _is_style_question(question):
            style_questions.append(question)
        else:
            content_questions.append(question)
    return [*content_questions, *style_questions, *final_notes]


def _is_style_question(question: dict[str, Any]) -> bool:
    if _is_design_system_question(question):
        return True
    haystack = " ".join(
        str(question.get(key) or "").lower()
        for key in ("id", "label", "presentation", "resource")
    )
    style_markers = (
        "visual",
        "style",
        "tone",
        "color",
        "colour",
        "palette",
        "aesthetic",
        "mood",
        "theme",
        "typography",
        "font",
        "density",
        "corner",
        "radius",
        "light_mode",
        "dark_mode",
        "look_and_feel",
        "视觉",
        "风格",
        "色彩",
        "颜色",
        "配色",
        "调性",
        "美学",
        "主题",
        "字体",
        "字号",
        "密度",
        "圆角",
        "深色",
        "浅色",
        "设计系统",
    )
    return any(marker in haystack for marker in style_markers)


def _is_design_system_question(question: dict[str, Any]) -> bool:
    return (
        question.get("id") == DESIGN_SYSTEM_QUESTION_ID
        or question.get("presentation") == "design_system_picker"
        or question.get("resource") == "design_systems"
    )


def _normalize_design_system_question(question: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(question)
    normalized["id"] = DESIGN_SYSTEM_QUESTION_ID
    normalized["label"] = str(normalized.get("label") or "希望参考哪套设计系统？").strip()
    normalized["type"] = "single_choice"
    normalized["presentation"] = "design_system_picker"
    normalized["resource"] = "design_systems"
    normalized["required"] = False
    normalized["allowOther"] = True
    normalized.pop("maxSelections", None)
    normalized.pop("min", None)
    normalized.pop("max", None)
    normalized.pop("default", None)

    options = normalized.get("options")
    if not isinstance(options, list):
        options = []
    summaries = list_design_systems()
    summary_by_id = {summary.id: summary for summary in summaries}
    recommended_options: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for option in options:
        try:
            validated = _validate_option(option)
        except ValueError:
            continue
        system_id = validated["value"]
        if system_id == "decide_for_me" or system_id in seen_ids or system_id not in summary_by_id:
            continue
        recommended_options.append(_option_from_design_system(summary_by_id[system_id], validated.get("description", "")))
        seen_ids.add(system_id)
        if len(recommended_options) >= DESIGN_SYSTEM_RECOMMENDATION_COUNT:
            break

    if len(recommended_options) < DESIGN_SYSTEM_RECOMMENDATION_COUNT:
        recommended_options.extend(
            _random_design_system_options(
                summaries,
                exclude_ids=seen_ids,
                limit=DESIGN_SYSTEM_RECOMMENDATION_COUNT - len(recommended_options),
            )
        )

    normalized["options"] = [
        *recommended_options[:DESIGN_SYSTEM_RECOMMENDATION_COUNT],
        {"value": "decide_for_me", "label": "为我决定"},
    ]
    return normalized


def _option_from_design_system(summary: DesignSystemSummary, reason: str = "") -> dict[str, str]:
    description = str(reason or "").strip() or f"可参考 {summary.title} 的视觉语言。"
    return {
        "value": summary.id,
        "label": summary.title,
        "description": description[:120],
    }


def _random_design_system_options(
    summaries: list[DesignSystemSummary],
    *,
    exclude_ids: set[str],
    limit: int,
) -> list[dict[str, str]]:
    candidates = [summary for summary in summaries if summary.id not in exclude_ids]
    if not candidates or limit <= 0:
        return []
    selected = random.sample(candidates, k=min(limit, len(candidates)))
    return [_option_from_design_system(summary, "随机候选，可作为风格参考。") for summary in selected]


def _format_design_system_catalog_for_prompt() -> str:
    summaries = list_design_systems()
    if not summaries:
        return "No local design systems are available."
    lines = [
        "Choose exactly 6 design systems from this catalog for design_system_reference.options.",
        "Each option must use value=id, label=title, and description=a short Chinese reason for this user request.",
    ]
    for summary in summaries:
        summary_text = summary.summary.strip()
        if len(summary_text) > 160:
            summary_text = f"{summary_text[:157]}..."
        lines.append(f"- id: {summary.id}; title: {summary.title}; summary: {summary_text}")
    return "\n".join(lines)


def _validate_option(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("Each option must be an object.")
    option = {
        "value": _required_text(value, "value"),
        "label": _required_text(value, "label"),
    }
    description = str(value.get("description") or "").strip()
    if description:
        option["description"] = description
    return option


def _move_decide_options_last(options: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return options with decide-for-me choices moved to the end."""
    decide_options = [option for option in options if option["value"] == "decide_for_me"]
    regular_options = [option for option in options if option["value"] != "decide_for_me"]
    return [*regular_options, *decide_options]


def _required_text(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"Missing required field: {key}")
    return value
