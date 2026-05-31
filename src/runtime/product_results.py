"""Helpers for slimming product-manager results returned to Orchestrator."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

_PPT_CONFIRMATION_STATUSES = {
    "awaiting_requirement_confirmation",
    "awaiting_content_plan_confirmation",
}

_TERMINAL_PRODUCT_ERROR_STATUSES = {
    "blocked",
    "canceled",
    "cancelled",
    "error",
    "failed",
    "failure",
    "generation_failed",
    "incomplete",
    "invalid",
    "needs_clarification",
    "route_not_implemented",
    "timed_out",
    "timeout",
}


class ProductToolResult(BaseModel):
    """Stable slim result envelope returned by Orchestrator product tools."""

    model_config = {"extra": "ignore"}

    result_schema_version: str = ""
    status: str = ""
    product_line: str = ""
    message: str = ""
    final_file_paths: list[str] = Field(default_factory=list)

    @field_validator("result_schema_version", "status", "product_line", "message", mode="before")
    @classmethod
    def _strip_text_fields(cls, value: Any) -> str:
        """Normalize product tool result text fields."""
        return str(value or "").strip()

    @field_validator("final_file_paths", mode="before")
    @classmethod
    def _normalize_final_file_paths(cls, value: Any) -> list[str]:
        """Normalize product final paths into non-empty strings."""
        return _string_list(value)

    def to_dict(self) -> dict[str, Any]:
        """Return the stable dictionary payload exposed to parent agent logic."""
        slim = self.model_dump(mode="python")
        return {key: value for key, value in slim.items() if value not in ("", None)}


def slim_product_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return a compact user-facing product result for parent tool responses."""
    payload = dict(result) if isinstance(result, dict) else {}
    return ProductToolResult(
        result_schema_version=payload.get("result_schema_version"),
        status=payload.get("status"),
        product_line=payload.get("product_line"),
        message=_build_user_message(payload),
        final_file_paths=_extract_final_file_paths(payload),
    ).to_dict()


def is_product_confirmation_result(result: Any) -> bool:
    """Return whether a slim product result should pause for user confirmation."""
    if not isinstance(result, dict):
        return False
    return str(result.get("status") or "").strip() in _PPT_CONFIRMATION_STATUSES and bool(
        str(result.get("message") or "").strip()
    )


def is_completed_product_result(result: Any) -> bool:
    """Return whether a product result is ready for final delivery."""
    if not isinstance(result, dict):
        return False
    return (
        str(result.get("product_line") or "").strip() in {"page", "ppt", "design"}
        and str(result.get("status") or "").strip().lower() == "success"
        and bool(str(result.get("message") or "").strip())
        and bool(_string_list(result.get("final_file_paths")))
    )


def is_terminal_product_result(result: Any) -> bool:
    """Return whether a failed product result should end the turn with a user reply."""
    if not isinstance(result, dict):
        return False
    return (
        str(result.get("product_line") or "").strip() in {"page", "ppt", "design"}
        and str(result.get("status") or "").strip().lower() in _TERMINAL_PRODUCT_ERROR_STATUSES
        and bool(str(result.get("message") or "").strip())
    )


def is_completed_page_product_result(result: Any) -> bool:
    """Return whether a Page product result is ready for final delivery."""
    return is_completed_product_result(result) and str(result.get("product_line") or "").strip() == "page"


def _build_user_message(payload: dict[str, Any]) -> str:
    """Build the concise user-facing message retained in a slim product result."""
    message = str(payload.get("message") or "").strip()
    confirmation_request = payload.get("confirmation_request")
    if not isinstance(confirmation_request, dict):
        return message

    summary_markdown = str(confirmation_request.get("summary_markdown") or "").strip()
    expected_user_action = str(confirmation_request.get("expected_user_action") or "").strip()
    return "\n\n".join(part for part in (message, summary_markdown, expected_user_action) if part)


def _extract_final_file_paths(payload: dict[str, Any]) -> list[str]:
    """Extract final deliverable paths without returning rich file metadata."""
    explicit_paths = _string_list(payload.get("final_file_paths"))
    if explicit_paths:
        return explicit_paths

    delivery_manifest = payload.get("delivery_manifest")
    if isinstance(delivery_manifest, dict):
        final_pptx = str(delivery_manifest.get("final_pptx") or "").strip()
        if final_pptx:
            return [final_pptx]

    return []


def _string_list(value: Any) -> list[str]:
    """Normalize a value into a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if item is not None and str(item).strip()]
    return []


__all__ = [
    "is_completed_product_result",
    "is_completed_page_product_result",
    "is_product_confirmation_result",
    "is_terminal_product_result",
    "ProductToolResult",
    "slim_product_result",
]
