"""Runtime helpers for generating code-backed workspace artifacts."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest
from google.adk.tools.tool_context import ToolContext
from google.genai.types import Content, Part

from conf.llm import build_llm, resolve_llm_model_name
from conf.path import PROJECT_PATH
from src.logger import logger
from src.runtime.adk_compat import get_invocation_context
from src.runtime.workspace import (
    build_generated_output_path,
    resolve_workspace_path,
    workspace_relative_path,
    workspace_root,
)

_CONTEXT_FILE_MAX_CHARS = 20000
_CODE_ARTIFACT_TIMEOUT_SECONDS = 420.0
_CODE_ARTIFACT_TOOL_RESULT_STATE_KEY = "_code_artifact_generation_result"
_HTML_START_RE = re.compile(r"<!doctype\s+html\b|<html\b", re.IGNORECASE)
_HTML_END_RE = re.compile(r"</html\s*>", re.IGNORECASE)

_LANGUAGE_EXTENSIONS = {
    "html": ".html",
    "css": ".css",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts",
    "ts": ".ts",
    "jsx": ".jsx",
    "tsx": ".tsx",
    "python": ".py",
    "py": ".py",
    "markdown": ".md",
    "md": ".md",
    "json": ".json",
    "yaml": ".yaml",
    "yml": ".yml",
    "toml": ".toml",
    "text": ".txt",
    "txt": ".txt",
}


def normalize_code_language(raw_value: str) -> str:
    """Return a normalized code language label."""
    value = str(raw_value or "").strip().lower()
    return value if value in _LANGUAGE_EXTENSIONS else "text"


def strip_code_fence(text: str) -> str:
    """Remove one surrounding markdown code fence from generated code."""
    stripped = str(text or "").strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def extension_for_language(language: str) -> str:
    """Return the output extension for one normalized language."""
    return _LANGUAGE_EXTENSIONS.get(normalize_code_language(language), ".txt")


class GeneratedCodeContentError(ValueError):
    """Raised when generated code cannot be safely written as the target artifact."""


def normalize_generated_code_content(text: str, *, language: str) -> tuple[str, list[str]]:
    """Normalize generated code before it is written to a workspace artifact."""
    normalized_language = normalize_code_language(language)
    code = strip_code_fence(text)
    if not code.strip():
        raise GeneratedCodeContentError("Generated code content is empty.")
    if normalized_language != "html":
        return code.strip(), []
    return _extract_html_document(code)


def _extract_html_document(text: str) -> tuple[str, list[str]]:
    """Extract a complete HTML document from model output."""
    raw_text = str(text or "").strip()
    start_match = _HTML_START_RE.search(raw_text)
    if start_match is None:
        raise GeneratedCodeContentError("HTML output is missing `<!DOCTYPE html>` or `<html>`.")

    end_match = None
    for match in _HTML_END_RE.finditer(raw_text):
        if match.end() > start_match.start():
            end_match = match
    if end_match is None:
        raise GeneratedCodeContentError("HTML output is missing closing `</html>`.")

    html_document = raw_text[start_match.start() : end_match.end()].strip()
    lowered = html_document.lower()
    if "<html" not in lowered or "</html" not in lowered:
        raise GeneratedCodeContentError("HTML output does not contain a complete `<html>...</html>` document.")

    warnings: list[str] = []
    if raw_text[: start_match.start()].strip():
        warnings.append("Dropped non-HTML text before the HTML document.")
    if raw_text[end_match.end() :].strip():
        warnings.append("Dropped non-HTML text after the HTML document.")
    return html_document, warnings


def _write_generated_code_content(
    content: str,
    *,
    output_file: Path,
    relative_output_path: str,
    normalized_language: str,
    warnings: list[str],
    output_description: str,
    output_source: str,
) -> dict[str, Any]:
    """Validate and persist one generated code artifact."""
    try:
        normalized_content, content_warnings = normalize_generated_code_content(
            content,
            language=normalized_language,
        )
    except GeneratedCodeContentError as exc:
        return {
            "status": "error",
            "message": f"Generated {normalized_language} content failed validation: {exc}",
            "error_type": "invalid_generated_content",
            "retryable": True,
            "raw_error_summary": f"{type(exc).__name__}: {exc}",
            "output_path": relative_output_path,
            "language": normalized_language,
            "provider": "google_adk",
            "model_name": resolve_llm_model_name(),
            "warnings": warnings,
        }

    output_file.write_text(normalized_content.rstrip() + "\n", encoding="utf-8")
    description = output_description or f"Code artifact generated by runtime ({normalized_language})."
    return {
        "status": "success",
        "message": f"Generated {normalized_language} code at {relative_output_path}.",
        "error_type": "",
        "retryable": False,
        "raw_error_summary": "",
        "output_path": relative_output_path,
        "output_files": [
            {
                "path": relative_output_path,
                "description": description,
                "source": output_source,
            }
        ],
        "language": normalized_language,
        "provider": "google_adk",
        "model_name": resolve_llm_model_name(),
        "warnings": [*warnings, *content_warnings],
    }


async def generate_code_artifact(
    runtime_context: Any,
    *,
    prompt: str,
    language: str = "html",
    output_path: str = "",
    context_files: list[str] | None = None,
    constraints: list[str] | None = None,
    output_type: str = "code_generation",
    output_description: str = "",
    output_source: str = "expert",
) -> dict[str, Any]:
    """Generate one code artifact with the configured LLM and write it to workspace."""
    run_context = get_invocation_context(runtime_context)
    state = _runtime_state(runtime_context, run_context)
    normalized_language = normalize_code_language(language)
    relative_output_path = str(output_path or "").strip()
    warnings: list[str] = []
    try:
        output_file = _resolve_output_path(
            output_path,
            language=normalized_language,
            runtime_context=run_context,
            state=state,
            output_type=output_type,
        )
        relative_output_path = workspace_relative_path(output_file)
        context_text, warnings = _read_context_files(context_files or [])
        request_text = _build_generation_request(
            prompt=prompt,
            language=normalized_language,
            output_path=relative_output_path,
            context_text=context_text,
            constraints=constraints or [],
        )
        tool_result_holder: dict[str, dict[str, Any]] = {}

        async def save_code_artifact(content: str, tool_context: ToolContext) -> dict[str, Any]:
            """Save the complete generated code artifact content."""
            result = _write_generated_code_content(
                content,
                output_file=output_file,
                relative_output_path=relative_output_path,
                normalized_language=normalized_language,
                warnings=warnings,
                output_description=output_description,
                output_source=output_source,
            )
            existing = tool_result_holder.get("result")
            if result.get("status") == "success" or not existing or existing.get("status") != "success":
                tool_result_holder["result"] = result
            tool_context.state[_CODE_ARTIFACT_TOOL_RESULT_STATE_KEY] = result
            if result.get("status") == "success":
                tool_context.actions.skip_summarization = True
            return result

        def before_model_callback(
            callback_context: CallbackContext,
            llm_request: LlmRequest,
        ) -> None:
            """Inject the complete generation request into the LLM call."""
            llm_request.contents.append(Content(role="user", parts=[Part(text=request_text)]))

        llm = LlmAgent(
            name="CodeArtifactGenerationAgent",
            model=build_llm(),
            instruction=(
                "You are an expert software engineer. Generate exactly one requested file. "
                "Submit the complete file content by calling save_code_artifact. "
                "Do not answer with prose, markdown fences, or explanations."
            ),
            tools=[save_code_artifact],
            include_contents="none",
            before_model_callback=before_model_callback,
        )

        logger.info(
            "code artifact generation started: output_path={} language={} timeout_seconds={} request_chars={}",
            relative_output_path,
            normalized_language,
            _CODE_ARTIFACT_TIMEOUT_SECONDS,
            len(request_text),
        )
        tool_result, generated_text = await _collect_generated_code_submission(
            llm,
            run_context,
            timeout_seconds=_CODE_ARTIFACT_TIMEOUT_SECONDS,
            tool_result_holder=tool_result_holder,
        )
        if tool_result and tool_result.get("status") == "success":
            logger.info(
                "code artifact generation wrote file via tool: output_path={} language={} chars={}",
                relative_output_path,
                normalized_language,
                output_file.stat().st_size if output_file.exists() else 0,
            )
            return tool_result

        if not generated_text:
            if tool_result:
                return tool_result
            logger.warning(
                "code artifact generation returned empty code: output_path={} language={}",
                relative_output_path,
                normalized_language,
            )
            return {
                "status": "error",
                "message": "Code artifact generation returned empty code.",
                "error_type": "empty_result",
                "retryable": True,
                "raw_error_summary": "empty model response",
                "output_path": relative_output_path,
                "language": normalized_language,
                "provider": "google_adk",
                "model_name": resolve_llm_model_name(),
                "warnings": warnings,
            }

        fallback_result = _write_generated_code_content(
            generated_text,
            output_file=output_file,
            relative_output_path=relative_output_path,
            normalized_language=normalized_language,
            warnings=warnings,
            output_description=output_description,
            output_source=output_source,
        )
        if fallback_result.get("status") != "success":
            if tool_result:
                return tool_result
            return fallback_result
        logger.info(
            "code artifact generation wrote file: output_path={} language={} chars={}",
            relative_output_path,
            normalized_language,
            output_file.stat().st_size if output_file.exists() else 0,
        )
        return fallback_result
    except Exception as exc:
        error_type, retryable = _classify_generation_exception(exc)
        raw_error_summary = f"{type(exc).__name__}: {exc}"
        logger.opt(exception=exc).error(
            "code artifact generation failed: output_path={} language={} error_type={} error={!r}",
            relative_output_path,
            normalized_language,
            error_type,
            exc,
        )
        return {
            "status": "error",
            "message": f"Code artifact generation failed: {type(exc).__name__}: {exc}",
            "error_type": error_type,
            "retryable": retryable,
            "raw_error_summary": raw_error_summary,
            "output_path": relative_output_path,
            "language": normalized_language,
            "provider": "google_adk",
            "model_name": resolve_llm_model_name(),
            "warnings": warnings,
        }


async def _collect_generated_code_submission(
    llm: LlmAgent,
    run_context: Any,
    *,
    timeout_seconds: float,
    tool_result_holder: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    """Collect a structured code submission or fallback generated text from ADK.

    Final response text is preferred, but ADK can occasionally surface model text
    without marking the event as final. In that case, the latest complete text
    event is still usable for file generation.
    """
    final_text = ""
    latest_complete_text = ""
    partial_chunks: list[str] = []

    try:
        async with asyncio.timeout(timeout_seconds):
            async for event in llm.run_async(run_context):
                tool_result = tool_result_holder.get("result")
                if tool_result and tool_result.get("status") == "success":
                    continue
                event_text = _extract_event_text(event)
                if not event_text:
                    continue

                if bool(getattr(event, "partial", False)):
                    partial_chunks.append(event_text)
                    continue

                cleaned_text = strip_code_fence(event_text)
                latest_complete_text = cleaned_text
                if _is_final_response_event(event):
                    final_text = cleaned_text
    except TimeoutError as exc:
        raise TimeoutError(
            f"code artifact generation timed out after {timeout_seconds:g} seconds"
        ) from exc

    tool_result = tool_result_holder.get("result")
    if final_text:
        return tool_result, final_text
    if latest_complete_text:
        logger.warning("code artifact generation used non-final text fallback")
        return tool_result, latest_complete_text
    if partial_chunks:
        logger.warning("code artifact generation used partial text fallback")
        return tool_result, strip_code_fence("".join(partial_chunks))
    return tool_result, ""


def _extract_event_text(event: Any) -> str:
    """Return concatenated non-thought text parts from one ADK event."""
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) or []
    texts = [
        str(text)
        for part in parts
        if getattr(part, "thought", None) is not True
        if (text := getattr(part, "text", None))
    ]
    return "".join(texts).strip()


def _is_final_response_event(event: Any) -> bool:
    """Return whether an ADK event reports itself as a final response."""
    is_final_response = getattr(event, "is_final_response", None)
    return bool(callable(is_final_response) and is_final_response())


def _runtime_state(runtime_context: Any, run_context: Any) -> dict[str, Any]:
    """Return the mutable session state used for output path defaults."""
    state = getattr(runtime_context, "state", None)
    if isinstance(state, dict):
        return state
    try:
        return run_context.session.state
    except AttributeError:
        return {}


def _resolve_context_file(path: str) -> Path:
    """Resolve a context file from project resources or workspace files."""
    raw_path = Path(path).expanduser()
    project_root = Path(PROJECT_PATH).resolve()
    candidate_roots = (project_root, workspace_root())

    if raw_path.is_absolute():
        resolved = raw_path.resolve()
        for root in candidate_roots:
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            if resolved.is_file():
                return resolved
            raise FileNotFoundError(f"Context file not found: {path}")
        raise ValueError(f"Context file is outside allowed roots: {path}")

    for root in candidate_roots:
        candidate = (root / raw_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Context file not found: {path}")


def _read_context_files(context_files: list[str]) -> tuple[str, list[str]]:
    """Read selected context files and return prompt text plus warnings."""
    sections: list[str] = []
    warnings: list[str] = []
    for context_file in context_files:
        try:
            resolved = _resolve_context_file(context_file)
            content = resolved.read_text(encoding="utf-8")
        except Exception as exc:
            warnings.append(f"{context_file}: {type(exc).__name__}: {exc}")
            continue

        truncated = content[:_CONTEXT_FILE_MAX_CHARS]
        if len(content) > _CONTEXT_FILE_MAX_CHARS:
            truncated += f"\n\n<!-- truncated to {_CONTEXT_FILE_MAX_CHARS} chars -->"
        sections.append(
            "\n".join(
                [
                    f"## Context file: {context_file}",
                    "```",
                    truncated,
                    "```",
                ]
            )
        )
    return "\n\n".join(sections), warnings


def _resolve_output_path(
    output_path: str,
    *,
    language: str,
    runtime_context: Any,
    state: dict[str, Any],
    output_type: str,
) -> Path:
    """Resolve the output path inside the runtime workspace."""
    if output_path.strip():
        resolved = resolve_workspace_path(output_path)
        if not resolved.suffix:
            resolved = resolved.with_suffix(extension_for_language(language))
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    session = getattr(runtime_context, "session", None)
    destination = build_generated_output_path(
        session_id=str(getattr(session, "id", "") or state.get("sid", "") or "default"),
        turn_index=int(state.get("turn_index", 0) or 0),
        step=int(state.get("step", 0) or 0),
        output_type=output_type,
        index=0,
        extension=extension_for_language(language),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def _build_generation_request(
    *,
    prompt: str,
    language: str,
    output_path: str,
    context_text: str,
    constraints: list[str],
) -> str:
    """Build the code-generation request sent to the LLM."""
    lines = [
        "Generate production-quality code for the requested deliverable.",
        f"Language or file type: {language}",
        f"Intended output path: {output_path}",
        "",
        "Rules:",
        "- Call save_code_artifact with the complete file contents.",
        "- Do not return the file contents as assistant prose unless tool calling is unavailable.",
        "- Do not wrap the answer in markdown fences.",
        "- Do not explain the code after the file contents.",
        "- Use clear, maintainable code and preserve all requested constraints.",
    ]
    if constraints:
        lines.append("- Additional constraints:")
        lines.extend(f"  - {constraint}" for constraint in constraints)
    if context_text.strip():
        lines.extend(["", "# Selected context", context_text])
    lines.extend(["", "# Generation brief", prompt.strip()])
    return "\n".join(lines)


def _classify_generation_exception(exc: Exception) -> tuple[str, bool]:
    """Classify a code-generation exception into a stable caller-facing error type."""
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(marker in text for marker in ("rate", "quota", "429", "overload", "unavailable", "503")):
        return "api_overloaded", True
    if any(marker in text for marker in ("timeout", "ssl", "connection", "network", "dns")):
        return "network_error", True
    if any(marker in text for marker in ("auth", "permission", "401", "403", "api key")):
        return "auth_error", False
    return "generation_error", True
