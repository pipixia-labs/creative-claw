"""Run a live LLM route check for Design product-line Orchestrator tool choice."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from src.agents.orchestrator.orchestrator_agent import Orchestrator
from src.runtime.expert_registry import build_expert_agents

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVALSET = PROJECT_ROOT / "evals" / "creative_claw_orchestrator" / "design_product_live_evalset.json"
APP_NAME = "creative_claw_design_route_check"


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the live route checker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evalset", type=Path, default=DEFAULT_EVALSET)
    return parser.parse_args()


async def run_case(
    *,
    case: dict[str, Any],
    expert_agents: dict[str, BaseAgent],
) -> dict[str, Any]:
    """Run one live eval case and return observed tool calls."""
    session_service = InMemorySessionService()
    artifact_service = InMemoryArtifactService()
    orchestrator = Orchestrator(
        session_service=session_service,
        artifact_service=artifact_service,
        expert_agents=expert_agents,
        app_name=APP_NAME,
    )
    session_input = dict(case.get("session_input") or {})
    state = dict(session_input.get("state") or {})
    user_id = str(session_input.get("user_id") or "eval-user")
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        state=state,
    )
    invocation = case["conversation"][0]
    prompt = _extract_text(invocation["user_content"])
    expected_tool = invocation["intermediate_data"]["tool_uses"][0]["name"]
    observed_calls: list[dict[str, Any]] = []
    async for event in orchestrator.runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=Content(role="user", parts=[Part(text=prompt)]),
    ):
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if not part.function_call:
                continue
            observed_calls.append(
                {
                    "name": part.function_call.name,
                    "args": dict(part.function_call.args or {}),
                }
            )
    observed_names = [call["name"] for call in observed_calls]
    return {
        "eval_id": case["eval_id"],
        "expected_tool": expected_tool,
        "observed_calls": observed_calls,
        "passed": expected_tool in observed_names,
    }


def _extract_text(content: dict[str, Any]) -> str:
    """Extract text from one ADK eval user content object."""
    parts = list(content.get("parts") or [])
    return "\n".join(str(part.get("text", "")) for part in parts if str(part.get("text", "")).strip())


async def run_evalset(evalset_path: Path) -> list[dict[str, Any]]:
    """Run all cases in one live Design route evalset."""
    payload = json.loads(evalset_path.read_text(encoding="utf-8"))
    expert_agents = build_expert_agents(app_name=APP_NAME)
    results = []
    for case in payload["eval_cases"]:
        results.append(await run_case(case=case, expert_agents=expert_agents))
    return results


def main() -> int:
    """Run the live route check and print a compact summary."""
    args = parse_args()
    results = asyncio.run(run_evalset(args.evalset))
    passed = sum(1 for result in results if result["passed"])
    for result in results:
        status = "PASS" if result["passed"] else "FAIL"
        observed = ", ".join(call["name"] for call in result["observed_calls"]) or "<none>"
        print(f"{status} {result['eval_id']}: expected={result['expected_tool']} observed={observed}")
    print(f"Route check: {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
