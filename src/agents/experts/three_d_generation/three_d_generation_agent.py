"""3D generation expert for Creative Claw."""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncGenerator

from typing_extensions import override

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from pydantic import BaseModel, Field, PrivateAttr, field_validator

from src.agents.experts.base import CreativeExpert
from src.agents.experts.three_d_generation.prompt_optimizer import (
    HYPER3D_PROMPT_CHARACTER_LIMIT,
    PromptOptimizationResult,
    optimize_3d_prompt,
)
from src.agents.experts.three_d_generation import tool as generation_tools
from src.agents.experts.schema_utils import (
    as_non_empty_string_list,
    clean_string,
    current_output_dict,
)
from src.logger import logger
from src.runtime.workspace import build_workspace_file_record


def _normalize_3d_prompt(raw_prompt: Any) -> str:
    """Normalize one prompt value into a single string."""
    if isinstance(raw_prompt, list):
        prompt_list = [str(item).strip() for item in raw_prompt if str(item).strip()]
        if len(prompt_list) > 1:
            raise ValueError("3DGeneration currently supports only one prompt at a time.")
        return prompt_list[0] if prompt_list else ""
    return clean_string(raw_prompt)


def _normalize_3d_input_paths(current_parameters: dict[str, Any]) -> list[str]:
    """Normalize input image paths from current parameters."""
    input_paths = current_parameters.get("input_paths", current_parameters.get("input_path", []))
    return as_non_empty_string_list(input_paths)


def _normalize_3d_image_urls(current_parameters: dict[str, Any]) -> list[str]:
    """Normalize input image URLs from current parameters."""
    urls: list[str] = []
    for key in ("image_url", "image_urls"):
        urls.extend(as_non_empty_string_list(current_parameters.get(key, [])))
    return urls


class ThreeDGenerationParameters(BaseModel):
    """Structured request contract read from ``current_parameters``."""

    raw_parameters: dict[str, Any] = Field(default_factory=dict)
    provider: str = "hy3d"
    prompt: str = ""
    input_paths: list[str] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)

    @classmethod
    def from_raw(cls, raw_parameters: Any) -> "ThreeDGenerationParameters":
        """Parse raw session parameters while preserving existing normalization errors."""
        current_parameters = dict(raw_parameters) if isinstance(raw_parameters, dict) else {}
        provider = clean_string(current_parameters.get("provider", "hy3d")).lower() or "hy3d"
        return cls(
            raw_parameters=current_parameters,
            provider=provider,
            prompt=_normalize_3d_prompt(current_parameters.get("prompt", "")),
            input_paths=_normalize_3d_input_paths(current_parameters),
            image_urls=_normalize_3d_image_urls(current_parameters),
        )


class ThreeDGenerationResultItem(BaseModel):
    """One generated 3D file entry stored in ``three_d_generation_results``."""

    path: str
    name: str
    type: str = ""
    preview_image_url: str = ""
    url: str = ""

    @field_validator("path", "name", "type", "preview_image_url", "url", mode="before")
    @classmethod
    def _strip_string(cls, value: Any) -> str:
        return clean_string(value)

    def to_result(self) -> dict[str, Any]:
        """Return the stable dictionary item stored in ADK session state."""
        return self.model_dump(mode="python")


class ThreeDGenerationOutput(BaseModel):
    """Structured ``current_output`` contract emitted by ``ThreeDGenerationAgent``."""

    status: str
    message: str
    output_files: list[dict[str, Any]] | None = None
    provider: str | None = None
    model_name: str | None = None
    job_id: str | None = None
    generate_type: str | None = None
    file_format: str | None = None
    subdivision_level: str | None = None
    resolution: str | None = None
    result_files: list[dict[str, Any]] | None = None
    prompt_optimization: dict[str, Any] | None = None
    optimized_prompt: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value: Any) -> str:
        return clean_string(value).lower() or "error"

    @field_validator(
        "message",
        "provider",
        "model_name",
        "job_id",
        "generate_type",
        "file_format",
        "subdivision_level",
        "resolution",
        "optimized_prompt",
        mode="before",
    )
    @classmethod
    def _strip_optional_string(cls, value: Any) -> str | None:
        if value is None:
            return None
        return clean_string(value)

    def to_current_output(self) -> dict[str, Any]:
        """Return the stable dictionary payload stored in ADK session state."""
        return current_output_dict(self)


