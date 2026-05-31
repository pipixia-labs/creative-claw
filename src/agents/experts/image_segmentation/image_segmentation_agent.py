"""Image segmentation expert agent."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from pydantic import BaseModel, Field, field_validator, model_validator
from typing_extensions import override

from src.agents.experts.base import CreativeExpert
from src.agents.experts.image_segmentation.tool import image_segmentation_tool
from src.agents.experts.schema_utils import as_list, clean_string, current_output_dict
from src.logger import logger
from src.runtime.workspace import build_workspace_file_record, resolve_workspace_path

_DEFAULT_SEGMENTATION_MODEL = "DINO-X-1.0"
_DEFAULT_SEGMENTATION_THRESHOLD = 0.25


class ImageSegmentationParameters(BaseModel):
    """Structured request contract read from ``current_parameters``."""

    input_path: str = ""
    prompt: str = ""
    model: str = _DEFAULT_SEGMENTATION_MODEL
    threshold: Any = _DEFAULT_SEGMENTATION_THRESHOLD

    @model_validator(mode="before")
    @classmethod
    def _coerce_mapping(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {
            "input_path": value.get("input_path"),
            "prompt": value.get("prompt"),
            "model": value.get("model", _DEFAULT_SEGMENTATION_MODEL),
            "threshold": value.get("threshold", _DEFAULT_SEGMENTATION_THRESHOLD),
        }

    @field_validator("input_path", "prompt", "model", mode="before")
    @classmethod
    def _strip_string(cls, value: Any) -> str:
        return clean_string(value)

    @property
    def model_name(self) -> str:
        """Return the effective provider model name."""
        return self.model or _DEFAULT_SEGMENTATION_MODEL

    @property
    def threshold_value(self) -> float:
        """Return the effective numeric threshold used by the provider tool."""
        return float(self.threshold)

    @property
    def missing_required_fields(self) -> bool:
        """Whether the existing segmentation request lacks required parameters."""
        return not self.input_path or not self.prompt


class ImageSegmentationResultItem(BaseModel):
    """One image-segmentation result item stored in ``image_segmentation_results``."""

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
    threshold: Any = None
    mask_path: str = ""

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
            "threshold": value.get("threshold"),
            "mask_path": clean_string(value.get("mask_path")),
        }

    def to_result(self) -> dict[str, Any]:
        """Return the stable dictionary item stored in ADK session state."""
        return self.model_dump(mode="python")


class ImageSegmentationOutput(BaseModel):
    """Structured ``current_output`` contract emitted by ``ImageSegmentationAgent``."""

    status: str
    message: str
    message_for_user: str | None = None
    output_files: list[dict[str, Any]] | None = Field(default=None)
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


class ImageSegmentationAgent(CreativeExpert):
    """Segment one natural-language target in one workspace image."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the image segmentation expert agent."""
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run the DINO-X image segmentation flow for one workspace image."""
        current_parameters = ImageSegmentationParameters.model_validate(
            ctx.session.state.get("current_parameters", {})
        )

        if current_parameters.missing_required_fields:
            error_text = f"Missing parameters provided to {self.name}, must include: input_path, prompt"
            current_output = ImageSegmentationOutput(status="error", message=error_text).to_current_output()
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": current_output})
            return

        result = await image_segmentation_tool(
            ctx,
            current_parameters.input_path,
            current_parameters.prompt,
            model=current_parameters.model_name,
            threshold=current_parameters.threshold_value,
        )

        current_turn = int(ctx.session.state.get("turn_index", 0) or 0)
        current_step = int(ctx.session.state.get("step", 0) or 0)
        current_expert_step = int(ctx.session.state.get("expert_step", 0) or 0)
        status = str(result.get("status", "")).strip().lower()
        message = str(result.get("message", "")).strip()
        output_files = []
        mask_path = str(result.get("mask_path", "")).strip()
        if status == "success" and mask_path:
            output_files.append(
                build_workspace_file_record(
                    resolve_workspace_path(mask_path),
                    description=(
                        f"binary segmentation mask generated from '{current_parameters.input_path}' "
                        f"with prompt '{current_parameters.prompt}'"
                    ),
                    source="expert",
                    turn=current_turn,
                    step=current_step,
                    expert_step=current_expert_step,
                )
            )

        result_item = ImageSegmentationResultItem.model_validate(
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
                "threshold": result.get("threshold"),
                "mask_path": mask_path,
            }
        ).to_result()

        current_output = ImageSegmentationOutput(
            status=status or "error",
            message=message,
            message_for_user=message,
            output_files=output_files,
            results=[result_item],
        ).to_current_output()
        yield self.format_event(
            message,
            {
                "current_output": current_output,
                "image_segmentation_results": current_output["results"],
            },
        )
        return


__all__ = [
    "ImageSegmentationAgent",
    "ImageSegmentationOutput",
    "ImageSegmentationParameters",
    "ImageSegmentationResultItem",
]
