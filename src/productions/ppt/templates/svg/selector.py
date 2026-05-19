"""Deterministic SVG layout template selection."""

from __future__ import annotations

import re
from collections.abc import Iterable

from src.productions.ppt.schemas import ConfirmedRequirement, DeckContentPlan
from src.productions.ppt.templates.svg.loader import load_svg_layout_template
from src.productions.ppt.templates.svg.models import (
    SvgLayoutTemplate,
    SvgLayoutTemplateMatch,
)

_AUTO_SELECTION_THRESHOLD = 35
_PRIMARY_SIGNAL_THRESHOLD = 25
_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+", re.IGNORECASE)

_TEMPLATE_ALIASES: dict[str, tuple[str, ...]] = {
    "academic_defense": ("academic_defense", "academic defense", "论文答辩", "毕业答辩", "学术答辩"),
    "ai_ops": ("ai_ops", "ai ops", "智能运维", "数字化转型", "电信ai"),
    "anthropic": ("anthropic", "claude", "anthropic style"),
    "china_telecom_template": ("china_telecom_template", "中国电信", "电信模板"),
    "exhibit": ("exhibit", "exhibit style", "董事会汇报", "高管汇报"),
    "google_style": ("google_style", "google style", "谷歌风格", "google模板"),
    "government_blue": ("government_blue", "政府蓝", "政务蓝", "智慧城市"),
    "government_red": ("government_red", "政府红", "党建", "党政", "红色政府"),
    "mckinsey": ("mckinsey", "麦肯锡", "咨询风格", "战略咨询"),
    "medical_university": ("medical_university", "医疗", "医院", "病例", "医学"),
    "pixel_retro": ("pixel_retro", "像素", "复古游戏", "pixel"),
    "psychology_attachment": ("psychology_attachment", "心理", "心理咨询", "疗愈"),
    "smart_red": ("smart_red", "红橙商务", "科技企业", "教育方案"),
    "科技蓝商务": ("科技蓝商务", "科技蓝", "蓝色商务", "科技商务"),
    "重庆大学": ("重庆大学", "重大", "重庆大学"),
}

_SCENARIO_TERMS: dict[str, tuple[str, ...]] = {
    "academic_defense": (
        "论文",
        "答辩",
        "毕业",
        "学术",
        "课题",
        "开题",
        "研究",
        "thesis",
        "defense",
        "academic",
        "research",
    ),
    "mckinsey": (
        "咨询",
        "战略",
        "麦肯锡",
        "投资分析",
        "商业分析",
        "高管",
        "董事会",
        "consulting",
        "strategy",
        "executive",
        "board",
        "investment",
    ),
    "google_style": (
        "google",
        "谷歌",
        "技术分享",
        "数据展示",
        "年度报告",
        "technical",
        "data presentation",
    ),
    "anthropic": (
        "anthropic",
        "claude",
        "大模型",
        "llm",
        "ai技术",
        "开发者",
        "developer",
    ),
    "government_red": ("党建", "党政", "政府工作报告", "红色", "权威", "party", "government red"),
    "government_blue": ("政务", "政府", "智慧城市", "数字政府", "治理", "government", "smart city"),
    "medical_university": ("医疗", "医院", "病例", "医学", "科研病例", "medical", "hospital"),
    "psychology_attachment": ("心理", "咨询", "疗愈", "心理治疗", "counseling", "psychology"),
    "pixel_retro": ("像素", "复古", "游戏", "cyberpunk", "pixel", "retro"),
    "china_telecom_template": ("中国电信", "电信", "运营商", "telecom"),
    "ai_ops": ("智能运维", "运维", "数字化转型", "it系统", "ai ops", "operations"),
    "exhibit": ("exhibit", "结论先行", "board", "executive", "strategy report"),
    "科技蓝商务": ("科技蓝", "商务", "解决方案", "企业汇报", "corporate report"),
    "smart_red": ("红橙", "科技公司", "教育解决方案", "vibrant business"),
    "重庆大学": ("重庆大学", "重大", "大学答辩", "university"),
}


