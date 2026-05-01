"""Tool helpers for reusable code generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models import LlmRequest
from google.genai.types import Content, Part

from conf.llm import build_llm, resolve_llm_model_name
from conf.path import PROJECT_PATH
from src.logger import logger
from src.runtime.workspace import (
    build_generated_output_path,
    resolve_workspace_path,
    workspace_relative_path,
    workspace_root,
)

_CONTEXT_FILE_MAX_CHARS = 20000

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


def _extension_for_language(language: str) -> str:
    """Return the output extension for one normalized language."""
    return _LANGUAGE_EXTENSIONS.get(normalize_code_language(language), ".txt")


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
    ctx: InvocationContext,
) -> Path:
    """Resolve the output path inside the runtime workspace."""
    if output_path.strip():
        resolved = resolve_workspace_path(output_path)
        if not resolved.suffix:
            resolved = resolved.with_suffix(_extension_for_language(language))
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    state = ctx.session.state
    destination = build_generated_output_path(
        session_id=str(getattr(ctx.session, "id", "") or state.get("sid", "") or "default"),
        turn_index=int(state.get("turn_index", 0) or 0),
        step=int(state.get("step", 0) or 0),
        output_type="code_generation",
        index=0,
        extension=_extension_for_language(language),
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
        "- Return only the complete file contents.",
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


async def code_generation_tool(
    ctx: InvocationContext,
    *,
    prompt: str,
    language: str = "html",
    output_path: str = "",
    context_files: list[str] | None = None,
    constraints: list[str] | None = None,
) -> dict[str, Any]:
    """Generate one code file with the configured LLM and write it to workspace."""
    normalized_language = normalize_code_language(language)
    output_file = _resolve_output_path(output_path, language=normalized_language, ctx=ctx)
    relative_output_path = workspace_relative_path(output_file)
    context_text, warnings = _read_context_files(context_files or [])
    request_text = _build_generation_request(
        prompt=prompt,
        language=normalized_language,
        output_path=relative_output_path,
        context_text=context_text,
        constraints=constraints or [],
    )

    def before_model_callback(
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> None:
        """Inject the complete code-generation request into the LLM call."""
        llm_request.contents.append(Content(role="user", parts=[Part(text=request_text)]))

    llm = LlmAgent(
        name="CodeGenerationToolAgent",
        model=build_llm(),
        instruction=(
            "You are an expert software engineer. Generate exactly one requested file. "
            "Return only the file contents."
        ),
        include_contents="none",
        before_model_callback=before_model_callback,
    )

    try:
        generated_text = ""
        async for event in llm.run_async(ctx):
            if event.is_final_response() and event.content and event.content.parts:
                text = next((part.text for part in event.content.parts if part.text), None)
                if text:
                    generated_text = strip_code_fence(text)

        if not generated_text:
            return {
                "status": "error",
                "message": "Code generation returned empty code.",
                "provider": "google_adk",
                "model_name": resolve_llm_model_name(),
                "warnings": warnings,
            }

        output_file.write_text(generated_text.rstrip() + "\n", encoding="utf-8")
        return {
            "status": "success",
            "message": f"Generated {normalized_language} code at {relative_output_path}.",
            "output_path": relative_output_path,
            "output_files": [
                {
                    "path": relative_output_path,
                    "description": f"Code generated by CodeGenerationExpert ({normalized_language}).",
                    "source": "expert",
                }
            ],
            "language": normalized_language,
            "provider": "google_adk",
            "model_name": resolve_llm_model_name(),
            "warnings": warnings,
        }
    except Exception as exc:
        logger.opt(exception=exc).error(
            "code generation failed: output_path={} language={} error_type={} error={!r}",
            relative_output_path,
            normalized_language,
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": f"Code generation failed: {type(exc).__name__}: {exc}",
            "output_path": relative_output_path,
            "language": normalized_language,
            "provider": "google_adk",
            "model_name": resolve_llm_model_name(),
            "warnings": warnings,
        }
