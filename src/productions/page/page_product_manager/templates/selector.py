"""Deterministic selector for Page product built-in templates."""

from __future__ import annotations

from src.productions.page.page_product_manager.templates.models import (
    PageTemplate,
    PageTemplateMatch,
)

DEFAULT_PAGE_TEMPLATE_ID = ""


def select_page_template(
    brief: str,
    *,
    template_id: str | None = None,
    templates: tuple[PageTemplate, ...] = (),
) -> PageTemplateMatch:
    """Select the best built-in Page template for a user brief."""
    if not templates:
        raise ValueError("At least one page template is required.")

    template_by_id = {template.id: template for template in templates}
    clean_template_id = str(template_id or "").strip()
    if clean_template_id and clean_template_id in template_by_id:
        return PageTemplateMatch(
            template=template_by_id[clean_template_id],
            score=10_000,
            reasons=(f"Explicit template id: {clean_template_id}",),
        )

    normalized_brief = _normalize(brief)
    best_match: PageTemplateMatch | None = None
    for index, template in enumerate(templates):
        score, reasons = _score_template(normalized_brief, template)
        # Earlier library entries win exact ties so selection remains stable.
        adjusted_score = score * 100 - index
        candidate = PageTemplateMatch(
            template=template,
            score=score,
            reasons=tuple(reasons),
        )
        if best_match is None:
            best_match = candidate
            best_adjusted_score = adjusted_score
            continue
        if adjusted_score > best_adjusted_score:
            best_match = candidate
            best_adjusted_score = adjusted_score

    if best_match is None or best_match.score <= 0:
        return PageTemplateMatch(
            template=None,
            score=0,
            reasons=(
                "No built-in Page template matched the brief strongly enough; use free-form HTML generation.",
            ),
        )

    return best_match


def _score_template(text: str, template: PageTemplate) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    for term in template.trigger_terms:
        normalized_term = _normalize(term)
        if not normalized_term or normalized_term not in text:
            continue
        weight = _term_weight(normalized_term)
        score += weight
        reasons.append(f"Matched trigger term: {term}")

    for tag in template.tags:
        normalized_tag = _normalize(tag)
        tag_value = normalized_tag.split(":", maxsplit=1)[-1]
        if len(tag_value) >= 2 and tag_value in text:
            score += 3
            reasons.append(f"Matched tag: {tag}")

    return score, reasons


def _term_weight(term: str) -> int:
    if len(term) >= 8:
        return 12
    if len(term) >= 4:
        return 9
    return 6


def _normalize(value: str) -> str:
    return str(value or "").strip().lower()
