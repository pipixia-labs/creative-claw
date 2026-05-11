"""JSON Schema validation for DesignProductManager contracts."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from conf.path import PROJECT_PATH

_SCHEMA_ROOT = Path("src/productions/design/design_product_manager/schemas")
_DESIGN_BRIEF_SCHEMA = "design-brief-v1.schema.json"
_DESIGN_RESULT_SCHEMA = "design-product-result-v1.schema.json"


class DesignSchemaValidationError(ValueError):
    """Raised when a DesignProductManager contract violates its JSON Schema."""


def validate_design_brief_contract(payload: dict[str, Any], *, project_root: str | Path | None = None) -> None:
    """Validate one `design-brief-v1` payload or raise a contract error."""
    _validate_contract(
        payload,
        schema_name=_DESIGN_BRIEF_SCHEMA,
        contract_name="design-brief-v1",
        project_root=project_root,
    )


def validate_design_result_contract(payload: dict[str, Any], *, project_root: str | Path | None = None) -> None:
    """Validate one `design-product-result-v1` payload or raise a contract error."""
    _validate_contract(
        payload,
        schema_name=_DESIGN_RESULT_SCHEMA,
        contract_name="design-product-result-v1",
        project_root=project_root,
    )


def _validate_contract(
    payload: dict[str, Any],
    *,
    schema_name: str,
    contract_name: str,
    project_root: str | Path | None,
) -> None:
    """Validate one payload against a local JSON Schema file."""
    validator = _load_validator(_schema_path(schema_name, project_root=project_root))
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.absolute_path))
    if not errors:
        return
    first_error = errors[0]
    location = _format_error_location(first_error)
    raise DesignSchemaValidationError(f"{contract_name} contract invalid at {location}: {first_error.message}")


def _schema_path(schema_name: str, *, project_root: str | Path | None) -> Path:
    """Return the absolute path for one design contract schema."""
    root = Path(project_root or PROJECT_PATH).resolve()
    return root / _SCHEMA_ROOT / schema_name


@lru_cache(maxsize=8)
def _load_validator(schema_path: Path) -> Draft202012Validator:
    """Load and cache one Draft 2020-12 JSON Schema validator."""
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _format_error_location(error: ValidationError) -> str:
    """Render a compact JSON path for one schema validation error."""
    path_parts = [str(part) for part in error.absolute_path]
    return "$" if not path_parts else "$." + ".".join(path_parts)
