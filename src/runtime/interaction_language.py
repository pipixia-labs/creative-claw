"""Helpers for product-facing user interaction language."""

from __future__ import annotations

import re
from typing import Any

INTERACTION_LANGUAGE_STATE_KEY = "interaction_language"

LANGUAGE_EN = "en"
LANGUAGE_ZH = "zh"

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_ASCII_ALPHA_RE = re.compile(r"[A-Za-z]")


def normalize_interaction_language(value: Any, *, fallback: str | None = LANGUAGE_EN) -> str:
    """Return the canonical interaction language code supported by the runtime."""
    normalized = str(value or "").strip().lower().replace("_", "-")
    if normalized in {"zh", "zh-cn", "zh-hans", "cn", "chinese", "simplified-chinese"}:
        return LANGUAGE_ZH
    if normalized in {"en", "en-us", "en-gb", "english"}:
        return LANGUAGE_EN
    if fallback:
        return normalize_interaction_language(fallback, fallback=LANGUAGE_EN)
    return ""


def detect_interaction_language(*texts: Any, default: str = LANGUAGE_EN) -> str:
    """Infer the conversation language from the latest available user-facing text."""
    for text in texts:
        candidate = str(text or "").strip()
        if not candidate:
            continue
        cjk_count = len(_CJK_RE.findall(candidate))
        alpha_count = len(_ASCII_ALPHA_RE.findall(candidate))
        if cjk_count >= 4 or (cjk_count > 0 and cjk_count >= alpha_count):
            return LANGUAGE_ZH
        if alpha_count > 0:
            return LANGUAGE_EN
    return normalize_interaction_language(default)


def resolve_interaction_language(
    *,
    explicit: Any = "",
    state: Any | None = None,
    texts: tuple[Any, ...] = (),
    default: str = LANGUAGE_EN,
) -> str:
    """Resolve the interaction language from explicit input, state, then text."""
    explicit_language = normalize_interaction_language(explicit, fallback="")
    if explicit_language:
        return explicit_language
    state_language = ""
    if state is not None and hasattr(state, "get"):
        state_language = state.get(INTERACTION_LANGUAGE_STATE_KEY, "")
    normalized_state_language = normalize_interaction_language(state_language, fallback="")
    if normalized_state_language:
        return normalized_state_language
    return detect_interaction_language(*texts, default=default)


def language_name_for_prompt(language: Any) -> str:
    """Return the English language name used inside model instructions."""
    return "Simplified Chinese" if normalize_interaction_language(language) == LANGUAGE_ZH else "English"


def localized_copy(language: Any, *, en: str, zh: str) -> str:
    """Return a short user-facing copy string for the interaction language."""
    return zh if normalize_interaction_language(language) == LANGUAGE_ZH else en


__all__ = [
    "INTERACTION_LANGUAGE_STATE_KEY",
    "LANGUAGE_EN",
    "LANGUAGE_ZH",
    "detect_interaction_language",
    "language_name_for_prompt",
    "localized_copy",
    "normalize_interaction_language",
    "resolve_interaction_language",
]
