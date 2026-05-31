"""Speech synthesis expert for Creative Claw."""

from __future__ import annotations

from typing import Any, AsyncGenerator
from typing_extensions import override

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from pydantic import BaseModel, Field, field_validator, model_validator

from src.agents.experts.base import CreativeExpert
from src.agents.experts.speech_synthesis.tool import (
    _parse_bool,
    normalize_speech_audio_format,
    speech_synthesis_tool,
)
from src.agents.experts.schema_utils import clean_string, current_output_dict
from src.runtime.workspace import build_workspace_file_record, save_binary_output


class SpeechSynthesisParameters(BaseModel):
    """Structured request contract read from ``current_parameters``."""

    text: str = ""
    ssml: str = ""
    speaker: str = ""
    voice_type: str = ""
    voice_name: str = ""
    resource_id: str = ""
    audio_format: str = "mp3"
    sample_rate: Any = 24000
    explicit_language: str = ""
    enable_timestamp: bool = False
    latex_parser: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_mapping(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {
            "text": value.get("text", ""),
            "ssml": value.get("ssml", ""),
            "speaker": value.get("speaker", ""),
            "voice_type": value.get("voice_type", ""),
            "voice_name": value.get("voice_name", ""),
            "resource_id": value.get("resource_id", ""),
            "audio_format": normalize_speech_audio_format(value.get("audio_format", "mp3")),
            "sample_rate": value.get("sample_rate", 24000),
            "explicit_language": value.get("language", value.get("explicit_language", "")),
            "enable_timestamp": _parse_bool(value.get("enable_timestamp", False)),
            "latex_parser": value.get("latex_parser", ""),
        }

    @field_validator(
        "text",
        "ssml",
        "speaker",
        "voice_type",
        "voice_name",
        "resource_id",
        "explicit_language",
        "latex_parser",
        mode="before",
    )
    @classmethod
    def _strip_string(cls, value: Any) -> str:
        return clean_string(value)

    @property
    def sample_rate_value(self) -> int:
        """Return the effective sample rate using the existing coercion behavior."""
        return int(self.sample_rate or 24000)

    @property
    def missing_required_text(self) -> bool:
        """Whether the request lacks both supported text inputs."""
        return not self.text and not self.ssml


class SpeechSynthesisResultItem(BaseModel):
    """One speech-synthesis result item stored in ``speech_synthesis_results``."""

    output_path: str
    speaker: str = ""
    voice_name: str = ""
    resource_id: str = ""
    audio_format: str
    usage: dict[str, Any] = Field(default_factory=dict)
    log_id: str = ""
    sentence_count: int = 0
    provider: str = ""

    @field_validator("output_path", "speaker", "voice_name", "resource_id", "audio_format", "log_id", "provider", mode="before")
    @classmethod
    def _strip_string(cls, value: Any) -> str:
        return clean_string(value)

    def to_result(self) -> dict[str, Any]:
        """Return the stable dictionary item stored in ADK session state."""
        return self.model_dump(mode="python")


class SpeechSynthesisOutput(BaseModel):
    """Structured ``current_output`` contract emitted by ``SpeechSynthesisExpert``."""

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


class SpeechSynthesisExpert(CreativeExpert):
    """Generate one speech audio file from text or SSML."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the speech synthesis expert."""
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one speech synthesis request."""
        current_parameters = SpeechSynthesisParameters.model_validate(
            ctx.session.state.get("current_parameters", {})
        )

        if current_parameters.missing_required_text:
            error_text = f"Missing parameters provided to {self.name}, must include: text or ssml"
            current_output = SpeechSynthesisOutput(status="error", message=error_text).to_current_output()
            yield self.format_event(error_text, {"current_output": current_output})
            return

        result = await speech_synthesis_tool(
            user_id=str(ctx.session.user_id),
            text=current_parameters.text,
            ssml=current_parameters.ssml,
            speaker=current_parameters.speaker,
            voice_type=current_parameters.voice_type,
            voice_name=current_parameters.voice_name,
            resource_id=current_parameters.resource_id,
            audio_format=current_parameters.audio_format,
            sample_rate=current_parameters.sample_rate_value,
            explicit_language=current_parameters.explicit_language,
            enable_timestamp=current_parameters.enable_timestamp,
            latex_parser=current_parameters.latex_parser,
        )
        if result["status"] != "success":
            current_output = SpeechSynthesisOutput(status="error", message=result["message"]).to_current_output()
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
            output_type="speech_synthesis",
            index=0,
            extension=f".{current_parameters.audio_format}",
        )
        output_record = build_workspace_file_record(
            output_path,
            description=(
                f"Speech synthesized by ByteDance TTS with speaker={result.get('speaker', '')}, "
                f"resource_id={result.get('model_name', '')}, format={current_parameters.audio_format}."
            ),
            source="expert",
            name=output_path.name,
            turn=current_turn,
            step=current_step,
            expert_step=current_expert_step,
        )
        synthesis_result = {
            "output_path": output_record["path"],
            "speaker": str(result.get("speaker", "")).strip(),
            "voice_name": str(result.get("voice_name", "")).strip(),
            "resource_id": str(result.get("model_name", "")).strip(),
            "audio_format": current_parameters.audio_format,
            "usage": result.get("usage", {}),
            "log_id": str(result.get("log_id", "")).strip(),
            "sentence_count": len(result.get("sentences", []) or []),
            "provider": str(result.get("provider", "")).strip(),
        }
        synthesis_result = SpeechSynthesisResultItem.model_validate(synthesis_result).to_result()
        message = f"{self.name} generated 1 speech file: {output_path.name}"
        current_output = SpeechSynthesisOutput(
            status="success",
            message=message,
            message_for_user=message,
            output_text=message,
            output_files=[output_record],
            results=[synthesis_result],
        ).to_current_output()
        yield self.format_event(
            message,
            {
                "current_output": current_output,
                "speech_synthesis_results": [synthesis_result],
            },
        )


__all__ = [
    "SpeechSynthesisExpert",
    "SpeechSynthesisOutput",
    "SpeechSynthesisParameters",
    "SpeechSynthesisResultItem",
]
