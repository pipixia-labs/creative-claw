"""Speech recognition expert for Creative Claw."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator
from typing_extensions import override

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.agents.experts.base import CreativeExpert
from src.agents.experts.speech_recognition.tool import (
    infer_subtitle_output_path,
    normalize_subtitle_format,
    parse_bool,
    resolve_speech_task,
    resolve_subtitle_format,
    speech_recognition_tool,
    speech_subtitle_tool,
)
from src.agents.experts.schema_utils import clean_string, current_output_dict
from src.runtime.workspace import (
    build_generated_output_path,
    build_workspace_file_record,
    resolve_workspace_path,
)


def _as_input_paths(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_paths = [value]
    else:
        raw_paths = list(value or [])
    return [str(path).strip() for path in raw_paths if str(path).strip()]


class SpeechRecognitionParameters(BaseModel):
    """Structured request contract read from ``current_parameters``."""

    model_config = ConfigDict(extra="allow")

    raw_parameters: dict[str, Any] = Field(default_factory=dict, exclude=True)
    input_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_mapping(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {"raw_parameters": {}, "input_paths": []}
        raw_parameters = dict(value)
        input_paths = raw_parameters.get("input_paths", raw_parameters.get("input_path"))
        return {**raw_parameters, "raw_parameters": raw_parameters, "input_paths": _as_input_paths(input_paths)}

    @property
    def language(self) -> str:
        """Return the requested recognition language."""
        return clean_string(self.raw_parameters.get("language", ""))

    @property
    def requested_timestamps(self) -> bool:
        """Return whether timestamps were explicitly requested."""
        return parse_bool(self.raw_parameters.get("timestamps"))

    @property
    def effective_task(self) -> str:
        """Return the task selected by the existing speech-task resolver."""
        return resolve_speech_task(self.raw_parameters)

    @property
    def effective_timestamps(self) -> bool:
        """Return whether timestamps should be requested from the provider."""
        return self.requested_timestamps or self.effective_task == "subtitle"

    @property
    def subtitle_format(self) -> str:
        """Return the selected subtitle file format."""
        return resolve_subtitle_format(self.raw_parameters)

    @property
    def output_path(self) -> str:
        """Return an optional explicit subtitle output path."""
        return clean_string(self.raw_parameters.get("output_path", ""))

    @property
    def subtitle_text(self) -> str:
        """Return optional text used for subtitle alignment."""
        return clean_string(self.raw_parameters.get("subtitle_text", self.raw_parameters.get("audio_text", "")))

    @property
    def caption_type(self) -> str:
        """Return the requested subtitle caption type."""
        return clean_string(self.raw_parameters.get("caption_type", self.raw_parameters.get("audio_type", "auto")))

    @property
    def sta_punc_mode(self) -> str:
        """Return optional subtitle alignment punctuation mode."""
        return clean_string(self.raw_parameters.get("sta_punc_mode", ""))

    @property
    def words_per_line(self) -> int | None:
        """Return optional subtitle layout line width."""
        return _coerce_optional_int(self.raw_parameters.get("words_per_line"))

    @property
    def max_lines(self) -> int | None:
        """Return optional subtitle layout line count."""
        return _coerce_optional_int(self.raw_parameters.get("max_lines"))

    @property
    def use_itn(self) -> bool:
        """Return whether inverse text normalization is enabled."""
        return parse_bool(self.raw_parameters.get("use_itn", self.raw_parameters.get("enable_itn", True)))

    @property
    def use_punc(self) -> bool:
        """Return whether punctuation is enabled."""
        return parse_bool(self.raw_parameters.get("use_punc", self.raw_parameters.get("enable_punc", True)))

    @property
    def use_ddc(self) -> bool:
        """Return whether DDC is enabled."""
        return parse_bool(self.raw_parameters.get("use_ddc", self.raw_parameters.get("enable_ddc", False)))

    @property
    def use_speaker_info(self) -> bool:
        """Return whether speaker metadata should be requested."""
        return parse_bool(
            self.raw_parameters.get(
                "with_speaker_info",
                self.raw_parameters.get("enable_speaker_info", False),
            )
        )

    @property
    def use_capitalize(self) -> bool:
        """Return whether subtitle capitalization is enabled."""
        return parse_bool(self.raw_parameters.get("use_capitalize", True))


class SpeechRecognitionResultItem(BaseModel):
    """One speech-recognition result item stored in ``speech_recognition_results``."""

    model_config = ConfigDict(extra="allow")

    input_path: str
    status: str
    message: str
    task: str
    timestamps: bool
    transcription_text: str = ""
    basic_info: str = ""
    provider: str = ""
    model_name: str = ""
    utterances: list[Any] = Field(default_factory=list)
    audio_duration_ms: Any = None
    request_id: str = ""
    log_id: str = ""
    job_id: str = ""
    subtitle_backend: str = ""
    caption_type: str = ""
    subtitle_path: str = ""
    subtitle_format: str = ""

    @field_validator(
        "input_path",
        "status",
        "message",
        "task",
        "transcription_text",
        "basic_info",
        "provider",
        "model_name",
        "request_id",
        "log_id",
        "job_id",
        "subtitle_backend",
        "caption_type",
        "subtitle_path",
        "subtitle_format",
        mode="before",
    )
    @classmethod
    def _strip_string(cls, value: Any) -> str:
        return clean_string(value)

    @field_validator("status", mode="after")
    @classmethod
    def _lower_status(cls, value: str) -> str:
        return value.lower() or "error"

    @field_validator("utterances", mode="before")
    @classmethod
    def _coerce_utterances(cls, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def to_result(self) -> dict[str, Any]:
        """Return the stable dictionary item stored in ADK session state."""
        return self.model_dump(mode="python")


class SpeechRecognitionOutput(BaseModel):
    """Structured ``current_output`` contract emitted by ``SpeechRecognitionExpert``."""

    status: str
    message: str
    message_for_user: str | None = None
    output_text: str | None = None
    results: list[dict[str, Any]] | None = None
    output_files: list[dict[str, Any]] | None = Field(default=None)

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


class SpeechRecognitionExpert(CreativeExpert):
    """Recognize speech from media files or generate subtitle files."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the speech recognition expert."""
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one normalized speech recognition request."""
        current_parameters = SpeechRecognitionParameters.model_validate(
            ctx.session.state.get("current_parameters", {})
        )
        input_paths = current_parameters.input_paths
        effective_task = current_parameters.effective_task
        effective_timestamps = current_parameters.effective_timestamps
        subtitle_format = current_parameters.subtitle_format
        output_path = current_parameters.output_path

        if not input_paths:
            error_text = f"Missing parameters provided to {self.name}, must include: input_path or input_paths"
            current_output = SpeechRecognitionOutput(status="error", message=error_text).to_current_output()
            yield self.format_event(error_text, {"current_output": current_output})
            return

        if effective_task == "subtitle" and output_path and len(input_paths) != 1:
            error_text = "Subtitle generation only supports output_path when exactly one input file is provided."
            current_output = SpeechRecognitionOutput(status="error", message=error_text).to_current_output()
            yield self.format_event(error_text, {"current_output": current_output})
            return

        result_list = await asyncio.gather(
            *[
                (
                    speech_subtitle_tool(
                        ctx,
                        path,
                        language=current_parameters.language,
                        subtitle_format=subtitle_format,
                        caption_type=current_parameters.caption_type,
                        subtitle_text=current_parameters.subtitle_text,
                        sta_punc_mode=current_parameters.sta_punc_mode,
                        words_per_line=current_parameters.words_per_line,
                        max_lines=current_parameters.max_lines,
                        use_itn=current_parameters.use_itn,
                        use_punc=current_parameters.use_punc,
                        use_ddc=current_parameters.use_ddc,
                        with_speaker_info=current_parameters.use_speaker_info,
                        use_capitalize=current_parameters.use_capitalize,
                    )
                    if effective_task == "subtitle"
                    else speech_recognition_tool(
                        ctx,
                        path,
                        language=current_parameters.language,
                        timestamps=effective_timestamps,
                        task=effective_task,
                        enable_itn=current_parameters.use_itn,
                        enable_punc=current_parameters.use_punc,
                        enable_ddc=current_parameters.use_ddc,
                        enable_speaker_info=current_parameters.use_speaker_info,
                    )
                )
                for path in input_paths
            ],
            return_exceptions=True,
        )

        current_turn = int(ctx.session.state.get("turn_index", 0) or 0)
        current_step = int(ctx.session.state.get("step", 0) or 0)
        current_expert_step = int(ctx.session.state.get("expert_step", 0) or 0)
        structured_results: list[dict[str, Any]] = []
        output_files: list[dict[str, str]] = []
        success_messages: list[str] = []
        error_messages: list[str] = []

        for index, (path, result) in enumerate(zip(input_paths, result_list)):
            if isinstance(result, Exception):
                structured_results.append(
                    SpeechRecognitionResultItem.model_validate(
                        {
                            "input_path": path,
                            "status": "error",
                            "message": f"{type(result).__name__}: {result}",
                            "task": effective_task,
                            "timestamps": effective_timestamps,
                        }
                    ).to_result()
                )
                error_messages.append(f"media {path} speech recognition failed, reason: {type(result).__name__}: {result}\n")
                continue

            status = str(result.get("status", "")).strip().lower() or "error"
            message = str(result.get("message", "")).strip()
            current_result = {
                "input_path": str(result.get("input_path", path)).strip() or path,
                "status": status,
                "message": message,
                "task": str(result.get("task", effective_task)).strip() or effective_task,
                "transcription_text": str(result.get("transcription_text", "")).strip(),
                "basic_info": str(result.get("basic_info", "")).strip(),
                "provider": str(result.get("provider", "")).strip(),
                "model_name": str(result.get("model_name", "")).strip(),
                "timestamps": bool(result.get("timestamps", effective_timestamps)),
                "utterances": result.get("utterances", []) if isinstance(result.get("utterances"), list) else [],
                "audio_duration_ms": result.get("audio_duration_ms"),
                "request_id": str(result.get("request_id", "")).strip(),
                "log_id": str(result.get("log_id", "")).strip(),
                "job_id": str(result.get("job_id", "")).strip(),
                "subtitle_backend": str(result.get("subtitle_backend", "")).strip(),
                "caption_type": str(result.get("caption_type", "")).strip(),
                "subtitle_path": "",
                "subtitle_format": "",
            }

            if status == "success" and effective_task == "subtitle":
                try:
                    subtitle_path = self._write_subtitle_output(
                        ctx,
                        input_path=current_result["input_path"],
                        subtitle_text=str(result.get("subtitle_content", "")).strip(),
                        subtitle_format=subtitle_format,
                        index=index,
                        explicit_output_path=output_path,
                    )
                    output_record = build_workspace_file_record(
                        subtitle_path,
                        description=(
                            f"Subtitle generated by {self.name} using format={normalize_subtitle_format(subtitle_format)} "
                            f"from media={current_result['input_path']}."
                        ),
                        source="expert",
                        name=Path(subtitle_path).name,
                        turn=current_turn,
                        step=current_step,
                        expert_step=current_expert_step,
                    )
                    current_result["subtitle_path"] = output_record["path"]
                    current_result["subtitle_format"] = normalize_subtitle_format(subtitle_format)
                    output_files.append(output_record)
                    success_messages.append(
                        f"media {path}: subtitle generated -> {current_result['subtitle_path']}\n"
                    )
                except Exception as exc:
                    current_result["status"] = "error"
                    current_result["message"] = f"Subtitle generation failed: {type(exc).__name__}: {exc}"
                    error_messages.append(
                        f"media {path} subtitle generation failed, reason: {type(exc).__name__}: {exc}\n"
                    )
            elif status == "success":
                success_messages.append(f"media {path}: speech recognition completed\n")
            else:
                error_messages.append(f"media {path} speech recognition failed, reason: {message}\n")

            structured_results.append(SpeechRecognitionResultItem.model_validate(current_result).to_result())

        success_count = sum(1 for item in structured_results if item["status"] == "success")
        if success_count == 0:
            error_text = f"All {len(input_paths)} speech recognition tasks failed:\n\n" + "\n".join(error_messages)
            current_output = SpeechRecognitionOutput(
                status="error",
                message=error_text,
                message_for_user=error_text,
                results=structured_results,
                output_files=output_files,
            ).to_current_output()
            yield self.format_event(
                error_text,
                {
                    "current_output": current_output,
                    "speech_recognition_results": structured_results,
                },
            )
            return

        message = (
            f"Finished {effective_task} processing for {len(input_paths)} media files "
            f"with {success_count} successful results."
        )
        output_text = message + "\n\n" + "\n".join(success_messages + error_messages)
        current_output = SpeechRecognitionOutput(
            status="success",
            message=message,
            message_for_user=message,
            output_text=output_text,
            output_files=output_files,
            results=structured_results,
        ).to_current_output()
        yield self.format_event(
            output_text,
            {
                "current_output": current_output,
                "speech_recognition_results": structured_results,
            },
        )

    def _write_subtitle_output(
        self,
        ctx: InvocationContext,
        *,
        input_path: str,
        subtitle_text: str,
        subtitle_format: str,
        index: int,
        explicit_output_path: str = "",
    ) -> Path:
        """Persist one generated subtitle document inside the workspace."""
        if explicit_output_path:
            destination = resolve_workspace_path(explicit_output_path)
        else:
            inferred_name = infer_subtitle_output_path(input_path, subtitle_format)
            destination = build_generated_output_path(
                session_id=ctx.session.id,
                turn_index=int(ctx.session.state.get("turn_index", 0) or 0),
                step=int(ctx.session.state.get("step", 0) or 0),
                output_type=f"speech_recognition_{Path(inferred_name).stem}",
                index=index,
                extension=f".{normalize_subtitle_format(subtitle_format)}",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(subtitle_text, encoding="utf-8")
        return destination.resolve()


def _coerce_optional_int(value: Any) -> int | None:
    """Normalize one optional integer-like input."""
    if value in ("", None):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "SpeechRecognitionExpert",
    "SpeechRecognitionOutput",
    "SpeechRecognitionParameters",
    "SpeechRecognitionResultItem",
]
