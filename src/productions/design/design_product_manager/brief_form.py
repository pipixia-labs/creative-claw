"""Web-only design brief question-form support for DesignProductManager."""

from __future__ import annotations

import json
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

DESIGN_BRIEF_FORM_STATE_KEY = "design_product_brief_form"
DESIGN_BRIEF_FORM_ANSWERS_STATE_KEY = "design_product_brief_form_answers"
DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY = "design_product_brief_form_pending_task"
DESIGN_BRIEF_FORM_SCHEMA_VERSION = "design-brief-form-v1"

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
            "- Use the cross-task common question framework below as the default coverage framework. Cover these questions unless the user brief already answers them or the item is clearly irrelevant:\n"
            "  1. Design scope or key pages/sections/modules.\n"
            "  2. Primary user scenario or core workflow.\n"
            "  3. Visual style direction.\n"
            "  4. Color direction when visual identity matters.\n"
            "  5. Interface language when product text or locale matters.\n"
            "  6. Target platform or device when the deliverable depends on it.\n"
            "  7. Output scale, such as screen count, section count, or variant count.\n"
            "  8. Prototype interaction level: static, simple clickable, or complete interactive prototype.\n"
            "  9. Adjustable design aspects, such as color, type, density, corner radius, or dark/light mode, only for advanced requests.\n"
            "  10. Special requirements, references, brand names, required content, or inspiration.\n"
            "- For app, website, dashboard, product UI, and prototype requests, normally include interface language, target platform/device, output scale, and prototype interaction level even when they seem implicit.\n"
            "- Add up to 5 task-specific questions when useful. These should be invented from the brief, such as industry positioning, domain objects, content treatment, business-specific functions, or domain-specific visual semantics.\n"
            "- Do not hard-code restaurant, ecommerce, SaaS, dashboard, or landing-page questions. Infer the domain from the current task.\n"
            "- Keep task-specific details broad enough to help design direction, not implementation details.\n"
            "- Use single_choice or multi_choice when options are clear; use text fields for user-specific details.\n"
            "- Use range only for bounded numeric preferences such as screen count or variant count.\n"
            "- For multi_choice, include maxSelections only when there is a real limit.\n"
            "- Include an option with value \"decide_for_me\" and label \"为我决定\" in every key choice question.\n"
            "- Use allowOther true on choice questions when custom answers are likely useful.\n"
            "- Keep specific brand names, copywriting, constraints, or extra notes in one optional long_text question when needed.\n"
            "- Make most questions optional when \"为我决定\" is available, so users can submit quickly.\n"
            "- Do not ask about implementation details unless the brief explicitly requires them.\n"
            "- Do not decide or mention a design system in this iteration.\n"
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
        normalized["questions"] = [_validate_question(item) for item in questions]
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
        if not isinstance(options, list) or not options:
            raise ValueError(f"Question {question['id']} must include options.")
        question["options"] = [_validate_option(option) for option in options]
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
    return question


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


def _required_text(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"Missing required field: {key}")
    return value
