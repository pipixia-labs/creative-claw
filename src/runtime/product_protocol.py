"""Typed protocol helpers for Orchestrator-to-product-manager tool calls."""

from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from src.runtime.interaction_language import normalize_interaction_language

ProductLine = Literal["ppt", "page", "design"]


def _clean_string(value: Any) -> str:
    """Normalize text-like values crossing the product tool boundary."""
    return str(value or "").strip()


def _default_tool_inputs(value: Any) -> Any:
    """Mirror the existing product tool defaulting for missing or falsey inputs."""
    return [] if not value else value


def _default_tool_output(value: Any) -> Any:
    """Mirror the existing product tool defaulting for missing or falsey output options."""
    return {} if not value else value


class ProductToolRequest(BaseModel):
    """Stable request envelope used by Orchestrator product tools."""

    model_config = {"extra": "ignore", "arbitrary_types_allowed": True}

    product_line: ProductLine
    task: str = Field(description="User-facing product task text.")
    inputs: Any = Field(default_factory=list)
    output: Any = Field(default_factory=dict)
    interaction_language: str = Field(
        default="",
        description="Language used for product-to-user communication.",
    )

    @field_validator("task", mode="before")
    @classmethod
    def _strip_task(cls, value: Any) -> str:
        """Strip task text at the Orchestrator-to-product boundary."""
        return _clean_string(value)

    @field_validator("inputs", mode="before")
    @classmethod
    def _default_inputs(cls, value: Any) -> Any:
        """Default tool inputs with the same semantics as the previous Orchestrator code."""
        return _default_tool_inputs(value)

    @field_validator("output", mode="before")
    @classmethod
    def _default_output(cls, value: Any) -> Any:
        """Default tool output options with the same semantics as the previous code."""
        return _default_tool_output(value)

    @field_validator("interaction_language", mode="before")
    @classmethod
    def _normalize_interaction_language(cls, value: Any) -> str:
        """Normalize optional interaction-language metadata."""
        return normalize_interaction_language(value, fallback="")

    def to_manager_kwargs(self) -> dict[str, Any]:
        """Return product-manager call arguments without runtime-only metadata."""
        return {
            "task": self.task,
            "inputs": copy.deepcopy(self.inputs),
            "output": copy.deepcopy(self.output),
            "interaction_language": self.interaction_language,
        }

    def to_event_args(self) -> dict[str, Any]:
        """Return the public tool argument payload used for step events."""
        payload = self.to_manager_kwargs()
        if not payload.get("interaction_language"):
            payload.pop("interaction_language", None)
        return payload


__all__ = ["ProductLine", "ProductToolRequest"]
