"""Run live-provider smoke checks for PPT ADK HITL multi-turn runtime flow.

The smoke keeps the top-level Orchestrator model live for each provider while
stubbing heavyweight PPT phases. This validates provider behavior around ADK
tool calls, tool-confirmation resume, plain-text continuation, Web/CLI-style
runtime state, and final artifact registration without spending provider calls
on slide rendering internals.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Iterator
import warnings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("CREATIVE_CLAW_CONSOLE_LOG_LEVEL", "CRITICAL")

from conf.llm import resolve_llm_model_name
from conf.system import SYS_CONFIG
from src.runtime.models import InboundMessage, WorkflowEvent
from src.runtime.workflow_service import CreativeClawRuntime
from src.runtime.workspace import resolve_workspace_path
from unit_test.ppt_runtime_smoke_helpers import RuntimePptSmokePatch

DEFAULT_TASK = "做一个 3 页 PPTX，用于产品发布，受众为管理层。请输出可编辑 PPTX，使用 HTML route。"


def parse_args() -> argparse.Namespace:
    """Parse provider smoke arguments."""
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
    parser.add_argument("--task", default=DEFAULT_TASK, help="Initial PPT request.")
    parser.add_argument("--slides", type=int, default=3, help="Expected slide count for the smoke.")
    parser.add_argument(
        "--turn-timeout",
        type=float,
        default=120.0,
        help="Timeout in seconds for each runtime turn.",
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
        "--structured-first-confirmation",
        action="store_true",
        help=(
            "Send the first confirmation through runtime metadata "
            "ppt_confirmation_response instead of relying only on plain text."
        ),
    )
    return parser.parse_args()


async def run_provider_case(
    *,
    model_reference: str,
    task: str,
    slides: int,
    turn_timeout: float,
    structured_first_confirmation: bool,
) -> dict[str, Any]:
    """Run one live Orchestrator provider through the PPT multi-turn HITL path."""
    model_label = resolve_llm_model_name(model_reference or None)
    runtime = CreativeClawRuntime(llm_model=model_reference)
    session_key = f"provider-ppt-hitl-{_safe_label(model_label)}"
    sender_id = f"{session_key}-user"
    first_confirmation_metadata = {"product_line": "ppt", "adk_hitl": True}
    if structured_first_confirmation:
        first_confirmation_metadata["ppt_confirmation_response"] = {"action": "confirm"}

    with RuntimePptSmokePatch(task=task, slide_count=slides).install_phase_stubs():
        turns = [
            await _run_turn(
                runtime,
                text=task,
                run_id=f"{session_key}-1",
                sender_id=sender_id,
                session_key=session_key,
                timeout=turn_timeout,
                metadata={"product_line": "ppt", "adk_hitl": True},
            ),
            await _run_turn(
                runtime,
                text="确认",
                run_id=f"{session_key}-2",
                sender_id=sender_id,
                session_key=session_key,
                timeout=turn_timeout,
                metadata=first_confirmation_metadata,
            ),
            await _run_turn(
                runtime,
                text="确认",
                run_id=f"{session_key}-3",
                sender_id=sender_id,
                session_key=session_key,
                timeout=turn_timeout,
                metadata={"product_line": "ppt", "adk_hitl": True},
            ),
        ]

    runtime_session_id = runtime._session_keys.get(f"runtime-provider-smoke:{session_key}", "")
    session = None
    if runtime_session_id:
        session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=sender_id,
            session_id=runtime_session_id,
        )
    state = dict(session.state) if session else {}
    final_file_paths = list(state.get("final_file_paths") or [])
    final_files_exist = [
        _workspace_file_exists(path) for path in final_file_paths if isinstance(path, str)
    ]
    passed = (
        "请确认 PPT 需求参数" in turns[0]["final_text"]
        and "请确认 PPT 内容规划" in turns[1]["final_text"]
        and state.get("ppt_product_result", {}).get("status") == "success"
        and state.get("ppt_workflow_state", {}).get("stage") == "completed"
        and bool(final_file_paths)
        and all(final_files_exist)
    )
    return {
        "model": model_label,
        "status": "success" if passed else "failed",
        "turns": turns,
        "runtime_session_id": runtime_session_id,
        "ppt_stage": state.get("ppt_workflow_state", {}).get("stage"),
        "product_status": state.get("ppt_product_result", {}).get("status"),
        "final_file_paths": final_file_paths,
        "final_files_exist": final_files_exist,
        "structured_first_confirmation": structured_first_confirmation,
    }


async def _run_turn(
    runtime: CreativeClawRuntime,
    *,
    text: str,
    run_id: str,
    sender_id: str,
    session_key: str,
    timeout: float,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Run one runtime turn and return the compact final event summary."""

    async def _collect() -> list[WorkflowEvent]:
        events: list[WorkflowEvent] = []
        inbound_metadata = dict(metadata)
        inbound_metadata["run_id"] = run_id
        async for event in runtime.run_message(
            InboundMessage(
                channel="runtime-provider-smoke",
                sender_id=sender_id,
                chat_id=session_key,
                text=text,
                metadata=inbound_metadata,
            )
        ):
            events.append(event)
        return events

    events = await asyncio.wait_for(_collect(), timeout=timeout)
    errors = [event.text for event in events if event.event_type == "error"]
    finals = [event for event in events if event.event_type == "final"]
    final_event = finals[-1] if finals else None
    return {
        "input": text,
        "run_id": run_id,
        "status": "error" if errors else "final" if final_event else "missing_final",
        "final_text": final_event.text if final_event else "",
        "artifact_paths": list(final_event.artifact_paths) if final_event else [],
        "errors": errors,
    }


def _safe_label(value: str) -> str:
    """Return a compact filesystem/session-safe provider label."""
    return "".join(ch if ch.isalnum() else "-" for ch in str(value or "configured")).strip("-")[:80] or "configured"


def _workspace_file_exists(path: str) -> bool:
    """Return whether a workspace-relative file exists."""
    try:
        return resolve_workspace_path(path).is_file()
    except Exception:
        return False


async def run_all(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Run all requested provider cases and keep going after provider failures."""
    results: list[dict[str, Any]] = []
    for model_reference in args.models or [""]:
        model_label = model_reference or "<configured>"
        try:
            with _provider_output_context(verbose=args.verbose):
                result = await run_provider_case(
                    model_reference=model_reference,
                    task=args.task,
                    slides=args.slides,
                    turn_timeout=args.turn_timeout,
                    structured_first_confirmation=args.structured_first_confirmation,
                )
            results.append(result)
        except Exception as exc:
            results.append(
                {
                    "model": model_label,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return results


@contextmanager
def _provider_output_context(*, verbose: bool) -> Iterator[None]:
    """Keep provider smoke output compact unless verbose output is requested."""
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
    """Run provider smoke cases and print compact JSON results."""
    args = parse_args()
    results = asyncio.run(run_all(args))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if args.result_json:
        args.result_json.parent.mkdir(parents=True, exist_ok=True)
        args.result_json.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if all(result.get("status") == "success" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
