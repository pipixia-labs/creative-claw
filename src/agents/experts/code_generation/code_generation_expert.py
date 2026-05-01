"""Reusable code generation expert for Creative Claw."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from typing_extensions import override

from src.agents.experts.base import CreativeExpert
from src.agents.experts.code_generation.tool import code_generation_tool


class CodeGenerationExpert(CreativeExpert):
    """Generate one code file from a structured brief and selected context files."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the code generation expert."""
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one code generation request."""
        current_parameters = ctx.session.state.get("current_parameters", {})
        prompt = str(current_parameters.get("prompt", "")).strip()
        if not prompt:
            error_text = f"Missing parameters provided to {self.name}, must include: prompt"
            current_output = {"status": "error", "message": error_text, "output_files": []}
            yield self.format_event(error_text, {"current_output": current_output})
            return

        result = await code_generation_tool(
            ctx,
            prompt=prompt,
            language=str(current_parameters.get("language", "html")).strip() or "html",
            output_path=str(current_parameters.get("output_path", "")).strip(),
            context_files=_as_string_list(current_parameters.get("context_files")),
            constraints=_as_string_list(current_parameters.get("constraints")),
        )

        if result["status"] == "error":
            current_output = {
                "status": "error",
                "message": result["message"],
                "output_files": [],
                "warnings": result.get("warnings", []),
            }
            yield self.format_event(result["message"], {"current_output": current_output})
            return

        current_output = {
            "status": "success",
            "message": result["message"],
            "output_text": result["message"],
            "output_files": result.get("output_files", []),
            "language": result.get("language", ""),
            "output_path": result.get("output_path", ""),
            "warnings": result.get("warnings", []),
            "provider": result.get("provider", ""),
            "model_name": result.get("model_name", ""),
        }
        yield self.format_event(
            result["message"],
            {
                "current_output": current_output,
                "code_generation_results": current_output,
            },
        )


def _as_string_list(value: Any) -> list[str]:
    """Normalize a scalar or sequence parameter into a string list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]
