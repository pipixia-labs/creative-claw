"""Page product manager agent and helpers."""

from src.productions.page.page_product_manager.page_code_generation_agent import (
    PageCodeGenerationAgent,
    build_page_code_generation_constraints,
    build_page_code_generation_prompt,
)
from src.productions.page.page_product_manager.page_product_experts import (
    PAGE_PRODUCT_EXPERT_ALLOWLIST,
    build_page_expert_listing,
    is_page_product_expert,
)
from src.productions.page.page_product_manager.page_product_manager import (
    PAGE_PRODUCT_DRAFT_STATE_KEY,
    PAGE_PRODUCT_FINAL_DRAFT_STATE_KEY,
    PAGE_PRODUCT_HTML_GENERATION_STATE_KEY,
    PAGE_PRODUCT_MATERIALS_STATE_KEY,
    PAGE_PRODUCT_RESULT_SCHEMA_VERSION,
    PAGE_PRODUCT_TEMPLATE_SELECTION_STATE_KEY,
    PageProductManager,
    PageProductRequest,
    PageProductResult,
)
from src.productions.page.page_product_manager.product_page_skills import (
    PRODUCT_PAGE_SKILLS_DIR,
    ProductPageSkillInfo,
    ProductPageSkillRegistry,
)
from src.productions.page.page_product_manager.templates import (
    PAGE_TEMPLATES,
    TEMPLATES_HTML_DIR,
    PageTemplate,
    PageTemplateMatch,
    PageTemplateRegistry,
    list_page_templates,
    load_page_templates_from_directory,
    select_page_template_match,
)

__all__ = [
    "PAGE_PRODUCT_EXPERT_ALLOWLIST",
    "PAGE_PRODUCT_DRAFT_STATE_KEY",
    "PAGE_PRODUCT_FINAL_DRAFT_STATE_KEY",
    "PAGE_PRODUCT_HTML_GENERATION_STATE_KEY",
    "PAGE_PRODUCT_MATERIALS_STATE_KEY",
    "PAGE_PRODUCT_RESULT_SCHEMA_VERSION",
    "PAGE_PRODUCT_TEMPLATE_SELECTION_STATE_KEY",
    "PAGE_TEMPLATES",
    "PRODUCT_PAGE_SKILLS_DIR",
    "TEMPLATES_HTML_DIR",
    "PageCodeGenerationAgent",
    "PageProductManager",
    "PageProductRequest",
    "PageProductResult",
    "PageTemplate",
    "PageTemplateMatch",
    "PageTemplateRegistry",
    "ProductPageSkillInfo",
    "ProductPageSkillRegistry",
    "build_page_code_generation_constraints",
    "build_page_code_generation_prompt",
    "build_page_expert_listing",
    "is_page_product_expert",
    "list_page_templates",
    "load_page_templates_from_directory",
    "select_page_template_match",
]
