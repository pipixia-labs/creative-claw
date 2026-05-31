"""Shared schema helpers for product-manager request and result contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def clean_string(value: Any) -> str:
    """Normalize text-like values passed through product-manager schemas."""
    return str(value or "").strip()


def default_empty_list(value: Any) -> Any:
    """Default a missing list-like field while preserving caller-provided shape."""
    return [] if value is None else value


def default_empty_dict(value: Any) -> Any:
    """Default a missing dictionary field while preserving caller-provided shape."""
    return {} if value is None else value


def default_schema_version(value: Any, default: str) -> str:
    """Return a cleaned schema version or the product-specific default."""
    return clean_string(value) or default


def require_non_empty_string(value: str, *, field_name: str) -> str:
    """Require a previously-normalized string field to be non-empty."""
    if not value:
        raise ValueError(f"{field_name} must be non-empty")
    return value


def model_dump_dict(model: BaseModel, *, mode: str = "python") -> dict[str, Any]:
    """Return a stable dictionary payload for product-manager state and results."""
    return model.model_dump(mode=mode)