def select_svg_layout_template_match(
    *,
    requirement: ConfirmedRequirement | None = None,
    content_plan: DeckContentPlan | None = None,
    task: str = "",
    template_id: str = "",
    templates: Iterable[SvgLayoutTemplate] | None = None,
) -> SvgLayoutTemplateMatch:
    """Select a system SVG layout template for one PPT request."""
    clean_template_id = str(template_id or "").strip()
    if not clean_template_id and requirement is not None:
        clean_template_id = requirement.template_requirement.template_id
    loaded_templates = tuple(templates) if templates is not None else None

    if clean_template_id:
        return _select_explicit_template(clean_template_id, templates=loaded_templates)

    primary_text = _selection_text(requirement=requirement, content_plan=None, task=task)
    text = _selection_text(requirement=requirement, content_plan=content_plan, task=task)
    if not text.strip():
        return SvgLayoutTemplateMatch(
            use_template=False,
            fallback_reason="No task text available for SVG layout template selection.",
        )

    candidates = loaded_templates if loaded_templates is not None else _load_all_templates()
    best_template: SvgLayoutTemplate | None = None
    best_score = 0
    best_primary_score = 0
    best_reasons: tuple[str, ...] = ()
    for template in candidates:
        score, reasons = _score_template(template, text)
        if score > best_score:
            best_template = template
            best_score = score
            best_primary_score = _score_template(template, primary_text)[0]
            best_reasons = tuple(reasons)

    if best_template is None or best_score < _AUTO_SELECTION_THRESHOLD:
        return SvgLayoutTemplateMatch(
            use_template=False,
            score=best_score,
            reasons=best_reasons,
            fallback_reason="No SVG layout template passed the auto-selection threshold.",
        )
    if best_primary_score < _PRIMARY_SIGNAL_THRESHOLD:
        return SvgLayoutTemplateMatch(
            use_template=False,
            score=best_score,
            reasons=best_reasons,
            fallback_reason="No SVG layout template passed the primary task signal threshold.",
        )
    return SvgLayoutTemplateMatch(
        use_template=True,
        template_id=best_template.template_id,
        score=best_score,
        reasons=best_reasons,
        template=best_template,
    )


def _select_explicit_template(
    template_id: str,
    *,
    templates: tuple[SvgLayoutTemplate, ...] | None,
) -> SvgLayoutTemplateMatch:
    normalized = _normalize(template_id)
    candidates = templates if templates is not None else _load_all_templates()
    for template in candidates:
        aliases = (template.template_id, template.label, *_TEMPLATE_ALIASES.get(template.template_id, ()))
        if normalized in {_normalize(alias) for alias in aliases}:
            return SvgLayoutTemplateMatch(
                use_template=True,
                template_id=template.template_id,
                score=100,
                reasons=(f"Explicit SVG layout template request matched `{template.template_id}`.",),
                explicit=True,
                template=template,
            )
    try:
        template = load_svg_layout_template(template_id)
        return SvgLayoutTemplateMatch(
            use_template=True,
            template_id=template.template_id,
            score=100,
            reasons=(f"Explicit SVG layout template request matched `{template.template_id}`.",),
            explicit=True,
            template=template,
        )
    except ValueError as exc:
        return SvgLayoutTemplateMatch(
            use_template=False,
            template_id=template_id,
            explicit=True,
            fallback_reason=str(exc),
        )


def _selection_text(
    *,
    requirement: ConfirmedRequirement | None,
    content_plan: DeckContentPlan | None,
    task: str,
) -> str:
    parts = [task]
    if requirement is not None:
        parts.extend(
            [
                requirement.request_brief,
                requirement.topic,
                requirement.audience,
                requirement.scenario,
                " ".join(requirement.style_requirement.style_keywords),
                requirement.style_requirement.tone,
                requirement.style_requirement.language_style,
                requirement.style_requirement.brand_notes,
                requirement.template_requirement.notes,
            ]
        )
    if content_plan is not None:
        parts.extend([content_plan.title, content_plan.core_narrative])
        parts.extend(page.title for page in content_plan.pages[:6])
        parts.extend(page.purpose for page in content_plan.pages[:6])
    return " ".join(str(part or "") for part in parts)


def _score_template(template: SvgLayoutTemplate, text: str) -> tuple[int, list[str]]:
    normalized_text = _normalize(text)
    token_text = set(_tokens(text))
    score = 0
    reasons: list[str] = []

    for term in _SCENARIO_TERMS.get(template.template_id, ()):
        if _term_matches(term, normalized_text, token_text):
            score += 18
            reasons.append(f"Matched scenario term `{term}`.")

    for keyword in template.keywords:
        if _term_matches(keyword, normalized_text, token_text):
            score += 10
            reasons.append(f"Matched template keyword `{keyword}`.")

    for alias in _TEMPLATE_ALIASES.get(template.template_id, ()):
        if _normalize(alias) and _normalize(alias) in normalized_text:
            score += 25
            reasons.append(f"Matched template alias `{alias}`.")

    summary_terms = [template.label, template.summary]
    for term in summary_terms:
        if term and _normalize(term) in normalized_text:
            score += 8
            reasons.append(f"Matched template metadata `{term}`.")

    return score, _dedupe(reasons)[:6]


def _term_matches(term: str, normalized_text: str, token_text: set[str]) -> bool:
    normalized_term = _normalize(term)
    if not normalized_term:
        return False
    if re.search(r"[\u4e00-\u9fff]", normalized_term):
        return normalized_term in normalized_text
    return normalized_term in token_text or normalized_term in normalized_text


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _tokens(value: str) -> list[str]:
    return [_normalize(match.group(0)) for match in _TOKEN_RE.finditer(value)]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _load_all_templates() -> tuple[SvgLayoutTemplate, ...]:
    # Import lazily to keep module import cheap in tests that do not use SVG templates.
    from src.productions.ppt.templates.svg.loader import load_svg_layout_templates_from_directory

    return load_svg_layout_templates_from_directory()
