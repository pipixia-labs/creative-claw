"""Shared agent wrapper for deterministic basic-operation experts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, AsyncGenerator

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator
from typing_extensions import override

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from src.agents.experts.base import CreativeExpert
from src.agents.experts.schema_utils import current_output_dict

BasicOperationRunner = Callable[[dict[str, Any]], dict[str, Any]]


class BasicOperationParameters(BaseModel):
    """Structured request contract read from ``current_parameters``."""

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _coerce_mapping(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return dict(value)

    def to_runner_parameters(
        self,
        *,
        session_id: str,
        turn_index: int,
        step: int,
        expert_step: int,
    ) -> dict[str, Any]:
        """Return the existing runner dictionary with ADK context metadata."""
        parameters = self.model_dump(mode="python")
        parameters["__session_id"] = session_id
        parameters["__turn_index"] = turn_index
        parameters["__step"] = step
        parameters["__expert_step"] = expert_step
        return parameters


class BasicOperationOutput(BaseModel):
    """Structured ``current_output`` contract emitted by basic-operation agents."""

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


class BasicOperationsAgent(CreativeExpert):
    """Run one deterministic basic operation through a media-specific runner."""

    _operation_runner: BasicOperationRunner = PrivateAttr()
    _results_key: str = PrivateAttr()

    def __init__(
        self,
        name: str,
        *,
        operation_runner: BasicOperationRunner,
        results_key: str,
        description: str = "",
    ) -> None:
        """Initialize a deterministic basic-operation expert wrapper."""
        super().__init__(name=name, description=description)
        self._operation_runner = operation_runner
        self._results_key = results_key

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one normalized deterministic media operation request."""
        current_parameters = BasicOperationParameters.model_validate(
            ctx.session.state.get("current_parameters", {})
        ).to_runner_parameters(
            session_id=ctx.session.id,
            turn_index=int(ctx.session.state.get("turn_index", 0) or 0),
            step=int(ctx.session.state.get("step", 0) or 0),
            expert_step=int(ctx.session.state.get("expert_step", 0) or 0),
        )
        current_output = BasicOperationOutput.model_validate(
            self._operation_runner(current_parameters)
        ).to_current_output()
        yield self.format_event(
            current_output.get("output_text") or current_output.get("message", ""),
            {
                "current_output": current_output,
                self._results_key: current_output.get("results", {}),
            },
        )


__all__ = [
    "BasicOperationOutput",
    "BasicOperationParameters",
    "BasicOperationRunner",
    "BasicOperationsAgent",
]
