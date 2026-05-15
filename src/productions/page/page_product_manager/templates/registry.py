"""Registry facade for Page product built-in templates."""

from __future__ import annotations

from pathlib import Path

from src.productions.page.page_product_manager.templates.loader import (
    TEMPLATES_HTML_DIR,
    load_page_templates_from_directory,
)
from src.productions.page.page_product_manager.templates.models import (
    PageTemplate,
    PageTemplateMatch,
)
from src.productions.page.page_product_manager.templates.selector import select_page_template


class PageTemplateRegistry:
    """In-memory registry for Page product built-in templates."""

    def __init__(
        self,
        templates_dir: str | Path = TEMPLATES_HTML_DIR,
        templates: tuple[PageTemplate, ...] | None = None,
    ) -> None:
        """Initialize the registry from the templates-html directory."""
        self._templates_dir = Path(templates_dir)
        self._templates = tuple(
            templates
            if templates is not None
            else load_page_templates_from_directory(self._templates_dir)
        )
        self._template_by_id = {template.id: template for template in self._templates}
        if len(self._template_by_id) != len(self._templates):
            raise ValueError("Page template ids must be unique.")

    @property
    def templates_dir(self) -> Path:
        """Return the directory used as the template source of truth."""
        return self._templates_dir

    def list_templates(self) -> tuple[PageTemplate, ...]:
        """Return all built-in templates in stable selection order."""
        return self._templates

    def list_template_dicts(self) -> list[dict[str, object]]:
        """Return JSON-friendly template summaries."""
        return [template.to_dict() for template in self._templates]

    def get_template(self, template_id: str) -> PageTemplate | None:
        """Return one template by id, or None when it is unknown."""
        return self._template_by_id.get(str(template_id or "").strip())

    def select_template(self, brief: str, template_id: str | None = None) -> PageTemplateMatch:
        """Select the best template for a brief."""
        return select_page_template(
            brief,
            template_id=template_id,
            templates=self._templates,
        )


DEFAULT_PAGE_TEMPLATE_REGISTRY = PageTemplateRegistry()
PAGE_TEMPLATES = DEFAULT_PAGE_TEMPLATE_REGISTRY.list_templates()


def list_page_templates() -> list[dict[str, object]]:
    """Return JSON-friendly summaries for all Page built-in templates."""
    return DEFAULT_PAGE_TEMPLATE_REGISTRY.list_template_dicts()


def get_page_template(template_id: str) -> PageTemplate | None:
    """Return one Page built-in template by id."""
    return DEFAULT_PAGE_TEMPLATE_REGISTRY.get_template(template_id)


def select_page_template_match(brief: str, template_id: str | None = None) -> PageTemplateMatch:
    """Select a Page built-in template through the default registry."""
    return DEFAULT_PAGE_TEMPLATE_REGISTRY.select_template(brief, template_id=template_id)
