"""Video generation expert for Creative Claw."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator
from typing_extensions import override

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from pydantic import BaseModel, Field, field_validator, model_validator

from src.agents.experts.base import CreativeExpert
from src.agents.experts.video_generation.capabilities import (
    get_default_video_duration,
    get_default_video_resolution,
    get_video_generation_model_name,
    normalize_dashscope_video_model_name,
    normalize_provider_video_aspect_ratio,
    normalize_provider_video_duration,
    normalize_provider_video_mode,
    normalize_provider_video_resolution,
    normalize_seedance_model_name,
    normalize_seedance_video_duration,
    normalize_seedance_video_resolution,
    normalize_video_prompt_rewrite,
    normalize_video_provider,
)
from src.agents.experts.video_generation import tool as video_tools
from src.agents.experts.schema_utils import (
    as_non_empty_string_list,
    as_prompt_list,
    clean_string,
    current_output_dict,
)
from src.logger import logger
from src.runtime.workspace import build_workspace_file_record, save_binary_output


VIDEO_PROMPT_ENHANCEMENT_ENABLED = False


def _effective_model_name(
    *,
    provider: str,
    mode: str,
    seedance_model_name: str,
    dashscope_model_name: str,
    kling_model_name: str,
) -> str:
    """Return the effective video model name selected for provider dispatch logging."""
    if provider == "seedance":
        return seedance_model_name
    if provider == "dashscope":
        return dashscope_model_name
    if provider == "kling":
        return kling_model_name or get_video_generation_model_name("kling", mode=mode)
    return get_video_generation_model_name(provider, mode=mode)


async def _prepare_prompts(
    ctx: InvocationContext,
    prompt_list: list[str],
    *,
    prompt_rewrite: str,
) -> list[str]:
    """Return prompts after applying the agent-side rewrite policy."""
    if prompt_rewrite == "off" or not VIDEO_PROMPT_ENHANCEMENT_ENABLED:
        return prompt_list

    enhanced_prompt_results = await asyncio.gather(
        *[
            video_tools.prompt_enhancement_tool(ctx, prompt)
            if prompt
            else asyncio.sleep(0, result={"status": "success", "message": ""})
            for prompt in prompt_list
        ]
    )
    normalized_prompts: list[str] = []
    for original_prompt, result in zip(prompt_list, enhanced_prompt_results):
        if result["status"] == "success":
            normalized_prompts.append(str(result["message"]).strip())
        else:
            logger.warning(
                "VideoGenerationAgent: prompt enhancement failed, using original prompt: {}",
                result.get("message", "unknown error"),
            )
            normalized_prompts.append(original_prompt)
    return normalized_prompts


def _parse_optional_bool(value) -> bool | None:
    """Return a bool for explicit values and None when the parameter is absent."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_bool(value, *, default: bool = False) -> bool:
    """Return a bool for flexible user-provided values."""
    parsed = _parse_optional_bool(value)
    return default if parsed is None else parsed


