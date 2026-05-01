"""Run the ADK live eval for Design product-line Orchestrator routing."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AGENT_DIR = PROJECT_ROOT / "evals" / "creative_claw_orchestrator" / "adk_app"
DEFAULT_EVALSET = PROJECT_ROOT / "evals" / "creative_claw_orchestrator" / "design_product_live_evalset.json"
DEFAULT_CONFIG = PROJECT_ROOT / "evals" / "creative_claw_orchestrator" / "adk_eval_config.json"


def build_command(args: argparse.Namespace) -> list[str]:
    """Build the `adk eval` command for the Design live eval."""
    project_adk = PROJECT_ROOT / ".venv" / "bin" / "adk"
    adk_bin = args.adk_bin or (str(project_adk) if project_adk.exists() else "") or shutil.which("adk") or "adk"
    command = [
        adk_bin,
        "eval",
        str(args.agent_dir),
        str(args.evalset),
        f"--config_file_path={args.config}",
        "--print_detailed_results",
    ]
    if args.log_level:
        command.append(f"--log_level={args.log_level}")
    return command


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the live eval runner."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-dir", type=Path, default=DEFAULT_AGENT_DIR)
    parser.add_argument("--evalset", type=Path, default=DEFAULT_EVALSET)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--adk-bin", default="")
    parser.add_argument("--log-level", default="")
    parser.add_argument("--dry-run", action="store_true", help="Print the command without running live eval.")
    return parser.parse_args()


def main() -> int:
    """Run the configured ADK live eval command."""
    args = parse_args()
    command = build_command(args)
    print(" ".join(command))
    if args.dry_run:
        return 0
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
