"""Run a local smoke test for the PPT product line and print generated files."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService

from src.agents.orchestrator.orchestrator_agent import Orchestrator
from src.runtime.workspace import inbox_root, resolve_workspace_path, workspace_relative_path

APP_NAME = "creative_claw_ppt_smoke"
DEFAULT_TASK = "基于材料生成 6 页 PPTX，用于介绍 Creative Claw PPT 产品线的进展和下一步计划。"
DEFAULT_BRIEF = """# Creative Claw PPT 产品线体验简报

## 当前目标
- PPTX 交付优先走 run_ppt_product，避免和旧 skills/pptx 互相冲突。
- HTML 路线先跑通，再逐步补 SVG 路线和 XML 路线。
- 产品链路需要保持 Google ADK 原生，PptProductManager 是产品级 LlmAgent。

## 已完成能力
- PptProductManager 可以整理需求、准备源材料、选择路线并登记交付结果。
- ContentPlanningAgent 可以读取 Markdown 材料并生成模板无关的 DeckContentPlan。
- HTML route 可以输出 HTML deck、PNG 预览、quality report 和可编辑 PPTX。

## 下一步
- 抽出共享 QualityDeliveryAgent。
- 补齐 clean_business 模板包文件。
- 增加 SVG 和 XML 路线。
"""


def parse_args() -> argparse.Namespace:
    """Parse local smoke-run arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default=DEFAULT_TASK, help="PPT task passed to run_ppt_product.")
    parser.add_argument("--source", type=Path, help="Optional local Markdown file used as source material.")
    parser.add_argument("--slides", type=int, default=6, help="Target slide count for the generated PPTX.")
    parser.add_argument("--session-id", default="", help="Workspace session id for generated files.")
    parser.add_argument("--result-json", type=Path, help="Optional path to save the compact smoke result JSON.")
    return parser.parse_args()


async def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    """Run the PPT product through the orchestrator tool and return file paths."""
    session_id = args.session_id or f"ppt-smoke-{int(time.time())}"
    source_record = _prepare_source(args.source, session_id=session_id)
    session_service = InMemorySessionService()
    artifact_service = InMemoryArtifactService()
    orchestrator = Orchestrator(
        session_service=session_service,
        artifact_service=artifact_service,
        expert_agents={},
        app_name=APP_NAME,
    )
    tool_context = SimpleNamespace(
        state={
            "sid": session_id,
            "turn_index": 1,
            "step": 1,
            "expert_step": 0,
        }
    )

    result = await orchestrator.run_ppt_product(
        task=args.task,
        inputs=[source_record],
        output={"format": "pptx", "route": "html", "slide_count": args.slides},
        tool_context=tool_context,
    )
    return _build_compact_result(result, source_record=source_record)


def _prepare_source(source: Path | None, *, session_id: str) -> dict[str, str]:
    """Stage a Markdown source into the Creative Claw workspace."""
    source_dir = inbox_root() / "ppt_smoke" / session_id
    source_dir.mkdir(parents=True, exist_ok=True)
    if source is None:
        staged_path = source_dir / "creative_claw_ppt_brief.md"
        staged_path.write_text(DEFAULT_BRIEF, encoding="utf-8")
    else:
        source_path = source.expanduser().resolve()
        if not source_path.exists() or not source_path.is_file():
            raise FileNotFoundError(f"Source file does not exist: {source_path}")
        if source_path.suffix.lower() not in {".md", ".markdown", ".mdown", ".mkd"}:
            raise ValueError("The smoke runner currently accepts Markdown sources only.")
        staged_path = source_dir / source_path.name
        if staged_path.resolve() != source_path:
            shutil.copy2(source_path, staged_path)

    return {
        "name": staged_path.name,
        "path": workspace_relative_path(staged_path),
        "mime_type": "text/markdown",
        "role": "source",
        "description": "PPT smoke-run source material.",
    }


def _build_compact_result(result: dict[str, Any], *, source_record: dict[str, str]) -> dict[str, Any]:
    """Build a compact, user-facing result payload."""
    manifest = dict(result.get("delivery_manifest") or {})
    route_build = dict(result.get("route_build") or {})
    final_pptx = str(manifest.get("final_pptx") or "")
    html_deck = str(route_build.get("html_deck_path") or "")
    previews = list(manifest.get("previews") or [])
    return {
        "status": result.get("status"),
        "message": result.get("message"),
        "selected_route": result.get("selected_route"),
        "source": source_record,
        "final_pptx": _to_absolute_path(final_pptx),
        "html_deck": _to_absolute_path(html_deck),
        "previews": [_to_absolute_path(path) for path in previews],
        "quality_report": _to_absolute_path(str(manifest.get("quality_report") or "")),
        "build_log": _to_absolute_path(str(manifest.get("build_log") or "")),
        "warnings": result.get("warnings") or [],
        "next_actions": result.get("next_actions") or [],
    }


def _to_absolute_path(path: str) -> str:
    """Convert a workspace-relative output path to an absolute path when possible."""
    if not path:
        return ""
    try:
        return str(resolve_workspace_path(path))
    except Exception:
        return path


def main() -> int:
    """Run the smoke command and print the generated artifact locations."""
    args = parse_args()
    compact_result = asyncio.run(run_smoke(args))
    print(json.dumps(compact_result, ensure_ascii=False, indent=2))
    if args.result_json:
        args.result_json.parent.mkdir(parents=True, exist_ok=True)
        args.result_json.write_text(
            json.dumps(compact_result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if compact_result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