class VideoGenerationParameters(BaseModel):
    """Structured request contract read from ``current_parameters``."""

    prompt_list: list[str] = Field(default_factory=lambda: [""])
    input_paths: list[str] = Field(default_factory=list)
    input_urls: list[str] = Field(default_factory=list)
    provider: str = "seedance"
    mode: str = "prompt"
    seedance_model_name: str = ""
    dashscope_model_name: str = ""
    kling_model_name: str = ""
    aspect_ratio: str = "16:9"
    resolution: str = ""
    duration_seconds: int = 5
    negative_prompt: str = ""
    kling_mode: str = "std"
    seedance_generate_audio: bool | None = None
    watermark: bool = False
    raw_prompt_rewrite: Any = None
    raw_person_generation: Any = None
    raw_seed: Any = None
    raw_prompt_extend: Any = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_mapping(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            value = {}
        provider = normalize_video_provider(value.get("provider"))
        mode = normalize_provider_video_mode(provider, value.get("mode", "prompt"))
        seedance_model_name = normalize_seedance_model_name(value.get("model_name"))
        dashscope_model_name = normalize_dashscope_video_model_name(value.get("model_name"), mode=mode)

        if provider == "kling":
            aspect_ratio = normalize_provider_video_aspect_ratio(
                provider,
                value.get("aspect_ratio", "16:9"),
            )
            resolution = ""
            duration_seconds = int(
                normalize_provider_video_duration(
                    provider,
                    value.get("duration_seconds", get_default_video_duration(provider) or 5),
                    mode=mode,
                )
                or get_default_video_duration(provider)
                or 5
            )
        elif provider == "seedance":
            aspect_ratio = normalize_provider_video_aspect_ratio(
                provider,
                value.get("aspect_ratio", "16:9"),
            )
            resolution = normalize_seedance_video_resolution(
                seedance_model_name,
                value.get("resolution", ""),
            )
            duration_seconds = normalize_seedance_video_duration(
                seedance_model_name,
                value.get("duration_seconds"),
            )
        elif provider == "dashscope":
            aspect_ratio = normalize_provider_video_aspect_ratio(
                provider,
                value.get("aspect_ratio", "16:9"),
            )
            resolution = normalize_provider_video_resolution(
                provider,
                value.get("resolution", get_default_video_resolution(provider)),
            )
            duration_seconds = int(
                normalize_provider_video_duration(
                    provider,
                    value.get("duration_seconds", get_default_video_duration(provider) or 5),
                    mode=mode,
                )
                or get_default_video_duration(provider)
                or 5
            )
        else:
            aspect_ratio = normalize_provider_video_aspect_ratio(
                provider,
                value.get("aspect_ratio", "16:9"),
            )
            resolution = normalize_provider_video_resolution(
                provider,
                value.get("resolution", get_default_video_resolution(provider)),
            )
            duration_seconds = int(
                normalize_provider_video_duration(
                    provider,
                    value.get("duration_seconds", get_default_video_duration(provider) or 8),
                    mode=mode,
                )
                or get_default_video_duration(provider)
                or 8
            )

        return {
            "prompt_list": value.get("prompt", ""),
            "input_paths": value.get("input_paths", value.get("input_path", [])),
            "input_urls": value.get(
                "image_urls",
                value.get("image_url", value.get("input_urls", value.get("input_url", []))),
            ),
            "provider": provider,
            "mode": mode,
            "seedance_model_name": seedance_model_name,
            "dashscope_model_name": dashscope_model_name,
            "kling_model_name": value.get("model_name", ""),
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "duration_seconds": duration_seconds,
            "negative_prompt": value.get("negative_prompt", ""),
            "kling_mode": video_tools.normalize_kling_mode(value.get("kling_mode", "std")),
            "seedance_generate_audio": _parse_optional_bool(value.get("generate_audio")),
            "watermark": _parse_bool(value.get("watermark"), default=False),
            "raw_prompt_rewrite": value.get("prompt_rewrite"),
            "raw_person_generation": value.get("person_generation"),
            "raw_seed": value.get("seed"),
            "raw_prompt_extend": value.get("prompt_extend"),
        }

    @field_validator("prompt_list", mode="before")
    @classmethod
    def _normalize_prompt_list(cls, value: Any) -> list[str]:
        return as_prompt_list(value, default_empty_prompt=True)

    @field_validator("input_paths", "input_urls", mode="before")
    @classmethod
    def _normalize_input_list(cls, value: Any) -> list[str]:
        return as_non_empty_string_list(value)

    @field_validator(
        "provider",
        "mode",
        "seedance_model_name",
        "dashscope_model_name",
        "kling_model_name",
        "aspect_ratio",
        "resolution",
        "negative_prompt",
        "kling_mode",
        mode="before",
    )
    @classmethod
    def _strip_string(cls, value: Any) -> str:
        return clean_string(value)

    @property
    def missing_generation_inputs(self) -> bool:
        """Whether the request lacks prompt, local input, and remote input values."""
        return not any(self.prompt_list) and not self.input_paths and not self.input_urls

    @property
    def missing_mode_inputs(self) -> bool:
        """Whether a non-prompt mode is missing provider-supported visual inputs."""
        return self.mode != "prompt" and not self.input_paths and not (
            self.provider == "dashscope" and self.input_urls
        )

    def prompt_rewrite(self) -> str:
        """Return the validated prompt rewrite policy."""
        return normalize_video_prompt_rewrite(self.raw_prompt_rewrite)

    def person_generation(self) -> str | None:
        """Return the validated Veo person-generation policy."""
        return video_tools.normalize_person_generation(self.raw_person_generation)

    def seed(self) -> int | None:
        """Return the provider-specific normalized seed value."""
        if self.provider == "seedance":
            return video_tools.normalize_seedance_seed(self.raw_seed)
        return video_tools.normalize_video_seed(self.raw_seed)

    def prompt_extend(self, *, prompt_rewrite: str) -> bool:
        """Return the DashScope prompt-extension setting for the current request."""
        explicit_prompt_extend = _parse_optional_bool(self.raw_prompt_extend)
        return prompt_rewrite != "off" if explicit_prompt_extend is None else explicit_prompt_extend

    def validation_inputs(self) -> list[str]:
        """Return the mode-validation inputs for the selected provider."""
        return [*self.input_paths, *self.input_urls] if self.provider == "dashscope" else self.input_paths


class VideoGenerationOutput(BaseModel):
    """Structured ``current_output`` contract emitted by ``VideoGenerationAgent``."""

    status: str
    message: str
    output_files: list[dict[str, Any]] | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value: Any) -> str:
        return clean_string(value).lower() or "error"

    @field_validator("message", mode="before")
    @classmethod
    def _strip_message(cls, value: Any) -> str:
        return clean_string(value)

    def to_current_output(self) -> dict[str, Any]:
        """Return the stable dictionary payload stored in ADK session state."""
        return current_output_dict(self)


