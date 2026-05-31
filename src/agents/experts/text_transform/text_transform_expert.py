"""Text transform expert for Creative Claw."""

from __future__ import annotations

from typing import Any, AsyncGenerator
from typing_extensions import override

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from pydantic import BaseModel, ValidationError, field_validator, model_validator

from src.agents.experts.base import CreativeExpert
from src.agents.experts.schema_utils import clean_string, current_output_dict
from src.agents.experts.text_transform.tool import (
    _SUPPORTED_TEXT_TRANSFORM_MODES,
    normalize_text_transform_mode,
    transform_text_tool,
)


class TextTransformParameters(BaseModel):
    """Structured request contract read from ``current_parameters``."""

    model_config = {"extra": "ignore"}

    input_text: str = ""
    mode: str = ""
    target_language: str = ""
    style: str = ""
    constraints: str = ""

    @model_validator(mode="before")
    @classmethod
    def _support_legacy_text_alias(cls, value: Any) -> Any:
        """Map the legacy ``text`` field onto ``input_text`` when needed."""
        if not isinstance(value, dict):
            return {}
        payload = dict(value)
        if "input_text" not in payload and "text" in payload:
            payload["input_text"] = payload["text"]
        return payload

    @field_validator("input_text", "mode", "target_language", "style", "constraints", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        """Strip text fields while preserving empty defaults."""
        return clean_string(value)

    @field_validator("mode")
    @classmethod
    def _lower_mode(cls, value: str) -> str:
        """Normalize transform mode casing."""
        return value.lower()

    @property
    def missing_required_fields(self) -> bool:
        """Return whether the request is missing required public fields."""
        return not self.input_text or not self.mode


class TextTransformOutput(BaseModel):
    """Structured ``current_output`` contract emitted by ``TextTransformExpert``."""

    model_config = {"extra": "allow"}

    status: str
    message: str
    message_for_user: str | None = None
    output_text: str | None = None
    mode: str | None = None
    transformed_text: str | None = None
    provider: str | None = None
    model_name: str | None = None

    @field_validator(
        "status",
        "message",
        "message_for_user",
        "output_text",
        "mode",
        "transformed_text",
        "provider",
        "model_name",
        mode="before",
    )
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        """Strip optional output text fields."""
        if value is None:
            return None
        return clean_string(value)

    def to_current_output(self) -> dict[str, Any]:
        """Return the stable dictionary payload stored in session state."""
        return current_output_dict(self)


def _parse_text_transform_parameters(raw_parameters: Any) -> TextTransformParameters:
    """Parse text-transform session parameters into a structured contract."""
    return TextTransformParameters.model_validate(raw_parameters or {})


class TextTransformExpert(CreativeExpert):
    """Run one atomic text transformation."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the text transform expert."""
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one atomic text transform request."""
        try:
            current_parameters = _parse_text_transform_parameters(
                ctx.session.state.get("current_parameters", {})
            )
        except ValidationError:
            current_parameters = TextTransformParameters()

        if current_parameters.missing_required_fields:
            error_text = f"Missing parameters provided to {self.name}, must include: input_text or text, mode"
            current_output = TextTransformOutput(status="error", message=error_text).to_current_output()
            yield self.format_event(error_text, {"current_output": current_output})
            return

        if current_parameters.mode not in _SUPPORTED_TEXT_TRANSFORM_MODES:
            error_text = (
                f"Invalid mode provided to {self.name}: {current_parameters.mode}. "
                f"Supported modes are: {sorted(_SUPPORTED_TEXT_TRANSFORM_MODES)}."
            )
            current_output = TextTransformOutput(status="error", message=error_text).to_current_output()
            yield self.format_event(error_text, {"current_output": current_output})
            return

        result = await transform_text_tool(
            ctx,
            input_text=current_parameters.input_text,
            mode=normalize_text_transform_mode(current_parameters.mode),
            target_language=current_parameters.target_language,
            style=current_parameters.style,
            constraints=current_parameters.constraints,
        )
        if result["status"] == "error":
            current_output = TextTransformOutput(
                status="error",
                message=result["message"],
            ).to_current_output()
            yield self.format_event(result["message"], {"current_output": current_output})
            return

        transformed_text = str(result["message"]).strip()
        current_output = TextTransformOutput(
            status="success",
            message=f"{self.name} completed mode={current_parameters.mode}.",
            message_for_user=transformed_text,
            output_text=transformed_text,
            mode=current_parameters.mode,
            transformed_text=transformed_text,
            provider=result.get("provider", ""),
            model_name=result.get("model_name", ""),
        ).to_current_output()
        yield self.format_event(
            transformed_text,
            {
                "current_output": current_output,
                "text_transform_results": current_output,
            },
        )


__all__ = [
    "TextTransformExpert",
    "TextTransformOutput",
    "TextTransformParameters",
]
