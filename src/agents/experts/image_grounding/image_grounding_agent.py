"""Image grounding expert agent."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from pydantic import BaseModel, Field, field_validator, model_validator
from typing_extensions import override

from src.agents.experts.base import CreativeExpert
from src.agents.experts.image_grounding.tool import dino_xseek_detection_tool
from src.agents.experts.schema_utils import as_list, clean_string, current_output_dict
from src.logger import logger


class ImageGroundingParameters(BaseModel):
    """Structured request contract read from ``current_parameters``."""

    input_path: str = ""
    prompt: str = ""
    model: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_mapping(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {
            "input_path": value.get("input_path"),
            "prompt": value.get("prompt"),
            "model": value.get("model", ""),
        }

    @field_validator("input_path", "prompt", "model", mode="before")
    @classmethod
    def _strip_string(cls, value: Any) -> str:
        return clean_string(value)

    @property
    def missing_required_fields(self) -> bool:
        """Whether the existing grounding request lacks required parameters."""
        return not self.input_path or not self.prompt


class ImageGroundingResultItem(BaseModel):
    """One image-grounding result item stored in ``image_ground_results``."""

    input_path: str
    prompt: str
    status: str
    message: str
    objects: list[Any] = Field(default_factory=list)
    bboxes: list[Any] = Field(default_factory=list)
    task_uuid: str = ""
    session_id: str = ""
    provider: str = ""
    model_name: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_item(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {
            "input_path": clean_string(value.get("input_path")),
            "prompt": clean_string(value.get("prompt")),
            "status": clean_string(value.get("status")).lower(),
            "message": clean_string(value.get("message")),
            "objects": as_list(value.get("objects")),
            "bboxes": as_list(value.get("bboxes")),
            "task_uuid": clean_string(value.get("task_uuid")),
            "session_id": clean_string(value.get("session_id")),
            "provider": clean_string(value.get("provider")),
            "model_name": clean_string(value.get("model_name")),
        }

    def to_result(self) -> dict[str, Any]:
        """Return the stable dictionary item stored in ADK session state."""
        return self.model_dump(mode="python")


class ImageGroundingOutput(BaseModel):
    """Structured ``current_output`` contract emitted by ``ImageGroundingAgent``."""

    status: str
    message: str
    message_for_user: str | None = None
    results: list[dict[str, Any]] | None = Field(default=None)

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value: Any) -> str:
        return clean_string(value).lower() or "error"

    @field_validator("message", "message_for_user", mode="before")
    @classmethod
    def _strip_optional_string(cls, value: Any) -> str:
        return clean_string(value)

    def to_current_output(self) -> dict[str, Any]:
        """Return the stable dictionary payload stored in ADK session state."""
        return current_output_dict(self)


class ImageGroundingAgent(CreativeExpert):
    """Ground a natural-language target description to bbox results in one image."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the image grounding expert agent."""
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run the DINO-XSeek grounding flow for one workspace image."""
        current_parameters = ImageGroundingParameters.model_validate(
            ctx.session.state.get("current_parameters", {})
        )

        if current_parameters.missing_required_fields:
            error_text = f"Missing parameters provided to {self.name}, must include: input_path, prompt"
            current_output = ImageGroundingOutput(status="error", message=error_text).to_current_output()
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": current_output})
            return

        result = await dino_xseek_detection_tool(
            ctx,
            current_parameters.input_path,
            current_parameters.prompt,
            **({"model": current_parameters.model} if current_parameters.model else {}),
        )
        status = str(result.get("status", "")).strip().lower()
        message = str(result.get("message", "")).strip()
        result_item = ImageGroundingResultItem.model_validate(
            {
                "input_path": str(result.get("input_path", current_parameters.input_path)).strip()
                or current_parameters.input_path,
                "prompt": str(result.get("prompt", current_parameters.prompt)).strip()
                or current_parameters.prompt,
                "status": status or "error",
                "message": message,
                "objects": result.get("objects", []),
                "bboxes": result.get("bboxes", []),
                "task_uuid": result.get("task_uuid", ""),
                "session_id": result.get("session_id", ""),
                "provider": result.get("provider", ""),
                "model_name": result.get("model_name", ""),
            }
        ).to_result()

        current_output = ImageGroundingOutput(
            status=status or "error",
            message=message,
            message_for_user=message,
            results=[result_item],
        ).to_current_output()
        yield self.format_event(
            message,
            {
                "current_output": current_output,
                "image_ground_results": current_output["results"],
            },
        )
        return


__all__ = [
    "ImageGroundingAgent",
    "ImageGroundingOutput",
    "ImageGroundingParameters",
    "ImageGroundingResultItem",
]
