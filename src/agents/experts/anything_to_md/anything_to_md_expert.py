"""Anything-to-Markdown expert for Creative Claw."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from typing_extensions import override

from src.agents.experts.anything_to_md.tool import convert_anything_to_markdown
from src.agents.experts.base import CreativeExpert


class AnythingToMDExpert(CreativeExpert):
    """Convert one local workspace source file into Markdown."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the AnythingToMD expert."""
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one AnythingToMD conversion request."""
        current_parameters = dict(ctx.session.state.get("current_parameters", {}))
        current_parameters["__session_id"] = ctx.session.id
        current_parameters["__turn_index"] = int(ctx.session.state.get("turn_index", 0) or 0)
        current_parameters["__step"] = int(ctx.session.state.get("step", 0) or 0)
        current_parameters["__expert_step"] = int(ctx.session.state.get("expert_step", 0) or 0)

        current_output = convert_anything_to_markdown(current_parameters)
        state_delta: dict[str, Any] = {"current_output": current_output}
        if current_output.get("status") == "success":
            state_delta["anything_to_md_results"] = current_output.get("results", {})

        yield self.format_event(
            current_output.get("output_text") or current_output.get("message", ""),
            state_delta,
        )
