"""Unified command-line entrypoint for CreativeClaw."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from conf.app_config import initialize_runtime_config
from conf.channel import CHANNEL_CONFIG, WebChannelConfig
from src.chat_runner import run_chat_service, run_cli_chat


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CreativeClaw CLI parser."""
    parser = argparse.ArgumentParser(
        prog="creative-claw",
        description="Unified CLI entrypoint for CreativeClaw chat channels.",
    )
    command_parsers = parser.add_subparsers(dest="command")
    command_parsers.required = True

    init_parser = command_parsers.add_parser(
        "init",
        help="Initialize the user-home CreativeClaw runtime directory and conf.json.",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing conf.json with the default template.",
    )

    design_parser = command_parsers.add_parser(
        "design",
        help="Run the Design product line from a CLI message.",
    )
    design_parser.add_argument(
        "--user-id",
        type=str,
        default="cli-user",
        help="Logical user ID for the Design CLI session.",
    )
    design_parser.add_argument(
        "--chat-id",
        type=str,
        default="design",
        help="Logical chat ID for the Design CLI session.",
    )
    design_parser.add_argument(
        "--message",
        type=str,
        required=True,
        help="Design request to send to the Design product line.",
    )
    design_parser.add_argument(
        "--scenario",
        type=str,
        default="",
        help="Optional design scenario hint, such as dashboard, landing_page, mobile_app, or deck.",
    )
    design_parser.add_argument(
        "--ask-questions",
        action="store_true",
        help="Ask scenario-specific design questions instead of proceeding with default assumptions.",
    )
    design_parser.add_argument(
        "--design-system",
        type=str,
        default="",
        help="Optional design system override, such as linear-app, stripe, apple, or default.",
    )
    design_parser.add_argument(
        "--task-skill",
        type=str,
        default="",
        help="Optional task skill override, such as dashboard, saas-landing, or mobile-app.",
    )
    design_parser.add_argument(
        "--device-frame",
        type=str,
        default="",
        help="Optional device frame override, such as browser-chrome or iphone-15-pro.",
    )
    design_parser.add_argument(
        "--output-format",
        type=str,
        default="html",
        help="Output format for code-backed design artifacts. Defaults to html.",
    )
    design_parser.add_argument(
        "--output-path",
        type=str,
        default="",
        help="Optional workspace-relative output path for the generated artifact.",
    )
    design_parser.add_argument(
        "--attachment",
        action="append",
        default=[],
        metavar="PATH",
        help="Attachment path for the Design request. Repeat this flag to send multiple files.",
    )

    chat_parser = command_parsers.add_parser(
        "chat",
        help="Start one CreativeClaw chat channel.",
    )
    channel_parsers = chat_parser.add_subparsers(dest="channel")
    channel_parsers.required = True

    cli_parser = channel_parsers.add_parser(
        "cli",
        help="Start the CLI terminal chat channel.",
    )
    cli_parser.add_argument(
        "--user-id",
        type=str,
        default="cli-user",
        help="Logical user ID for the CLI channel session.",
    )
    cli_parser.add_argument(
        "--chat-id",
        type=str,
        default="terminal",
        help="Logical chat ID for the CLI channel session.",
    )
    cli_parser.add_argument(
        "--message",
        type=str,
        help="Exit after sending a single message (non-interactive mode).",
    )
    cli_parser.add_argument(
        "--attachment",
        action="append",
        default=[],
        metavar="PATH",
        help="Attachment path for non-interactive mode. Repeat this flag to send multiple files.",
    )

    for name in ("telegram", "feishu"):
        channel_parsers.add_parser(
            name,
            help=f"Start the {name} chat channel.",
        )

    web_parser = channel_parsers.add_parser(
        "web",
        help="Start the local browser web chat channel.",
    )
    web_parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Host interface for the web chat server. Defaults to the configured web host in conf.json.",
    )
    web_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for the web chat server. Defaults to the configured web port in conf.json.",
    )
    web_parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Browser page title for the web chat surface.",
    )
    web_parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the browser automatically after the web chat server starts.",
    )

    return parser


def collect_cli_attachment_paths(args: argparse.Namespace) -> list[str]:
    """Collect CLI attachment paths from the current public flag."""
    return list(getattr(args, "attachment", []) or [])


def collect_design_product_metadata(args: argparse.Namespace) -> dict[str, object]:
    """Build runtime metadata for the Design product-line CLI command."""
    return {
        "product_line": "design",
        "design": {
            "scenario": str(args.scenario or "").strip(),
            "allow_assumptions": not bool(args.ask_questions),
            "design_system": str(args.design_system or "").strip(),
            "task_skill": str(args.task_skill or "").strip(),
            "device_frame": str(args.device_frame or "").strip(),
            "output_format": str(args.output_format or "html").strip() or "html",
            "output_path": str(args.output_path or "").strip(),
        },
    }


def build_web_channel_config(args: argparse.Namespace) -> WebChannelConfig:
    """Build the effective web channel config from defaults plus CLI overrides."""
    return CHANNEL_CONFIG.web.model_copy(
        update={
            "host": args.host or CHANNEL_CONFIG.web.host,
            "port": args.port if args.port is not None else CHANNEL_CONFIG.web.port,
            "title": args.title or CHANNEL_CONFIG.web.title,
            "open_browser": bool(args.open_browser or CHANNEL_CONFIG.web.open_browser),
        }
    )


async def run_cli(args: argparse.Namespace) -> int:
    """Run the parsed CreativeClaw CLI command."""
    if args.command == "init":
        config_path, workspace_path, created = initialize_runtime_config(force=bool(args.force))
        action = "created" if created else "kept"
        print(f"Runtime directory ready: {config_path.parent}")
        print(f"Config file {action}: {config_path}")
        print(f"Workspace ready: {workspace_path}")
        return 0

    if args.command == "design":
        await run_cli_chat(
            user_id=args.user_id,
            chat_id=args.chat_id,
            message=args.message,
            attachment_paths=collect_cli_attachment_paths(args),
            metadata=collect_design_product_metadata(args),
        )
        return 0

    if args.command != "chat":
        raise ValueError(f"Unsupported command '{args.command}'.")

    if args.channel == "cli":
        await run_cli_chat(
            user_id=args.user_id,
            chat_id=args.chat_id,
            message=args.message,
            attachment_paths=collect_cli_attachment_paths(args),
        )
        return 0

    if args.channel == "web":
        await run_chat_service(args.channel, web_config=build_web_channel_config(args))
        return 0

    await run_chat_service(args.channel)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run the requested CreativeClaw CLI command."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return asyncio.run(run_cli(args))


if __name__ == "__main__":
    raise SystemExit(main())
