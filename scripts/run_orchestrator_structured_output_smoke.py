"""Run live provider smoke checks for ADK structured output with tools."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterator
import warnings

from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.apps import App
from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from conf.llm import build_llm, get_provider_spec, resolve_llm_model_name, resolve_provider_and_model
from src.agents.orchestrator.final_response import (
    ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY,
    OrchestratorFinalResponse,
)

APP_NAME = "creative_claw_structured_output_smoke"
DEFAULT_PROMPT = "Run the Creative Claw ADK structured output smoke check."
EXPECTED_REPLY = "structured output smoke passed: tool-ok"


def get_structured_output_smoke_status() -> dict[str, str]:
    """Return a fixed marker so the smoke check proves tool use happened."""
    return {"status": "tool-ok"}


def parse_args() -> argparse.Namespace:
    """Parse live provider smoke arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        help=(
            "Provider/model reference such as openai/gpt-5.4 or "
            "gemini/gemini-2.5-flash. Repeat to test multiple providers. "
            "Defaults to the configured model."
        ),
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="User prompt for each smoke run.",
    )
    parser.add_argument(
        "--max-llm-calls",
        type=int,
        default=4,
        help="Maximum ADK LLM calls per case.",
    )
    parser.add_argument(
        "--result-json",
        type=Path,
        help="Optional path to save the compact result JSON.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show provider and ADK warning/traceback output instead of compact JSON only.",
    )
    parser.add_argument(
        "--force-native",
        action="store_true",
        help=(
            "Probe providers that are unsupported in auto mode. Use this only "
            "when you intentionally want to reproduce provider native-schema failures."
        ),
    )
    return parser.parse_args()


async def run_smoke_case(
    *,
    model_reference: str,
    prompt: str,
    max_llm_calls: int,
) -> dict[str, Any]:
    """Run one live provider through ADK output_schema plus tool calling."""
    model_label = resolve_llm_model_name(model_reference or None)
    session_service = InMemorySessionService()
    artifact_service = InMemoryArtifactService()
    agent = LlmAgent(
        name="StructuredOutputSmokeAgent",
        model=build_llm(model_reference or None),
        instruction=(
            "You are a smoke-test agent for Creative Claw.\n"
            "Call get_structured_output_smoke_status exactly once.\n"
            f"Then return a structured final response with reply_text exactly {EXPECTED_REPLY!r} "
            "and final_file_paths as an empty list."
        ),
        tools=[get_structured_output_smoke_status],
        output_schema=OrchestratorFinalResponse,
        output_key=ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY,
    )
    runner = Runner(
        app=App(name=APP_NAME, root_agent=agent),
        app_name=APP_NAME,
        session_service=session_service,
        artifact_service=artifact_service,
    )
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id="structured-smoke-user",
        state={},
    )

    function_calls: list[str] = []
    final_texts: list[str] = []
    async for event in runner.run_async(
        user_id="structured-smoke-user",
        session_id=session.id,
        new_message=Content(role="user", parts=[Part(text=prompt)]),
        run_config=RunConfig(max_llm_calls=max_llm_calls),
    ):
        function_calls.extend(
            call.name for call in event.get_function_calls() if call.name
        )
        if event.is_final_response() and event.content and event.content.parts:
            final_texts.extend(
                part.text
                for part in event.content.parts
                if part.text and not part.thought
            )

    updated_session = await session_service.get_session(
        app_name=APP_NAME,
        user_id="structured-smoke-user",
        session_id=session.id,
    )
    payload = (updated_session.state if updated_session else {}).get(
        ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY
    )
    structured = _coerce_structured_payload(payload, final_texts=final_texts)
    passed = (
        structured is not None
        and structured.reply_text == EXPECTED_REPLY
        and structured.final_file_paths == []
        and "get_structured_output_smoke_status" in function_calls
    )
    return {
        "model": model_label,
        "status": "success" if passed else "failed",
        "function_calls": function_calls,
        "structured_response": structured.model_dump(mode="json") if structured else None,
        "final_text": "".join(final_texts).strip(),
    }


def _coerce_structured_payload(
    payload: Any,
    *,
    final_texts: list[str],
) -> OrchestratorFinalResponse | None:
    """Coerce ADK output_key state or final text into the Orchestrator schema."""
    candidates: list[Any] = []
    if payload is not None:
        candidates.append(payload)
    joined_text = "".join(final_texts).strip()
    if joined_text:
        candidates.append(joined_text)
    for candidate in candidates:
        try:
            if isinstance(candidate, str):
                return OrchestratorFinalResponse.model_validate_json(candidate)
            return OrchestratorFinalResponse.model_validate(candidate)
        except Exception:
            continue
    return None


async def run_all(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Run all requested smoke cases and keep going after provider failures."""
    results: list[dict[str, Any]] = []
    for model_reference in args.models or [""]:
        model_label = model_reference or "<configured>"
        try:
            if not args.force_native:
                unsupported = _unsupported_provider_result(model_reference)
                if unsupported is not None:
                    results.append(unsupported)
                    continue
            with _provider_output_context(verbose=args.verbose):
                results.append(
                    await run_smoke_case(
                        model_reference=model_reference,
                        prompt=args.prompt,
                        max_llm_calls=args.max_llm_calls,
                    )
                )
        except Exception as exc:
            results.append(
                {"model": model_label, "status": "error", "error": str(exc)}
            )
    return results


def _unsupported_provider_result(model_reference: str) -> dict[str, Any] | None:
    """Return an unsupported result when provider auto mode should not use native schema."""
    provider_name, model_name = resolve_provider_and_model(model_reference or None)
    provider_spec = get_provider_spec(provider_name)
    if provider_spec.native_structured_output:
        return None
    return {
        "model": f"{provider_name}/{model_name}",
        "status": "unsupported",
        "reason": (
            "provider resolves to prompt_json in auto mode; native "
            "output_schema + tools is not supported by this smoke check. "
            "Use --force-native to probe it anyway."
        ),
    }


@contextmanager
def _provider_output_context(*, verbose: bool) -> Iterator[None]:
    """Keep provider smoke failures compact unless verbose output is requested."""
    if verbose:
        yield
        return

    previous_disable_level = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with (
            warnings.catch_warnings(),
            redirect_stderr(io.StringIO()),
            redirect_stdout(io.StringIO()),
        ):
            warnings.simplefilter("ignore")
            yield
    finally:
        logging.disable(previous_disable_level)


def main() -> int:
    """Run the provider smoke cases and print compact JSON results."""
    args = parse_args()
    results = asyncio.run(run_all(args))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if args.result_json:
        args.result_json.parent.mkdir(parents=True, exist_ok=True)
        args.result_json.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    passing_statuses = {"success", "unsupported"}
    return 0 if all(result["status"] in passing_statuses for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
