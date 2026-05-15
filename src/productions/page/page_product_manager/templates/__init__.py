"""Built-in Page product templates."""

from src.productions.page.page_product_manager.templates.loader import (
    TEMPLATES_HTML_DIR,
    load_page_templates_from_directory,
)
from src.productions.page.page_product_manager.templates.models import (
    PageTemplate,
    PageTemplateMatch,
)
from src.productions.page.page_product_manager.templates.registry import (
    DEFAULT_PAGE_TEMPLATE_REGISTRY,
    PAGE_TEMPLATES,
    PageTemplateRegistry,
    get_page_template,
    list_page_templates,
    select_page_template_match,
)
from src.productions.page.page_product_manager.templates.selector import (
    DEFAULT_PAGE_TEMPLATE_ID,
    select_page_template,
)

__all__ = [
    "DEFAULT_PAGE_TEMPLATE_ID",
    "DEFAULT_PAGE_TEMPLATE_REGISTRY",
    "PAGE_TEMPLATES",
    "TEMPLATES_HTML_DIR",
    "PageTemplate",
    "PageTemplateMatch",
    "PageTemplateRegistry",
    "get_page_template",
    "list_page_templates",
    "load_page_templates_from_directory",
    "select_page_template",
    "select_page_template_match",
]
