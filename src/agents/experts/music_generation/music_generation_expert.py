"""Music generation expert for Creative Claw."""

from __future__ import annotations

from typing import Any, AsyncGenerator
from typing_extensions import override

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from pydantic import BaseModel, Field, field_validator, model_validator

from src.agents.experts.base import CreativeExpert
from src.agents.experts.music_generation.tool import (
    _parse_bool,
    music_generation_tool,
    normalize_music_audio_format,
)
from src.agents.experts.schema_utils import clean_string, current_output_dict
from src.runtime.workspace import build_workspace_file_record, save_binary_output


class MusicGenerationParameters(BaseModel):
    """Structured request contract read from ``current_parameters``."""

    prompt: str = ""
    lyrics: str = ""
    instrumental: bool = True
    audio_format: str = "mp3"
    sample_rate: Any = 44100
    bitrate: Any = 256000
    model: str = "music-2.5"

    @model_validator(mode="before")
    @classmethod
    def _coerce_mapping(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        lyrics = clean_string(value.get("lyrics", ""))
        return {
            "prompt": value.get("prompt", ""),
            "lyrics": lyrics,
            "instrumental": _parse_bool(value.get("instrumental", not bool(lyrics))),
            "audio_format": normalize_music_audio_format(value.get("audio_format", "mp3")),
            "sample_rate": value.get("sample_rate", 44100),
            "bitrate": value.get("bitrate", 256000),
            "model": value.get("model", "music-2.5"),
        }

    @field_validator("prompt", "lyrics", "model", mode="before")
    @classmethod
    def _strip_string(cls, value: Any) -> str:
        return clean_string(value)

    @property
    def sample_rate_value(self) -> int:
        """Return the effective sample rate using the existing coercion behavior."""
        return int(self.sample_rate or 44100)

    @property
    def bitrate_value(self) -> int:
        """Return the effective bitrate using the existing coercion behavior."""
        return int(self.bitrate or 256000)

    @property
    def model_name(self) -> str:
        """Return the effective music generation model name."""
        return self.model or "music-2.5"


class MusicGenerationResultItem(BaseModel):
    """One music-generation result item stored in ``music_generation_results``."""

    output_path: str
    audio_format: str
    instrumental: bool
    lyrics_used: str = ""
    provider: str = ""
    model_name: str = ""

    @field_validator("output_path", "audio_format", "lyrics_used", "provider", "model_name", mode="before")
    @classmethod
    def _strip_string(cls, value: Any) -> str:
        return clean_string(value)

    def to_result(self) -> dict[str, Any]:
        """Return the stable dictionary item stored in ADK session state."""
        return self.model_dump(mode="python")


class MusicGenerationOutput(BaseModel):
    """Structured ``current_output`` contract emitted by ``MusicGenerationExpert``."""

    status: str
    message: str
    message_for_user: str | None = None
    output_text: str | None = None
    output_files: list[dict[str, Any]] | None = Field(default=None)
    results: list[dict[str, Any]] | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value: Any) -> str:
        return clean_string(value).lower() or "error"

    @field_validator("message", "message_for_user", "output_text", mode="before")
    @classmethod
    def _strip_optional_string(cls, value: Any) -> str:
        return clean_string(value)

    def to_current_output(self) -> dict[str, Any]:
        """Return the stable dictionary payload stored in ADK session state."""
        return current_output_dict(self)


class MusicGenerationExpert(CreativeExpert):
    """Generate one music or BGM clip from text instructions."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the music generation expert."""
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one music generation request."""
        current_parameters = MusicGenerationParameters.model_validate(
            ctx.session.state.get("current_parameters", {})
        )

        if not current_parameters.prompt:
            error_text = f"Missing parameters provided to {self.name}, must include: prompt"
            current_output = MusicGenerationOutput(status="error", message=error_text).to_current_output()
            yield self.format_event(error_text, {"current_output": current_output})
            return

        result = await music_generation_tool(
            prompt=current_parameters.prompt,
            lyrics=current_parameters.lyrics,
            instrumental=current_parameters.instrumental,
            audio_format=current_parameters.audio_format,
            sample_rate=current_parameters.sample_rate_value,
            bitrate=current_parameters.bitrate_value,
            model=current_parameters.model_name,
        )
        if result["status"] != "success":
            current_output = MusicGenerationOutput(status="error", message=result["message"]).to_current_output()
            yield self.format_event(result["message"], {"current_output": current_output})
            return

        current_turn = int(ctx.session.state.get("turn_index", 0) or 0)
        current_step = int(ctx.session.state.get("step", 0) or 0)
        current_expert_step = int(ctx.session.state.get("expert_step", 0) or 0)
        output_path = save_binary_output(
            result["message"],
            session_id=ctx.session.id,
            turn_index=current_turn,
            step=current_step,
            output_type="music_generation",
            index=0,
            extension=f".{current_parameters.audio_format}",
        )
        output_record = build_workspace_file_record(
            output_path,
            description=(
                f"Music generated by MiniMax with model={result.get('model_name', '')}, "
                f"format={current_parameters.audio_format}, instrumental={result.get('instrumental', False)}."
            ),
            source="expert",
            name=output_path.name,
            turn=current_turn,
            step=current_step,
            expert_step=current_expert_step,
        )
        generation_result = {
            "output_path": output_record["path"],
            "audio_format": current_parameters.audio_format,
            "instrumental": bool(result.get("instrumental", False)),
            "lyrics_used": str(result.get("lyrics_used", "")).strip(),
            "provider": str(result.get("provider", "")).strip(),
            "model_name": str(result.get("model_name", "")).strip(),
        }
        generation_result = MusicGenerationResultItem.model_validate(generation_result).to_result()
        message = f"{self.name} generated 1 music file: {output_path.name}"
        current_output = MusicGenerationOutput(
            status="success",
            message=message,
            message_for_user=message,
            output_text=message,
            output_files=[output_record],
            results=[generation_result],
        ).to_current_output()
        yield self.format_event(
            message,
            {
                "current_output": current_output,
                "music_generation_results": [generation_result],
            },
        )


__all__ = [
    "MusicGenerationExpert",
    "MusicGenerationOutput",
    "MusicGenerationParameters",
    "MusicGenerationResultItem",
]
