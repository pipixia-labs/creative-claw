"""Anything-to-Markdown expert for Creative Claw."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import override

from src.agents.experts.anything_to_md.tool import convert_anything_to_markdown
from src.agents.experts.base import CreativeExpert
from src.agents.experts.schema_utils import current_output_dict


class AnythingToMDParameters(BaseModel):
    """Structured request contract read from ``current_parameters``."""

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _coerce_mapping(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return dict(value)

    def to_converter_parameters(
        self,
        *,
        session_id: str,
        turn_index: int,
        step: int,
        expert_step: int,
    ) -> dict[str, Any]:
        """Return the existing converter dictionary with ADK context metadata."""
        parameters = self.model_dump(mode="python")
        parameters["__session_id"] = session_id
        parameters["__turn_index"] = turn_index
        parameters["__step"] = step
        parameters["__expert_step"] = expert_step
        return parameters


class AnythingToMDOutput(BaseModel):
    """Structured ``current_output`` contract emitted by ``AnythingToMDExpert``."""

    model_config = ConfigDict(extra="allow")

    status: str
    message: str
    message_for_user: str | None = None
    output_text: str | None = None
    results: dict[str, Any] | None = None
    output_files: list[dict[str, Any]] | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def _coerce_output(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {"status": "error", "message": str(value)}
        return value

    def to_current_output(self) -> dict[str, Any]:
        """Return the stable dictionary payload stored in ADK session state."""
        return current_output_dict(self)


class AnythingToMDExpert(CreativeExpert):
    """Convert one local workspace source file into Markdown."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the AnythingToMD expert."""
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one AnythingToMD conversion request."""
        current_parameters = AnythingToMDParameters.model_validate(
            ctx.session.state.get("current_parameters", {})
        ).to_converter_parameters(
            session_id=ctx.session.id,
            turn_index=int(ctx.session.state.get("turn_index", 0) or 0),
            step=int(ctx.session.state.get("step", 0) or 0),
            expert_step=int(ctx.session.state.get("expert_step", 0) or 0),
        )

        current_output = AnythingToMDOutput.model_validate(
            convert_anything_to_markdown(current_parameters)
        ).to_current_output()
        state_delta: dict[str, Any] = {"current_output": current_output}
        if current_output.get("status") == "success":
            state_delta["anything_to_md_results"] = current_output.get("results", {})

        yield self.format_event(
            current_output.get("output_text") or current_output.get("message", ""),
            state_delta,
        )


__all__ = ["AnythingToMDExpert", "AnythingToMDOutput", "AnythingToMDParameters"]