class ThreeDGenerationAgent(CreativeExpert):
    """Generate 3D assets through provider-specific tools."""

    _public_name: str = PrivateAttr(default="3DGeneration")

    def __init__(self, name: str, description: str = "", public_name: str = "3DGeneration") -> None:
        """Initialize the 3D generation expert."""
        super().__init__(name=name, sub_agents=[], description=description)
        self._public_name = public_name

    @staticmethod
    def _normalize_prompt(raw_prompt: Any) -> str:
        """Normalize one prompt value into a single string."""
        return _normalize_3d_prompt(raw_prompt)

    @staticmethod
    def _normalize_input_paths(current_parameters: dict[str, Any]) -> list[str]:
        """Normalize input image paths from current parameters."""
        return _normalize_3d_input_paths(current_parameters)

    @staticmethod
    def _normalize_image_urls(current_parameters: dict[str, Any]) -> list[str]:
        """Normalize input image URLs from current parameters."""
        return _normalize_3d_image_urls(current_parameters)

    @staticmethod
    def _optional_bool(current_parameters: dict[str, Any], *keys: str) -> bool | None:
        """Return an optional boolean parameter from the first present key."""
        for key in keys:
            if key in current_parameters and current_parameters.get(key) not in (None, ""):
                return generation_tools.coerce_bool(current_parameters.get(key))
        return None

    @staticmethod
    def _provider_model(
        current_parameters: dict[str, Any],
        *,
        provider: str,
        default_model: str,
    ) -> str:
        """Return the model ID for a provider, ignoring the global hy3d default."""
        raw_model = str(current_parameters.get("model", "") or "").strip()
        if provider != "hy3d" and raw_model in {"", generation_tools.DEFAULT_MODEL, "3.1"}:
            return default_model
        return raw_model or default_model

    @staticmethod
    def _hy3d_face_count(current_parameters: dict[str, Any]) -> Any:
        """Return the hy3d face-count request value with the quality default."""
        face_count = current_parameters.get("face_count")
        if face_count in (None, ""):
            return generation_tools.DEFAULT_HY3D_FACE_COUNT
        return face_count

    @staticmethod
    def _prompt_optimization_enabled(current_parameters: dict[str, Any]) -> bool:
        """Return whether the private prompt optimizer should run for this request."""
        try:
            return generation_tools.coerce_bool(
                current_parameters.get("optimize_prompt", current_parameters.get("prompt_optimization")),
                default=True,
            )
        except ValueError as exc:
            logger.warning("Invalid prompt optimization flag; defaulting to enabled. error={!r}", exc)
            return True

    async def _optimize_prompt_for_request(
        self,
        ctx: InvocationContext,
        *,
        prompt: str,
        provider: str,
        generate_type: str,
        input_paths: list[str],
        image_urls: list[str],
        current_parameters: dict[str, Any],
    ) -> PromptOptimizationResult | None:
        """Run the private prompt optimizer when text guidance is useful."""
        normalized_prompt = prompt.strip()
        if not normalized_prompt or not self._prompt_optimization_enabled(current_parameters):
            return None

        max_characters = HYPER3D_PROMPT_CHARACTER_LIMIT if provider == "hyper3d" else None
        return await optimize_3d_prompt(
            ctx,
            prompt=normalized_prompt,
            provider=provider,
            generate_type=generate_type,
            has_input_image=bool(input_paths or image_urls),
            max_characters=max_characters,
        )

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run the 3D generation expert with normalized session parameters."""
        try:
            request = ThreeDGenerationParameters.from_raw(ctx.session.state.get("current_parameters", {}))
        except ValueError as exc:
            error_text = f"{self._public_name} parameter normalization failed: {exc}"
            logger.error(error_text)
            current_output = ThreeDGenerationOutput(status="error", message=error_text).to_current_output()
            yield self.format_event(error_text, {"current_output": current_output})
            return

        current_parameters = request.raw_parameters
        provider = request.provider
        prompt = request.prompt
        input_paths = request.input_paths
        image_urls = request.image_urls

        if provider not in {"hy3d", "seed3d", "hyper3d", "hitem3d"}:
            error_text = (
                f"{self._public_name} supports providers `hy3d`, `seed3d`, "
                "`hyper3d`, and `hitem3d`."
            )
            logger.error(error_text)
            current_output = ThreeDGenerationOutput(status="error", message=error_text).to_current_output()
            yield self.format_event(error_text, {"current_output": current_output})
            return

        generate_type = ""
        prompt_optimization_result: PromptOptimizationResult | None = None
        if provider == "seed3d":
            if len(input_paths) + len(image_urls) != 1:
                error_text = f"{self._public_name} provider `seed3d` requires exactly one image source."
                logger.error(error_text)
                current_output = ThreeDGenerationOutput(status="error", message=error_text).to_current_output()
                yield self.format_event(error_text, {"current_output": current_output})
                return
            image_url = image_urls[0] if image_urls else None

            result = await generation_tools.seed3d_generate_tool(
                input_path=input_paths[0] if input_paths else None,
                image_url=image_url,
                model=self._provider_model(
                    current_parameters,
                    provider=provider,
                    default_model=generation_tools.DEFAULT_SEED3D_MODEL,
                ),
                file_format=str(
                    current_parameters.get(
                        "file_format",
                        current_parameters.get(
                            "result_format",
                            generation_tools.DEFAULT_SEED3D_FILE_FORMAT,
                        ),
                    )
                    or generation_tools.DEFAULT_SEED3D_FILE_FORMAT
                ),
                subdivision_level=str(
                    current_parameters.get(
                        "subdivision_level",
                        generation_tools.DEFAULT_SEED3D_SUBDIVISION_LEVEL,
                    )
                    or generation_tools.DEFAULT_SEED3D_SUBDIVISION_LEVEL
                ),
                timeout_seconds=int(
                    current_parameters.get("timeout_seconds", generation_tools.DEFAULT_TIMEOUT_SECONDS)
                ),
                interval_seconds=int(
                    current_parameters.get(
                        "interval_seconds",
                        generation_tools.DEFAULT_SEED3D_INTERVAL_SECONDS,
                    )
                ),
                session_id=ctx.session.id,
                turn_index=int(ctx.session.state.get("turn_index", 0) or 0),
                step=int(ctx.session.state.get("step", 0) or 0),
            )
        elif provider == "hyper3d":
            if len(input_paths) + len(image_urls) > 5:
                error_text = f"{self._public_name} provider `hyper3d` supports at most 5 images."
                logger.error(error_text)
                current_output = ThreeDGenerationOutput(status="error", message=error_text).to_current_output()
                yield self.format_event(error_text, {"current_output": current_output})
                return

            hyper3d_prompt = prompt
            prompt_optimization_result = await self._optimize_prompt_for_request(
                ctx,
                prompt=prompt,
                provider=provider,
                generate_type="image_to_3d" if input_paths or image_urls else "text_to_3d",
                input_paths=input_paths,
                image_urls=image_urls,
                current_parameters=current_parameters,
            )
            if prompt_optimization_result is not None:
                hyper3d_prompt = prompt_optimization_result.prompt

            result = await generation_tools.hyper3d_generate_tool(
                prompt=hyper3d_prompt or None,
                input_paths=input_paths,
                image_urls=image_urls,
                model=self._provider_model(
                    current_parameters,
                    provider=provider,
                    default_model=generation_tools.DEFAULT_HYPER3D_MODEL,
                ),
                file_format=str(
                    current_parameters.get(
                        "file_format",
                        current_parameters.get(
                            "result_format",
                            generation_tools.DEFAULT_HYPER3D_FILE_FORMAT,
                        ),
                    )
                    or generation_tools.DEFAULT_HYPER3D_FILE_FORMAT
                ),
                subdivision_level=(
                    str(current_parameters.get("subdivision_level", "")).strip() or None
                ),
                material=(str(current_parameters.get("material", "")).strip() or None),
                mesh_mode=(str(current_parameters.get("mesh_mode", "")).strip() or None),
                quality_override=current_parameters.get(
                    "quality_override",
                    current_parameters.get("face_count"),
                ),
                addons=(str(current_parameters.get("addons", "")).strip() or None),
                use_original_alpha=self._optional_bool(current_parameters, "use_original_alpha"),
                bbox_condition=current_parameters.get("bbox_condition"),
                ta_pose=self._optional_bool(current_parameters, "ta_pose", "TAPose"),
                hd_texture=self._optional_bool(current_parameters, "hd_texture"),
                timeout_seconds=int(
                    current_parameters.get("timeout_seconds", generation_tools.DEFAULT_TIMEOUT_SECONDS)
                ),
                interval_seconds=int(
                    current_parameters.get(
                        "interval_seconds",
                        generation_tools.DEFAULT_HYPER3D_INTERVAL_SECONDS,
                    )
                ),
                session_id=ctx.session.id,
                turn_index=int(ctx.session.state.get("turn_index", 0) or 0),
                step=int(ctx.session.state.get("step", 0) or 0),
            )
        elif provider == "hitem3d":
            if prompt:
                error_text = (
                    f"{self._public_name} provider `hitem3d` supports parameter text only; "
                    "do not pass a free-form prompt."
                )
                logger.error(error_text)
                current_output = ThreeDGenerationOutput(status="error", message=error_text).to_current_output()
                yield self.format_event(error_text, {"current_output": current_output})
                return
            if input_paths:
                error_text = (
                    f"{self._public_name} provider `hitem3d` requires externally accessible "
                    "`image_url` or `image_urls`, not local input paths."
                )
                logger.error(error_text)
                current_output = ThreeDGenerationOutput(status="error", message=error_text).to_current_output()
                yield self.format_event(error_text, {"current_output": current_output})
                return
            if not image_urls or len(image_urls) > 4:
                error_text = f"{self._public_name} provider `hitem3d` requires 1 to 4 image URLs."
                logger.error(error_text)
                current_output = ThreeDGenerationOutput(status="error", message=error_text).to_current_output()
                yield self.format_event(error_text, {"current_output": current_output})
                return

            result = await generation_tools.hitem3d_generate_tool(
                image_urls=image_urls,
                model=self._provider_model(
                    current_parameters,
                    provider=provider,
                    default_model=generation_tools.DEFAULT_HITEM3D_MODEL,
                ),
                file_format=str(
                    current_parameters.get(
                        "file_format",
                        current_parameters.get(
                            "result_format",
                            generation_tools.DEFAULT_HITEM3D_FILE_FORMAT,
                        ),
                    )
                    or generation_tools.DEFAULT_HITEM3D_FILE_FORMAT
                ),
                resolution=str(
                    current_parameters.get(
                        "resolution",
                        generation_tools.DEFAULT_HITEM3D_RESOLUTION,
                    )
                    or generation_tools.DEFAULT_HITEM3D_RESOLUTION
                ),
                face_count=current_parameters.get("face", current_parameters.get("face_count")),
                request_type=current_parameters.get("request_type"),
                multi_images_bit=(
                    str(current_parameters.get("multi_images_bit", "")).strip() or None
                ),
                timeout_seconds=int(
                    current_parameters.get("timeout_seconds", generation_tools.DEFAULT_TIMEOUT_SECONDS)
                ),
                interval_seconds=int(
                    current_parameters.get(
                        "interval_seconds",
                        generation_tools.DEFAULT_HITEM3D_INTERVAL_SECONDS,
                    )
                ),
                session_id=ctx.session.id,
                turn_index=int(ctx.session.state.get("turn_index", 0) or 0),
                step=int(ctx.session.state.get("step", 0) or 0),
            )
        else:
            if len(input_paths) > 1:
                error_text = f"{self._public_name} provider `hy3d` supports at most one input image."
                logger.error(error_text)
                current_output = ThreeDGenerationOutput(status="error", message=error_text).to_current_output()
                yield self.format_event(error_text, {"current_output": current_output})
                return
            if image_urls:
                error_text = f"{self._public_name} provider `hy3d` does not support `image_url` inputs."
                logger.error(error_text)
                current_output = ThreeDGenerationOutput(status="error", message=error_text).to_current_output()
                yield self.format_event(error_text, {"current_output": current_output})
                return
            generate_type = generation_tools.normalize_generate_type(
                current_parameters.get("generate_type", generation_tools.DEFAULT_GENERATE_TYPE)
            )
            if prompt and input_paths and generate_type != "Sketch":
                error_text = (
                    f"{self._public_name} requires `generate_type=sketch` when both prompt and input image are provided."
                )
                logger.error(error_text)
                current_output = ThreeDGenerationOutput(status="error", message=error_text).to_current_output()
                yield self.format_event(error_text, {"current_output": current_output})
                return

            hy3d_prompt = prompt
            prompt_optimization_result = await self._optimize_prompt_for_request(
                ctx,
                prompt=prompt,
                provider=provider,
                generate_type=generate_type,
                input_paths=input_paths,
                image_urls=image_urls,
                current_parameters=current_parameters,
            )
            if prompt_optimization_result is not None:
                hy3d_prompt = prompt_optimization_result.prompt
            result = await generation_tools.hy3d_generate_tool(
                prompt=hy3d_prompt or None,
                input_path=input_paths[0] if input_paths else None,
                model=self._provider_model(
                    current_parameters,
                    provider=provider,
                    default_model=generation_tools.DEFAULT_MODEL,
                ),
                enable_pbr=generation_tools.coerce_bool(
                    current_parameters.get("enable_pbr"),
                    default=generation_tools.DEFAULT_HY3D_ENABLE_PBR,
                ),
                generate_type=generate_type,
                face_count=self._hy3d_face_count(current_parameters),
                polygon_type=(
                    str(current_parameters.get("polygon_type", "")).strip() or None
                ),
                result_format=(
                    str(current_parameters.get("result_format", "")).strip() or None
                ),
                timeout_seconds=int(
                    current_parameters.get("timeout_seconds", generation_tools.DEFAULT_TIMEOUT_SECONDS)
                ),
                interval_seconds=int(
                    current_parameters.get("interval_seconds", generation_tools.DEFAULT_INTERVAL_SECONDS)
                ),
                session_id=ctx.session.id,
                turn_index=int(ctx.session.state.get("turn_index", 0) or 0),
                step=int(ctx.session.state.get("step", 0) or 0),
            )

        if result["status"] == "error":
            prompt_optimization_payload = None
            if prompt_optimization_result is not None:
                prompt_optimization_payload = {
                    "used_llm": prompt_optimization_result.used_llm,
                    "model_name": prompt_optimization_result.model_name,
                    "message": prompt_optimization_result.message,
                }
            current_output = ThreeDGenerationOutput(
                status="error",
                message=result["message"],
                provider=result.get("provider", provider),
                model_name=result.get("model_name", ""),
                job_id=result.get("job_id", ""),
                prompt_optimization=prompt_optimization_payload,
            ).to_current_output()
            logger.error("{} execution failed: {}", self._public_name, result["message"])
            yield self.format_event(result["message"], {"current_output": current_output})
            return

        downloaded_files = result.get("downloaded_files", [])
        output_files = []
        structured_results = []
        current_turn = int(ctx.session.state.get("turn_index", 0) or 0)
        current_step = int(ctx.session.state.get("step", 0) or 0)
        current_expert_step = int(ctx.session.state.get("expert_step", 0) or 0)
        prompt_description = prompt or "[image-only generation]"
        provider_name = str(result.get("provider", provider) or provider)

        for index, file_info in enumerate(downloaded_files, start=1):
            output_path = Path(file_info["path"])
            artifact_name = output_path.name
            description = (
                f"The {index}th 3D file generated by {provider_name} in turn {current_turn}, step {current_step}. "
                f"generate_type={result.get('generate_type', generate_type)}, prompt={prompt_description}"
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
            structured_results.append(
                ThreeDGenerationResultItem(
                    path=output_files[-1]["path"],
                    name=artifact_name,
                    type=file_info.get("type", ""),
                    preview_image_url=file_info.get("preview_image_url", ""),
                    url=file_info.get("url", ""),
                ).to_result()
            )

        file_names = ", ".join(file_info["name"] for file_info in output_files)
        message = (
            f"{self._public_name} completed {provider_name} job {result.get('job_id', '')} with "
            f"{len(output_files)} file(s): {file_names}"
        )
        prompt_optimization_payload = None
        optimized_prompt = None
        if prompt_optimization_result is not None:
            prompt_optimization_payload = {
                "used_llm": prompt_optimization_result.used_llm,
                "model_name": prompt_optimization_result.model_name,
                "message": prompt_optimization_result.message,
            }
            optimized_prompt = prompt_optimization_result.prompt
        current_output = ThreeDGenerationOutput(
            status="success",
            message=message,
            output_files=output_files,
            provider=provider_name,
            model_name=result.get("model_name", ""),
            job_id=result.get("job_id", ""),
            generate_type=result.get("generate_type", generate_type),
            file_format=result.get("file_format", ""),
            subdivision_level=result.get("subdivision_level", ""),
            resolution=result.get("resolution", ""),
            result_files=structured_results,
            prompt_optimization=prompt_optimization_payload,
            optimized_prompt=optimized_prompt,
        ).to_current_output()
        logger.info(message)
        yield self.format_event(
            message,
            {
                "current_output": current_output,
                "three_d_generation_results": structured_results,
            },
        )


__all__ = [
    "ThreeDGenerationAgent",
    "ThreeDGenerationOutput",
    "ThreeDGenerationParameters",
    "ThreeDGenerationResultItem",
]
