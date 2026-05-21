"""Provider registry and model factory helpers for Creative Claw."""

from __future__ import annotations

from dataclasses import dataclass

from google.adk.models import BaseLlm, Gemini, LiteLlm

from conf.app_config import load_app_config
from conf.openai_codex import OpenAICodexLlm

DEEPSEEK_V4_MODEL_NAMES = ("deepseek-v4-pro", "deepseek-v4-flash")
PROVIDER_ALIASES = {"openai-codex": "openai_codex"}


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Metadata needed to construct one ADK model backend."""

    name: str
    kind: str
    default_api_base: str = ""
    model_prefix: str = ""
    default_api_version: str = ""
    known_models: tuple[str, ...] = ()
    native_structured_output: bool = False


PROVIDERS: dict[str, ProviderSpec] = {
    "custom": ProviderSpec(name="custom", kind="openai_compatible"),
    "azure_openai": ProviderSpec(
        name="azure_openai",
        kind="azure",
        model_prefix="azure",
        default_api_version="2024-10-21",
        native_structured_output=True,
    ),
    "openai_codex": ProviderSpec(
        name="openai_codex",
        kind="codex_oauth",
        model_prefix="openai_codex",
        default_api_base="https://chatgpt.com/backend-api/codex/responses",
        native_structured_output=True,
    ),
    "anthropic": ProviderSpec(name="anthropic", kind="litellm_prefix", model_prefix="anthropic"),
    "openai": ProviderSpec(
        name="openai",
        kind="litellm_prefix",
        model_prefix="openai",
        native_structured_output=True,
    ),
    "openrouter": ProviderSpec(
        name="openrouter",
        kind="litellm_prefix",
        model_prefix="openrouter",
        default_api_base="https://openrouter.ai/api/v1",
    ),
    "deepseek": ProviderSpec(
        name="deepseek",
        kind="litellm_prefix",
        model_prefix="deepseek",
        default_api_base="https://api.deepseek.com",
        known_models=DEEPSEEK_V4_MODEL_NAMES,
    ),
    "groq": ProviderSpec(name="groq", kind="litellm_prefix", model_prefix="groq"),
    "zhipu": ProviderSpec(
        name="zhipu",
        kind="openai_compatible",
        default_api_base="https://open.bigmodel.cn/api/paas/v4",
    ),
    "dashscope": ProviderSpec(
        name="dashscope",
        kind="openai_compatible",
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    "vllm": ProviderSpec(name="vllm", kind="openai_compatible"),
    "ollama": ProviderSpec(name="ollama", kind="litellm_prefix", model_prefix="ollama"),
    "gemini": ProviderSpec(name="gemini", kind="gemini", native_structured_output=True),
    "moonshot": ProviderSpec(
        name="moonshot",
        kind="openai_compatible",
        default_api_base="https://api.moonshot.ai/v1",
    ),
    "minimax": ProviderSpec(
        name="minimax",
        kind="openai_compatible",
        default_api_base="https://api.minimax.chat/v1",
    ),
    "mistral": ProviderSpec(name="mistral", kind="litellm_prefix", model_prefix="mistral"),
    "stepfun": ProviderSpec(
        name="stepfun",
        kind="openai_compatible",
        default_api_base="https://api.stepfun.com/v1",
    ),
    "siliconflow": ProviderSpec(
        name="siliconflow",
        kind="openai_compatible",
        default_api_base="https://api.siliconflow.cn/v1",
    ),
    "volcengine": ProviderSpec(
        name="volcengine",
        kind="openai_compatible",
        default_api_base="https://ark.cn-beijing.volces.com/api/v3",
    ),
    "byteplus": ProviderSpec(
        name="byteplus",
        kind="openai_compatible",
        default_api_base="https://ark.ap-southeast.bytepluses.com/api/v3",
    ),
    "qianfan": ProviderSpec(
        name="qianfan",
        kind="openai_compatible",
        default_api_base="https://qianfan.baidubce.com/v2",
    ),
}


def build_llm(
    model_reference: str | None = None,
    *,
    provider_override: str | None = None,
) -> BaseLlm:
    """Construct one ADK model backend from the current runtime config."""
    app_config = load_app_config()
    provider_name, model_name = resolve_provider_and_model(
        model_reference,
        provider_override=provider_override,
    )
    provider_config = getattr(app_config.providers, provider_name)
    spec = get_provider_spec(provider_name)

    if spec.kind == "gemini":
        return Gemini(model=model_name)
    if spec.kind == "codex_oauth":
        return OpenAICodexLlm(
            model=f"{spec.model_prefix}/{model_name}",
            api_base=provider_config.api_base or spec.default_api_base,
        )

    kwargs: dict[str, object] = {}
    if provider_config.api_key:
        kwargs["api_key"] = provider_config.api_key
    if provider_config.extra_headers:
        kwargs["extra_headers"] = provider_config.extra_headers

    api_base = provider_config.api_base or spec.default_api_base
    if api_base:
        kwargs["api_base"] = api_base

    if spec.kind == "litellm_prefix":
        return LiteLlm(model=f"{spec.model_prefix}/{model_name}", **kwargs)

    if spec.kind == "azure":
        api_version = provider_config.api_version or spec.default_api_version
        if api_version:
            kwargs["api_version"] = api_version
        return LiteLlm(model=f"{spec.model_prefix}/{model_name}", **kwargs)

    kwargs["custom_llm_provider"] = "openai"
    return LiteLlm(model=model_name, **kwargs)


def resolve_llm_model_name(model_reference: str | None = None) -> str:
    """Return the fully-qualified provider/model name used for logs and results."""
    provider_name, model_name = resolve_provider_and_model(model_reference)
    return f"{provider_name}/{model_name}"


def resolve_structured_output_mode(model_reference: str | None = None) -> str:
    """Return the Orchestrator structured-output strategy for one model."""
    app_config = load_app_config()
    requested_mode = _normalize_structured_output_mode(app_config.llm.structured_output_mode)
    if requested_mode != "auto":
        return requested_mode

    provider_name, _ = resolve_provider_and_model(model_reference)
    spec = get_provider_spec(provider_name)
    return "native" if spec.native_structured_output else "prompt_json"


def resolve_provider_and_model(
    model_reference: str | None = None,
    *,
    provider_override: str | None = None,
) -> tuple[str, str]:
    """Resolve one provider name and model name from config plus optional overrides."""
    app_config = load_app_config()
    provider_name = _normalize_provider_name(provider_override or app_config.llm.provider)
    model_name = (model_reference or app_config.llm.model).strip()

    if model_reference and "/" in model_reference:
        prefix, bare_model = model_reference.split("/", 1)
        normalized_prefix = _normalize_provider_name(prefix)
        if normalized_prefix in PROVIDERS:
            provider_name = normalized_prefix
            model_name = bare_model.strip()

    if provider_name not in PROVIDERS:
        supported = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unsupported LLM provider '{provider_name}'. Supported providers: {supported}.")
    if not model_name:
        raise ValueError("LLM model name cannot be empty.")
    return provider_name, model_name


def _normalize_provider_name(name: str | None) -> str:
    """Return the canonical provider name for config and model references."""
    cleaned = str(name or "").strip()
    return PROVIDER_ALIASES.get(cleaned, cleaned)


def _normalize_structured_output_mode(mode: str | None) -> str:
    """Return a supported structured-output mode."""
    cleaned = str(mode or "auto").strip().lower()
    if cleaned not in {"auto", "native", "prompt_json"}:
        raise ValueError(
            "llm.structured_output_mode must be one of: auto, native, prompt_json."
        )
    return cleaned


def get_provider_spec(name: str) -> ProviderSpec:
    """Return one provider spec or raise a clear error."""
    try:
        return PROVIDERS[name]
    except KeyError as exc:  # pragma: no cover - defensive guard
        supported = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unsupported LLM provider '{name}'. Supported providers: {supported}.") from exc


def get_known_provider_models(name: str) -> tuple[str, ...]:
    """Return explicitly documented model ids for one provider."""
    return get_provider_spec(name).known_models
