"""Private expert allowlist for PageProductManager."""

from __future__ import annotations

from typing import Any

from src.runtime.expert_registry import get_expert_spec

PAGE_PRODUCT_EXPERT_ALLOWLIST: tuple[str, ...] = (
    "ImageGenerationAgent",
    "CodeGenerationExpert",
    "ImageUnderstandingAgent",
    "AnythingToMD",
    "SearchAgent",
)


def is_page_product_expert(agent_name: str) -> bool:
    """Return whether an expert is private to PageProductManager."""
    return str(agent_name or "").strip() in PAGE_PRODUCT_EXPERT_ALLOWLIST


def build_page_expert_listing(
    expert_agents: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return tool-visible metadata for PageProductManager private experts."""
    available_agents = expert_agents or {}
    experts: list[dict[str, Any]] = []
    for agent_name in PAGE_PRODUCT_EXPERT_ALLOWLIST:
        spec = get_expert_spec(agent_name)
        notes = spec.notes_builder() if spec.notes_builder is not None else spec.notes
        experts.append(
            {
                "name": agent_name,
                "available": agent_name in available_agents,
                "required_parameters": list(spec.required_parameters),
                "default_prompt_key": spec.default_prompt_key,
                "supports_plain_prompt": spec.supports_plain_prompt,
                "default_parameters": dict(spec.default_parameters),
                "allowed_values": {
                    key: list(values)
                    for key, values in spec.allowed_values.items()
                },
                "notes": notes,
            }
        )
    return experts


__all__ = [
    "PAGE_PRODUCT_EXPERT_ALLOWLIST",
    "build_page_expert_listing",
    "is_page_product_expert",
]
