"""Shared schema helpers for expert request and output contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def clean_string(value: Any) -> str:
    """Normalize text-like values passed through expert session state."""
    return str(value or "").strip()


def as_prompt_list(value: Any, *, default_empty_prompt: bool = False) -> list[str]:
    """Normalize scalar or sequence prompt values while preserving empty entries."""
    if value is None:
        return [""] if default_empty_prompt else []
    if isinstance(value, str):
        return [value.strip()]
    if isinstance(value, (list, tuple, set)):
        return [clean_string(item) for item in value]
    return [clean_string(value)]


def as_non_empty_string_list(value: Any) -> list[str]:
    """Normalize scalar or sequence values into non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [clean_string(item) for item in value if clean_string(item)]
    cleaned = clean_string(value)
    return [cleaned] if cleaned else []


def as_list(value: Any) -> list[Any]:
    """Normalize scalar or tuple values into a list without filtering entries."""
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    return [value]


def current_output_dict(model: BaseModel) -> dict[str, Any]:
    """Return the stable minimal dictionary payload stored as ``current_output``."""
    return model.model_dump(mode="python", exclude_none=True)
