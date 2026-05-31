"""Reusable code generation expert for Creative Claw."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from pydantic import BaseModel, Field, ValidationError, field_validator
from typing_extensions import override

from src.agents.experts.base import CreativeExpert
from src.agents.experts.code_generation.tool import code_generation_tool
from src.agents.experts.schema_utils import clean_string


class CodeGenerationParameters(BaseModel):
    """Structured request contract read from ``current_parameters``."""

    model_config = {"extra": "ignore"}

    prompt: str = ""
    language: str = "html"
    output_path: str = ""
    context_files: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)

    @field_validator("prompt", "language", "output_path", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        """Strip text fields while preserving empty defaults."""
        return clean_string(value)

    @field_validator("language")
    @classmethod
    def _default_language(cls, value: str) -> str:
        """Default missing language to HTML."""
        return value or "html"

    @field_validator("context_files", "constraints", mode="before")
    @classmethod
    def _normalize_string_list(cls, value: Any) -> list[str]:
        """Normalize scalar or sequence values into non-empty strings."""
        return _as_string_list(value)

    @property
    def missing_required_fields(self) -> bool:
        """Return whether the request is missing required public fields."""
        return not self.prompt


class CodeGenerationOutput(BaseModel):
    """Structured ``current_output`` contract emitted by ``CodeGenerationExpert``."""

    model_config = {"extra": "allow"}

    status: str = "error"
    message: str = ""
    output_text: str = ""
    output_files: list[dict[str, Any]] = Field(default_factory=list)
    error_type: str = ""
    retryable: bool = False
    raw_error_summary: str = ""
    language: str = ""
    output_path: str = ""
    warnings: list[str] = Field(default_factory=list)
    provider: str = ""
    model_name: str = ""

    @field_validator(
        "status",
        "message",
        "output_text",
        "error_type",
        "raw_error_summary",
        "language",
        "output_path",
        "provider",
        "model_name",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        """Strip text output fields."""
        return clean_string(value)

    @field_validator("status")
    @classmethod
    def _normalize_status(cls, value: str) -> str:
        """Normalize output status casing."""
        return value.lower() or "error"

    @classmethod
    def from_tool_result(cls, result: dict[str, Any]) -> "CodeGenerationOutput":
        """Build the stable expert output from a code-generation tool result."""
        message = clean_string(result.get("message"))
        status = clean_string(result.get("status") or "error").lower() or "error"
        return cls(
            status=status,
            message=message,
            output_text=message if status == "success" else "",
            output_files=list(result.get("output_files") or []),
            error_type=result.get("error_type", ""),
            retryable=bool(result.get("retryable", False)),
            raw_error_summary=result.get("raw_error_summary", ""),
            language=result.get("language", ""),
            output_path=result.get("output_path", ""),
            warnings=list(result.get("warnings") or []),
            provider=result.get("provider", ""),
            model_name=result.get("model_name", ""),
        )

    def to_current_output(self) -> dict[str, Any]:
        """Return the stable dictionary payload stored in session state."""
        return self.model_dump(mode="python")


def _parse_code_generation_parameters(raw_parameters: Any) -> CodeGenerationParameters:
    """Parse code-generation session parameters into a structured contract."""
    return CodeGenerationParameters.model_validate(raw_parameters or {})


class CodeGenerationExpert(CreativeExpert):
    """Generate one code file from a structured brief and selected context files."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the code generation expert."""
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one code generation request."""
        try:
            current_parameters = _parse_code_generation_parameters(
                ctx.session.state.get("current_parameters", {})
            )
        except ValidationError:
            current_parameters = CodeGenerationParameters()
        if current_parameters.missing_required_fields:
            error_text = f"Missing parameters provided to {self.name}, must include: prompt"
            current_output = _build_current_output(
                {
                    "status": "error",
                    "message": error_text,
                    "error_type": "invalid_parameters",
                    "retryable": False,
                    "raw_error_summary": error_text,
                    "output_files": [],
                }
            )
            yield self.format_event(error_text, {"current_output": current_output})
            return

        result = await code_generation_tool(
            ctx,
            prompt=current_parameters.prompt,
            language=current_parameters.language,
            output_path=current_parameters.output_path,
            context_files=current_parameters.context_files,
            constraints=current_parameters.constraints,
        )

        if result["status"] == "error":
            current_output = _build_current_output(result)
            yield self.format_event(result["message"], {"current_output": current_output})
            return

        current_output = _build_current_output(result)
        yield self.format_event(
            result["message"],
            {
                "current_output": current_output,
                "code_generation_results": current_output,
            },
        )


def _build_current_output(result: dict[str, Any]) -> dict[str, Any]:
    """Return CodeGenerationExpert output in its stable capability contract."""
    return CodeGenerationOutput.from_tool_result(result).to_current_output()


def _as_string_list(value: Any) -> list[str]:
    """Normalize a scalar or sequence parameter into a string list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


__all__ = [
    "CodeGenerationExpert",
    "CodeGenerationOutput",
    "CodeGenerationParameters",
]
