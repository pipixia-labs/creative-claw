"""Design product manager agent and validation helpers."""

from src.productions.design.design_product_manager.design_code_generation_agent import (
    DesignCodeGenerationAgent,
    build_design_code_generation_constraints,
    build_design_code_generation_prompt,
)
from src.productions.design.design_product_manager.brief_form import (
    DESIGN_BRIEF_FORM_SCHEMA_VERSION,
    DesignBriefFormExpert,
    extract_question_form_json,
    normalize_question_form_block,
    parse_form_answers,
    validate_question_form_schema,
)
from src.productions.design.design_product_manager.design_product_manager import (
    DESIGN_PRODUCT_RESULT_SCHEMA_VERSION,
    DesignProductManager,
)
from src.productions.design.design_product_manager.design_product_experts import (
    DESIGN_PRODUCT_EXPERT_ALLOWLIST,
    build_design_expert_listing,
    is_design_product_expert,
)
from src.productions.design.design_product_manager.product_design_skills import (
    PRODUCT_DESIGN_SKILLS_DIR,
    ProductDesignSkillInfo,
    ProductDesignSkillRegistry,
)
from src.productions.design.design_product_manager.schema_validation import (
    DesignSchemaValidationError,
    validate_design_brief_contract,
    validate_design_result_contract,
)
from src.productions.design.design_product_manager.validation import (
    BrowserViewport,
    DEFAULT_BROWSER_VIEWPORTS,
    DesignArtifactValidation,
    validate_design_artifact,
    validate_design_artifacts,
)

__all__ = [
    "DesignArtifactValidation",
    "DESIGN_PRODUCT_RESULT_SCHEMA_VERSION",
    "DESIGN_BRIEF_FORM_SCHEMA_VERSION",
    "DESIGN_PRODUCT_EXPERT_ALLOWLIST",
    "DesignCodeGenerationAgent",
    "DesignBriefFormExpert",
    "DesignProductManager",
    "DesignSchemaValidationError",
    "PRODUCT_DESIGN_SKILLS_DIR",
    "ProductDesignSkillInfo",
    "ProductDesignSkillRegistry",
    "BrowserViewport",
    "DEFAULT_BROWSER_VIEWPORTS",
    "build_design_code_generation_constraints",
    "build_design_code_generation_prompt",
    "build_design_expert_listing",
    "extract_question_form_json",
    "is_design_product_expert",
    "normalize_question_form_block",
    "parse_form_answers",
    "validate_design_brief_contract",
    "validate_design_artifact",
    "validate_design_artifacts",
    "validate_question_form_schema",
    "validate_design_result_contract",
]