class VideoGenerationAgent(CreativeExpert):
    """Generate one or more videos from prompt, image, or video-guided inputs."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the video generation expert."""
        super().__init__(name=name, sub_agents=[], description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run the video expert with the normalized session parameters."""
        current_parameters = VideoGenerationParameters.model_validate(
            ctx.session.state.get("current_parameters", {})
        )

        if current_parameters.missing_generation_inputs:
            error_text = (
                f"Missing parameters provided to {self.name}, must include prompt, "
                "input_path/input_paths, or image_url/image_urls."
            )
            current_output = VideoGenerationOutput(status="error", message=error_text).to_current_output()
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": current_output})
            return

        if current_parameters.missing_mode_inputs:
            error_text = (
                f"{self.name} requires input_path/input_paths or image_url/image_urls "
                f"when mode is {current_parameters.mode}."
            )
            current_output = VideoGenerationOutput(status="error", message=error_text).to_current_output()
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": current_output})
            return

        try:
            prompt_rewrite = current_parameters.prompt_rewrite()
            person_generation = current_parameters.person_generation()
            seed = current_parameters.seed()
            video_tools._validate_mode_input_paths(
                current_parameters.provider,
                current_parameters.mode,
                current_parameters.validation_inputs(),
            )
        except ValueError as exc:
            error_text = f"{self.name} got invalid parameters: {exc}"
            current_output = VideoGenerationOutput(status="error", message=error_text).to_current_output()
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": current_output})
            return

        normalized_prompts = await _prepare_prompts(
            ctx,
            current_parameters.prompt_list,
            prompt_rewrite=prompt_rewrite,
        )
        effective_model_name = _effective_model_name(
            provider=current_parameters.provider,
            mode=current_parameters.mode,
            seedance_model_name=current_parameters.seedance_model_name,
            dashscope_model_name=current_parameters.dashscope_model_name,
            kling_model_name=current_parameters.kling_model_name,
        )
        logger.info(
            (
                "VideoGenerationAgent dispatch: provider={} mode={} model_name={} "
                "aspect_ratio={} resolution={} duration_seconds={} prompt_rewrite={} "
                "generate_audio={} prompt_count={} input_path_count={} input_url_count={}"
            ),
            current_parameters.provider,
            current_parameters.mode,
            effective_model_name,
            current_parameters.aspect_ratio,
            current_parameters.resolution,
            current_parameters.duration_seconds,
            prompt_rewrite,
            current_parameters.seedance_generate_audio,
            len(normalized_prompts),
            len(current_parameters.input_paths),
            len(current_parameters.input_urls),
        )

        if current_parameters.provider == "veo":
            generation_tasks = [
                video_tools.veo_video_generation_tool(
                    prompt,
                    input_paths=current_parameters.input_paths,
                    mode=current_parameters.mode,
                    aspect_ratio=current_parameters.aspect_ratio,
                    resolution=current_parameters.resolution,
                    duration_seconds=current_parameters.duration_seconds,
                    negative_prompt=current_parameters.negative_prompt,
                    person_generation=person_generation,
                    seed=seed,
                )
                for prompt in normalized_prompts
            ]
        elif current_parameters.provider == "kling":
            generation_tasks = [
                video_tools.kling_video_generation_tool(
                    prompt,
                    input_paths=current_parameters.input_paths,
                    mode=current_parameters.mode,
                    aspect_ratio=current_parameters.aspect_ratio,
                    duration_seconds=current_parameters.duration_seconds,
                    negative_prompt=current_parameters.negative_prompt,
                    model_name=current_parameters.kling_model_name,
                    kling_mode=current_parameters.kling_mode,
                )
                for prompt in normalized_prompts
            ]
        elif current_parameters.provider == "dashscope":
            prompt_extend = current_parameters.prompt_extend(prompt_rewrite=prompt_rewrite)
            generation_tasks = [
                video_tools.dashscope_video_generation_tool(
                    prompt,
                    input_paths=current_parameters.input_paths,
                    input_urls=current_parameters.input_urls,
                    mode=current_parameters.mode,
                    aspect_ratio=current_parameters.aspect_ratio,
                    model_name=current_parameters.dashscope_model_name,
                    resolution=current_parameters.resolution,
                    duration_seconds=current_parameters.duration_seconds,
                    prompt_extend=prompt_extend,
                    watermark=current_parameters.watermark,
                    seed=seed,
                )
                for prompt in normalized_prompts
            ]
        else:
            generation_tasks = [
                video_tools.seedance_video_generation_tool(
                    prompt,
                    input_paths=current_parameters.input_paths,
                    mode=current_parameters.mode,
                    aspect_ratio=current_parameters.aspect_ratio,
                    model_name=current_parameters.seedance_model_name,
                    resolution=current_parameters.resolution,
                    duration_seconds=current_parameters.duration_seconds,
                    generate_audio=current_parameters.seedance_generate_audio,
                    watermark=current_parameters.watermark,
                    seed=seed,
                )
                for prompt in normalized_prompts
            ]
        result_list = await asyncio.gather(*generation_tasks)

        current_turn = int(ctx.session.state.get("turn_index", 0) or 0)
        current_step = int(ctx.session.state.get("step", 0) or 0)
        current_expert_step = int(ctx.session.state.get("expert_step", 0) or 0)
        output_files = []
        messages: list[str] = []
        for index, (prompt, result) in enumerate(zip(normalized_prompts, result_list)):
            if result["status"] == "error":
                messages.append(f"video task {index + 1} failed: {result['message']}")
                continue

            output_path = save_binary_output(
                result["message"],
                session_id=ctx.session.id,
                turn_index=current_turn,
                step=current_step,
                output_type="video_generation",
                index=index,
                extension=".mp4",
            )
            artifact_name = output_path.name
            provider_name = result.get("provider", current_parameters.provider)
            messages.append(f"video task {index + 1} succeeded, output file: {artifact_name}")
            description = (
                f"The {index + 1}th video generated by video generation tool in "
                f"turn {current_turn}, step {current_step}, provider is {provider_name}, "
                f"mode is {current_parameters.mode}, prompt is {prompt}"
            )
            output_files.append(
                build_workspace_file_record(
                    output_path,
                    description=description,
                    source="expert",
                    name=artifact_name,
                    turn=current_turn,
                    step=current_step,
                    expert_step=current_expert_step,
                )
            )

        if not output_files:
            message = f"{self.name} all {len(result_list)} video generation tasks failed: {', '.join(messages)}"
            current_output = VideoGenerationOutput(status="error", message=message).to_current_output()
            logger.error(message)
            yield self.format_event(message, {"current_output": current_output})
            return

        message = f"{self.name} has completed {len(result_list)} video generation tasks: {', '.join(messages)}"
        current_output = VideoGenerationOutput(
            status="success",
            message=message,
            output_files=output_files,
        ).to_current_output()
        logger.info(message)
        yield self.format_event(message, {"current_output": current_output})


__all__ = [
    "VideoGenerationAgent",
    "VideoGenerationOutput",
    "VideoGenerationParameters",
]
