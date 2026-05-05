"""Pydantic contracts shared by PPT product agents and route pipelines."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

PPT_PRODUCT_RESULT_SCHEMA_VERSION = "ppt-product-result-v1"
# Standard page types are template-level requirements, not a global deck-plan
# requirement. A template package may opt into these page types later.
REQUIRED_DECK_PAGE_TYPES = (
    "cover",
    "toc",
    "chapter_start",
    "chapter_content",
    "ending",
)

PptRoute = Literal["html", "svg", "xml"]
DeckPageType = Literal[
    "cover",
    "toc",
    "chapter_start",
    "chapter_content",
    "ending",
    "content",
    "activity",
    "quote",
    "stat",
    "kpi_grid",
    "comparison",
    "timeline",
    "roadmap",
    "process",
    "chart",
    "image_grid",
    "code",
    "appendix",
    "disclaimer",
]
DeckPageAssetSourceKind = Literal[
    "material_figure",
    "user_upload",
    "search",
    "image_generation",
    "placeholder",
]
DeckPageAssetStatus = Literal["pending", "ready", "failed"]


def _clean_string(value: str) -> str:
    """Normalize one user-facing schema string."""
    return str(value or "").strip()


class SourceInput(BaseModel):
    """One source file or URL attached to a PPT request."""

    name: str = ""
    path: str = ""
    mime_type: str = ""
    role: str = "source"
    description: str = ""

    @field_validator("name", "path", "mime_type", "role", "description", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        """Strip text-like source metadata."""
        return _clean_string(value)


class SourceUnderstanding(BaseModel):
    """Prepared source material references for downstream PPT planning agents."""

    document_type: str = "brief"
    markdown_sources: list[dict[str, Any]] = Field(default_factory=list)
    figures: list[dict[str, Any]] = Field(default_factory=list)
    output_files: list[dict[str, Any]] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)

    @field_validator("document_type", mode="before")
    @classmethod
    def _strip_document_type(cls, value: Any) -> str:
        """Strip the normalized document type."""
        return _clean_string(value) or "brief"


class ReferenceAsset(BaseModel):
    """One visual or brand reference provided for the PPT request."""

    name: str = ""
    path: str = ""
    asset_type: str = ""
    role: str = "reference"
    description: str = ""

    @field_validator("name", "path", "asset_type", "role", "description", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        """Strip text-like reference metadata."""
        return _clean_string(value)


class SlideCountPolicy(BaseModel):
    """Slide count bounds requested or inferred for a deck."""

    minimum: int = Field(default=4, ge=1)
    maximum: int = Field(default=8, ge=1)
    target: int | None = Field(default=None, ge=1)
    source: Literal["user", "inferred", "default"] = "default"

    @model_validator(mode="after")
    def _validate_bounds(self) -> "SlideCountPolicy":
        """Ensure slide count bounds are internally consistent."""
        if self.maximum < self.minimum:
            raise ValueError("maximum must be greater than or equal to minimum")
        if self.target is not None and not self.minimum <= self.target <= self.maximum:
            raise ValueError("target must be within minimum and maximum")
        return self


class TemplateRequirement(BaseModel):
    """Template intent and constraints for a PPT route."""

    use_template: bool = False
    template_source: Literal["system", "user", "none"] = "none"
    template_id: str = ""
    template_path: str = ""
    notes: str = ""

    @field_validator("template_id", "template_path", "notes", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        """Strip text-like template metadata."""
        return _clean_string(value)


class StyleRequirement(BaseModel):
    """Visual, tone, and language style constraints for a deck."""

    style_keywords: list[str] = Field(default_factory=list)
    tone: str = ""
    language_style: str = ""
    brand_notes: str = ""


class EditabilityRequirement(BaseModel):
    """Editability expectations and route-specific caveats."""

    level: Literal["low", "medium", "high", "native", "unknown"] = "unknown"
    must_preserve_template: bool = False
    notes: str = ""

    @field_validator("notes", mode="before")
    @classmethod
    def _strip_notes(cls, value: Any) -> str:
        """Strip editability notes."""
        return _clean_string(value)


class ConfirmedRequirement(BaseModel):
    """User-confirmed or system-normalized PPT product requirement."""

    route: PptRoute
    request_brief: str = ""
    topic: str
    audience: str = ""
    scenario: str = ""
    slide_count_policy: SlideCountPolicy = Field(default_factory=SlideCountPolicy)
    language: str = "zh-CN"
    aspect_ratio: Literal["16:9", "4:3"] = "16:9"
    output_format: Literal["pptx"] = "pptx"
    source_inputs: list[SourceInput] = Field(default_factory=list)
    source_understanding: SourceUnderstanding = Field(default_factory=SourceUnderstanding)
    reference_assets: list[ReferenceAsset] = Field(default_factory=list)
    template_requirement: TemplateRequirement = Field(default_factory=TemplateRequirement)
    style_requirement: StyleRequirement = Field(default_factory=StyleRequirement)
    editability_requirement: EditabilityRequirement = Field(default_factory=EditabilityRequirement)
    confirmed_by_user: bool = False

    @field_validator("request_brief", "topic", "audience", "scenario", "language", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        """Strip core requirement text fields."""
        return _clean_string(value)

    @field_validator("topic")
    @classmethod
    def _topic_required(cls, value: str) -> str:
        """Require a non-empty topic for downstream planning."""
        if not value:
            raise ValueError("topic is required")
        return value


class DeckChapter(BaseModel):
    """One narrative chapter in a deck content plan."""

    title: str
    purpose: str = ""
    order: int = Field(default=1, ge=1)


class DeckPageAsset(BaseModel):
    """One planned or resolved visual asset for a slide."""

    asset_id: str = ""
    role: str = "supporting_visual"
    semantic_position: str = "bottom_band"
    source_kind: DeckPageAssetSourceKind = "placeholder"
    status: DeckPageAssetStatus = "pending"
    description: str = ""
    alt: str = ""
    path: str = ""
    prompt: str = ""
    search_query: str = ""
    aspect_ratio: str = "16:9"
    resolution: str = "1K"
    placeholder_name: str = ""
    provider: str = ""
    warnings: list[str] = Field(default_factory=list)

    @field_validator(
        "asset_id",
        "role",
        "semantic_position",
        "description",
        "alt",
        "path",
        "prompt",
        "search_query",
        "aspect_ratio",
        "resolution",
        "placeholder_name",
        "provider",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        """Strip text-like asset metadata."""
        return _clean_string(value)


class DeckPagePlan(BaseModel):
    """Template-independent content plan for one slide."""

    slide_number: int = Field(ge=1)
    page_type: DeckPageType
    title: str
    purpose: str
    chapter: str = ""
    key_takeaway: str
    content_blocks: list[dict[str, Any]] = Field(default_factory=list)
    asset_intent: str = ""
    asset_roles: list[str] = Field(default_factory=list)
    asset_semantic_positions: list[str] = Field(default_factory=list)
    asset_source_preference: Literal["user", "search", "ai", "placeholder", "mixed"] = "placeholder"
    assets: list[DeckPageAsset] = Field(default_factory=list)
    speaker_notes: str = ""

    @field_validator(
        "title",
        "purpose",
        "chapter",
        "key_takeaway",
        "asset_intent",
        "speaker_notes",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        """Strip slide plan text fields."""
        return _clean_string(value)

    @field_validator("title", "purpose", "key_takeaway")
    @classmethod
    def _required_slide_text(cls, value: str) -> str:
        """Require core text fields for every planned slide."""
        if not value:
            raise ValueError("slide title, purpose, and key_takeaway are required")
        return value


class DeckContentPlan(BaseModel):
    """Template-independent plan consumed by HTML, SVG, or XML routes."""

    title: str
    core_narrative: str
    chapters: list[DeckChapter] = Field(default_factory=list)
    pages: list[DeckPagePlan]

    @field_validator("title", "core_narrative", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        """Strip deck-level plan text fields."""
        return _clean_string(value)

    @model_validator(mode="after")
    def _validate_plan_shape(self) -> "DeckContentPlan":
        """Validate the generic, template-independent deck-plan shape."""
        validate_deck_content_plan(self)
        return self


class QualityReviewResult(BaseModel):
    """Shared quality review result for any PPT route."""

    status: Literal["pass", "warning", "failed", "not_run"] = "not_run"
    page_count_ok: bool | None = None
    file_open_ok: bool | None = None
    text_complete_ok: bool | None = None
    assets_ok: bool | None = None
    placeholder_free_ok: bool | None = None
    overflow_ok: bool | None = None
    style_consistency_ok: bool | None = None
    route_issues: list[dict[str, Any]] = Field(default_factory=list)
    recommended_recovery_stage: str = ""


class DeliveryManifest(BaseModel):
    """Files and reports registered for the final PPT delivery."""

    final_pptx: str = ""
    previews: list[str] = Field(default_factory=list)
    quality_report: str = ""
    build_log: str = ""
    intermediate_artifacts: list[str] = Field(default_factory=list)
    output_files: list[dict[str, Any]] = Field(default_factory=list)


class HtmlTemplatePackage(BaseModel):
    """Loaded system HTML template package for the HTML route."""

    template_id: str
    label: str
    version: str = "0.1.0"
    aspect_ratio: Literal["16:9", "4:3"] = "16:9"
    viewport_width: int = Field(default=1280, ge=1)
    viewport_height: int = Field(default=720, ge=1)
    page_types: dict[str, str] = Field(default_factory=dict)
    pptx_strategy: Literal["native_editable", "html_to_pptx", "screenshot"] = "native_editable"
    editability_notes: str = ""


class HtmlRouteBuildPackage(BaseModel):
    """Build artifacts produced by the HTML route before delivery review."""

    template: HtmlTemplatePackage
    html_deck_path: str
    preview_paths: list[str] = Field(default_factory=list)
    pptx_path: str
    quality_report_path: str = ""
    build_log_path: str = ""
    warnings: list[str] = Field(default_factory=list)


class PptProductResult(BaseModel):
    """Top-level structured result returned by `run_ppt_product`."""

    result_schema_version: str = PPT_PRODUCT_RESULT_SCHEMA_VERSION
    status: Literal[
        "accepted",
        "needs_clarification",
        "route_not_implemented",
        "generation_failed",
        "success",
        "error",
        "awaiting_requirement_confirmation",
        "awaiting_content_plan_confirmation",
    ]
    product_line: Literal["ppt"] = "ppt"
    phase: str
    message: str
    selected_route: PptRoute
    confirmed_requirement: ConfirmedRequirement | None = None
    deck_content_plan: DeckContentPlan | None = None
    route_build: HtmlRouteBuildPackage | None = None
    quality_review: QualityReviewResult | None = None
    delivery_manifest: DeliveryManifest = Field(default_factory=DeliveryManifest)
    confirmation_request: dict[str, Any] = Field(default_factory=dict)
    output_files: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


def validate_deck_content_plan(plan: DeckContentPlan) -> None:
    """Validate the minimal route-independent DeckContentPlan invariants."""
    if not plan.pages:
        raise ValueError("DeckContentPlan must contain at least one page.")
    slide_numbers = [page.slide_number for page in plan.pages]
    duplicate_numbers = sorted({number for number in slide_numbers if slide_numbers.count(number) > 1})
    if duplicate_numbers:
        duplicates = ", ".join(str(number) for number in duplicate_numbers)
        raise ValueError(f"DeckContentPlan has duplicate slide numbers: {duplicates}")
