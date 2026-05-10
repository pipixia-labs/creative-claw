import json
import shlex
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.adk.agents.run_config import StreamingMode
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event
from google.adk.sessions import InMemorySessionService
from google.adk.sessions.state import State
from google.genai.types import Content, Part

from conf.system import SYS_CONFIG
from src.agents.experts.image_grounding.image_grounding_agent import ImageGroundingAgent
from src.agents.orchestrator.final_response import (
    ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY,
    OrchestratorFinalResponse,
)
from src.agents.orchestrator.orchestrator_agent import (
    Orchestrator,
    _ReplyTextStreamExtractor,
    _extract_confirmation_tool_result,
    _extract_question_form_tool_result,
    _format_confirmation_reply,
    _format_question_form_reply,
    _normalize_final_response_paths,
    orchestrator_before_model_callback,
)
from src.runtime.adk_compat import annotate_agent_origin
from src.runtime.step_events import configure_step_event_publisher
from src.runtime.tool_context import route_context
from src.runtime.workspace import build_workspace_file_record, workspace_relative_path, workspace_root


class OrchestratorTests(unittest.TestCase):
    def test_instruction_mentions_structured_final_response_contract(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )

        instruction = orchestrator._build_instruction()

        self.assertIn("Do not create a full upfront plan", instruction)
        self.assertIn("list_skills", instruction)
        self.assertIn("read_skill", instruction)
        self.assertIn("web_fetch", instruction)
        self.assertIn("web_search", instruction)
        self.assertIn("image_crop", instruction)
        self.assertIn("image_rotate", instruction)
        self.assertIn("image_flip", instruction)
        self.assertIn("image_info", instruction)
        self.assertIn("video_info", instruction)
        self.assertIn("audio_info", instruction)
        self.assertIn("glob", instruction)
        self.assertIn("grep", instruction)
        self.assertIn("exec_command", instruction)
        self.assertIn("process_session", instruction)
        self.assertIn("invoke_agent(agent_name, prompt)", instruction)
        self.assertIn("Do not output internal workflow JSON", instruction)
        self.assertIn("keep changes small and reviewable", instruction.lower())
        self.assertIn("re-check the latest state", instruction.lower())
        self.assertIn("main conversational agent", instruction.lower())
        self.assertIn("coding, debugging, and file-editing tasks", instruction.lower())
        self.assertIn("background=true", instruction)
        self.assertIn("aspect_ratio", instruction)
        self.assertIn("resolution", instruction)
        self.assertIn("duration_seconds", instruction)
        self.assertIn("video_extension", instruction)
        self.assertIn("Seedance 2.0", instruction)
        self.assertIn("generate_audio=true", instruction)
        self.assertIn("watermark", instruction)
        self.assertIn('prompt_rewrite="off"', instruction)
        self.assertIn("native audio", instruction)
        self.assertIn("SRT/VTT", instruction)
        self.assertIn("SpeechRecognitionExpert", instruction)
        self.assertIn("seedance", instruction)
        self.assertIn("visual-only", instruction)
        self.assertIn("kling-v1-6", instruction)
        self.assertIn("nano_banana", instruction)
        self.assertIn("seedream", instruction)
        self.assertIn("wan2.7-image-pro", instruction)
        self.assertIn("happyhorse-1.0-t2v", instruction)
        self.assertIn("Do not route DashScope video editing or reference-video requests yet", instruction)
        self.assertIn("<skills>", instruction)
        self.assertIn("planning-with-files", instruction)
        self.assertIn("workspace file history", instruction)
        self.assertIn("input_path", instruction)
        self.assertIn("`input_name` is legacy", instruction)
        self.assertIn("list_session_files(section=...)", instruction)
        self.assertIn("reply_text", instruction)
        self.assertIn("final_file_paths", instruction)
        self.assertIn("The final structured response is the only final delivery", instruction)
        self.assertNotIn("message(content=...)", instruction)
        self.assertNotIn("message_file(paths=..., caption=...)", instruction)
        self.assertNotIn("message_image(paths=..., caption=...)", instruction)
        self.assertNotIn("set_final_files(paths=[...])", instruction)
        self.assertIn("aligned with the user's language", instruction)
        self.assertIn("If the user mixes languages", instruction)
        self.assertIn("delivery channel context", instruction)
        self.assertIn("Do not expose raw routing identifiers", instruction)
        self.assertIn("Expert parameter contracts", instruction)
        self.assertIn("SearchAgent: required=query, mode", instruction)
        self.assertIn("plain_prompt=yes", instruction)
        self.assertIn("ImageEditingAgent: required=prompt, input_path or input_paths", instruction)
        self.assertIn("plain_prompt=no", instruction)
        self.assertIn("text prompts only", instruction)
        self.assertIn("modify one or more existing workspace images", instruction)
        self.assertIn("Use mode `prompt`", instruction)
        self.assertIn("save a binary mask image file", instruction)
        self.assertIn("return bounding boxes", instruction)
        self.assertIn("deterministic local image operations", instruction)
        self.assertIn("Serper image search", instruction)
        self.assertIn("Use this expert for text-to-video", instruction)
        self.assertIn("ImageBasicOperations", instruction)
        self.assertIn("VideoBasicOperations", instruction)
        self.assertIn("AudioBasicOperations", instruction)
        self.assertIn("deterministic local video operations", instruction)
        self.assertIn("deterministic local audio operations", instruction)
        self.assertIn("default resource id is `seed-tts-2.0`", instruction)
        self.assertIn("voice_name", instruction)
        self.assertIn("code default model is `music-2.5`", instruction)
        self.assertIn("Provider `hy3d` remains the default", instruction)
        self.assertIn("doubao-seed3d-2-0-260328", instruction)
        self.assertIn("hyper3d-gen2-260112", instruction)
        self.assertIn("hitem3d-2-0-251223", instruction)
        self.assertIn("Use `task=subtitle`", instruction)
        self.assertIn("SRT/VTT", instruction)
        self.assertIn("Creative workflow routing hints", instruction)
        self.assertIn("creative-brief-to-storyboard", instruction)
        self.assertIn("narration-to-visual-prompts", instruction)
        self.assertIn("asset-to-script", instruction)
        self.assertIn("style-brief-to-prompt", instruction)
        self.assertIn("creative-workflow-router", instruction)
        self.assertIn("creative-qc", instruction)
        self.assertIn("do not skip straight to `ImageGenerationAgent` or `VideoGenerationAgent`", instruction)
        self.assertIn("Product line: design", instruction)
        self.assertIn("run_ppt_product", instruction)
        self.assertIn("PPT workflow routing hints", instruction)
        self.assertIn("prefer `run_ppt_product`", instruction)
        self.assertIn("do not rewrite it into a slide outline", instruction)
        self.assertIn("Do not put your own inferred outline", instruction)
        self.assertIn("Do not route PPTX delivery through DesignProductManager", instruction)
        self.assertIn("run_design_product", instruction)
        self.assertIn("[cc-form-answers", instruction)
        self.assertIn("Product line options", instruction)
        self.assertIn("If the user asks for UI design", instruction)
        self.assertIn("Product-line tools have priority over skills", instruction)
        self.assertIn("Route by the requested final deliverable", instruction)
        self.assertIn("standalone image deliverables stay with the orchestrator", instruction)
        self.assertIn("If the current product lines cannot handle", instruction)
        self.assertIn("greeting card", instruction)
        self.assertIn("product first, skills second", instruction)
        self.assertIn("prefer `run_design_product`", instruction)

    def test_agent_uses_structured_output_schema(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )

        self.assertIs(orchestrator.agent.output_schema, OrchestratorFinalResponse)
        self.assertEqual(
            orchestrator.agent.output_key,
            ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY,
        )

    def test_reply_text_stream_extractor_only_emits_reply_text(self) -> None:
        extractor = _ReplyTextStreamExtractor()

        chunks = [
            '{"reply_text":"Hi',
            ', I am CreativeClaw',
            '.\\nNice to meet you", "final_file_paths":[]}',
        ]
        deltas = [delta for chunk in chunks if (delta := extractor.append(chunk))]

        self.assertEqual(deltas, ["Hi", ", I am CreativeClaw", ".\nNice to meet you"])

    def test_list_skills_records_orchestration_step(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={})

        orchestrator.list_skills(tool_context=tool_context)

        events = tool_context.state["orchestration_events"]
        self.assertEqual(events[0]["title"], "List Skills")
        self.assertEqual(events[0]["stage"], "planning")

    def test_read_file_records_orchestration_step(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={})

        orchestrator.read_file("README.md", tool_context=tool_context)

        events = tool_context.state["orchestration_events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["title"], "read_file")
        self.assertIn("path=README.md", events[0]["detail"])
        self.assertIn("Status: started", events[0]["detail"])
        self.assertIn("Result:", events[1]["detail"])
        self.assertIn("path=README.md", events[1]["detail"])

    def test_exec_command_records_created_workspace_files(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={"turn_index": 1, "step": 0, "expert_step": 0})

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            output_path = Path(tmpdir) / "result.png"
            relative_output_path = workspace_relative_path(output_path)

            result = orchestrator.exec_command(
                f"printf 'card' > {shlex.quote(relative_output_path)}",
                tool_context=tool_context,
            )
            payload = json.loads(
                orchestrator.list_session_files(section="latest_output", tool_context=tool_context)
            )
            normalized_paths = _normalize_final_response_paths(
                [relative_output_path],
                state=tool_context.state,
            )

        self.assertNotIn("Exit code:", result)
        self.assertEqual(len(payload["latest_output_files"]), 1)
        self.assertEqual(payload["latest_output_files"][0]["path"], relative_output_path)
        self.assertEqual(normalized_paths, [relative_output_path])

    def test_exec_command_does_not_record_files_from_failed_command(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={"turn_index": 1, "step": 0, "expert_step": 0})

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            output_path = Path(tmpdir) / "partial.png"
            relative_output_path = workspace_relative_path(output_path)

            result = orchestrator.exec_command(
                f"printf 'partial' > {shlex.quote(relative_output_path)}; exit 2",
                tool_context=tool_context,
            )
            output_exists = output_path.exists()
            payload = json.loads(
                orchestrator.list_session_files(section="latest_output", tool_context=tool_context)
            )

        self.assertIn("Exit code: 2", result)
        self.assertTrue(output_exists)
        self.assertEqual(payload["latest_output_files"], [])

    def test_list_session_files_returns_latest_output_records(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(
            state={
                "input_files": [],
                "new_files": [],
                "files_history": [
                    [{"name": "upload.png", "path": "inbox/cli/upload.png", "source": "channel"}],
                    [{"name": "result.png", "path": "generated/session/result.png", "source": "image_grounding"}],
                ],
            }
        )

        result = orchestrator.list_session_files(section="latest_output", tool_context=tool_context)
        payload = json.loads(result)

        self.assertEqual(len(payload["latest_output_files"]), 1)
        self.assertEqual(payload["latest_output_files"][0]["path"], "generated/session/result.png")

    def test_list_session_files_prefers_final_file_paths_for_latest_output(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            html_path = Path(tmpdir) / "dashboard.html"
            screenshot_path = Path(tmpdir) / "dashboard.png"
            html_path.write_text("<html></html>", encoding="utf-8")
            screenshot_path.write_bytes(b"fake-preview")
            html_record = build_workspace_file_record(
                html_path,
                description="final dashboard html",
                source="design_v2",
            )
            screenshot_record = build_workspace_file_record(
                screenshot_path,
                description="supporting preview screenshot",
                source="design_v2_preview",
            )
            tool_context = SimpleNamespace(
                state={
                    "input_files": [],
                    "generated": [html_record, screenshot_record],
                    "new_files": [html_record, screenshot_record],
                    "files_history": [[html_record, screenshot_record]],
                    "final_file_paths": [workspace_relative_path(html_path)],
                }
            )

            result = orchestrator.list_session_files(section="latest_output", tool_context=tool_context)

        payload = json.loads(result)

        self.assertEqual(len(payload["latest_output_files"]), 1)
        self.assertEqual(payload["latest_output_files"][0]["path"], html_record["path"])

    def test_list_session_files_prefers_new_non_channel_files_over_generated_history(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(
            state={
                "input_files": [],
                "new_files": [
                    {"name": "upload.png", "path": "inbox/cli/upload.png", "source": "channel"},
                    {"name": "new.html", "path": "generated/session/new.html", "source": "design_v2"},
                ],
                "generated": [
                    {"name": "old.html", "path": "generated/session/old.html", "source": "design_v2"},
                    {"name": "new.html", "path": "generated/session/new.html", "source": "design_v2"},
                ],
                "files_history": [
                    [{"name": "old.html", "path": "generated/session/old.html", "source": "design_v2"}],
                    [{"name": "new.html", "path": "generated/session/new.html", "source": "design_v2"}],
                ],
            }
        )

        result = orchestrator.list_session_files(section="latest_output", tool_context=tool_context)
        payload = json.loads(result)

        self.assertEqual(len(payload["latest_output_files"]), 1)
        self.assertEqual(payload["latest_output_files"][0]["path"], "generated/session/new.html")

    def test_normalize_final_response_paths_accepts_tracked_relative_file(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            file_path = Path(tmpdir) / "result.png"
            file_path.write_bytes(b"fake-image")
            file_record = build_workspace_file_record(
                file_path,
                description="generated image",
                source="image_generation",
            )

            normalized = _normalize_final_response_paths(
                [workspace_relative_path(file_path), workspace_relative_path(file_path)],
                state={"generated": [file_record]},
            )

        self.assertEqual(normalized, [workspace_relative_path(file_path)])

    def test_normalize_final_response_paths_rejects_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            file_path = Path(tmpdir) / "result.png"
            file_path.write_bytes(b"fake-image")
            file_record = build_workspace_file_record(
                file_path,
                description="generated image",
                source="image_generation",
            )

            with self.assertRaisesRegex(ValueError, "workspace-relative"):
                _normalize_final_response_paths(
                    [str(file_path.resolve())],
                    state={"generated": [file_record]},
                )

    def test_normalize_final_response_paths_rejects_untracked_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            file_path = Path(tmpdir) / "result.png"
            file_path.write_bytes(b"fake-image")

            with self.assertRaisesRegex(ValueError, "current session file history"):
                _normalize_final_response_paths(
                    [workspace_relative_path(file_path)],
                    state={"generated": []},
                )

    def test_summarize_read_file_result_prefers_preview(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "read_file",
            "line one\nline two\nline three\nline four",
        )

        self.assertEqual(status, "success")
        self.assertIn("Read succeeded", summary)
        self.assertIn("line one", summary)
        self.assertIn("End:", summary)
        self.assertIn("line four", summary)

    def test_summarize_list_dir_counts_entries(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "list_dir",
            "[D] src\n[F] README.md\n[F] pyproject.toml",
        )

        self.assertEqual(status, "success")
        self.assertIn("3 entries", summary)
        self.assertIn("README.md", summary)

    def test_summarize_exec_command_counts_lines(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "exec_command",
            "total 8\n-rw-r--r-- file.txt\n-rw-r--r-- app.py\nSTDERR:\nwarn one\nwarn two",
        )

        self.assertEqual(status, "success")
        self.assertIn("Command completed", summary)
        self.assertIn("about 3 stdout lines", summary)
        self.assertIn("about 2 stderr lines", summary)
        self.assertIn("stderr summary", summary)

    def test_summarize_background_exec_command_mentions_session(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "exec_command",
            "Command still running (session abc123, pid 456). Use process_session(action='list'|'poll') for follow-up.",
        )

        self.assertEqual(status, "success")
        self.assertIn("Background command started", summary)
        self.assertIn("abc123", summary)

    def test_summarize_glob_result_counts_matches(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "glob",
            "src/app.py\nsrc/nested/worker.py",
        )

        self.assertEqual(status, "success")
        self.assertIn("2 matching paths", summary)
        self.assertIn("src/app.py", summary)

    def test_summarize_process_session_result_mentions_status(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "process_session",
            "build finished\n\nStatus: exited\nExit code: 0",
        )

        self.assertEqual(status, "success")
        self.assertIn("Session update received", summary)
        self.assertIn("exited", summary)

    def test_summarize_web_fetch_uses_json_fields(self) -> None:
        payload = (
            "{"
            '"url":"https://example.com",'
            '"finalUrl":"https://example.com",'
            '"status":200,'
            '"extractor":"html",'
            '"truncated":false,'
            '"length":42,'
            '"text":"alpha\\nbeta\\ngamma\\ndelta"'
            "}"
        )
        status, summary = Orchestrator._summarize_tool_result("web_fetch", payload)

        self.assertEqual(status, "success")
        self.assertIn("extractor=html", summary)
        self.assertIn("alpha", summary)
        self.assertIn("End:", summary)
        self.assertIn("delta", summary)

    def test_summarize_invoke_agent_result_uses_structured_fields(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "invoke_agent",
            {
                "agent_name": "KnowledgeAgent",
                "status": "success",
                "message": "analysis complete",
                "output_text": "line one\nline two",
                "output_files": [{"path": "generated/demo.txt"}],
            },
        )

        self.assertEqual(status, "success")
        self.assertIn("KnowledgeAgent finished", summary)
        self.assertIn("files=1", summary)
        self.assertIn("analysis complete", summary)

    def test_summarize_invoke_agent_error_marks_failure(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "invoke_agent",
            {
                "agent_name": "SearchAgent",
                "status": "error",
                "message": "search failed",
            },
        )

        self.assertEqual(status, "error")
        self.assertIn("search failed", summary)

    def test_summarize_list_session_files_result_uses_latest_output_summary(self) -> None:
        payload = json.dumps(
            {
                "latest_output_files": [
                    {"path": "generated/session/a.png"},
                    {"path": "generated/session/b.png"},
                ]
            },
            ensure_ascii=False,
        )
        status, summary = Orchestrator._summarize_tool_result("list_session_files", payload)

        self.assertEqual(status, "success")
        self.assertIn("latest_output_files contains 2 record(s)", summary)
        self.assertIn("generated/session/a.png", summary)

    def test_format_confirmation_reply_uses_tool_confirmation_request(self) -> None:
        reply = _format_confirmation_reply(
            {
                "message": "请确认 PPT 需求参数。",
                "confirmation_request": {
                    "summary_markdown": "| 参数 | 当前值 |\n| --- | --- |\n| 页数 | 3 页 |",
                    "expected_user_action": "回复“确认”继续；或说明修改意见。",
                },
            }
        )

        self.assertIn("请确认 PPT 需求参数。", reply)
        self.assertIn("| 页数 | 3 页 |", reply)
        self.assertIn("回复“确认”继续", reply)

    def test_extract_confirmation_tool_result_from_function_response_event(self) -> None:
        tool_result = {
            "status": "awaiting_requirement_confirmation",
            "message": "请确认 PPT 需求参数。",
            "confirmation_request": {
                "summary_markdown": "| 参数 | 当前值 |\n| --- | --- |\n| 主题 | 英语单词 |",
                "expected_user_action": "回复“确认”继续。",
            },
        }
        event = SimpleNamespace(
            content=Content(
                role="user",
                parts=[
                    Part.from_function_response(
                        name="continue_ppt_product",
                        response=tool_result,
                    )
                ],
            )
        )

        self.assertEqual(_extract_confirmation_tool_result(event), tool_result)


class OrchestratorCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_before_model_callback_includes_workspace_file_history_without_new_upload(self) -> None:
        callback_context = SimpleNamespace(
            state={
                "workflow_status": "running",
                "step": 2,
                "user_prompt": "Flip this image upside down for me.",
                "channel": "cli",
                "chat_id": "terminal",
                "sender_id": "cli-user",
                "input_files": [],
                "summary_history": ["Call `ImageGenerationAgent` to generate an image."],
                "message_history": [
                    "ImageGenerationAgent has completed 1 image generation tasks: "
                    "image generation task1 success, output file: step1_generation_output0.png"
                ],
                "files_history": [
                    [
                        {
                            "name": "step1_generation_output0.png",
                            "path": "generated/session_1/step1_generation_output0.png",
                            "description": "generated image from previous step",
                        }
                    ]
                ],
                "new_files": [],
                "product_line": "design",
                "product_line_options": {
                    "product_line": "design",
                    "design": {
                        "output": {"format": "html"},
                    },
                },
            }
        )
        llm_request = SimpleNamespace(contents=[])

        await orchestrator_before_model_callback(callback_context, llm_request)

        self.assertEqual(len(llm_request.contents), 1)
        self.assertIsInstance(llm_request.contents[0], Content)
        prompt_text = "\n".join(
            part.text for part in llm_request.contents[0].parts if getattr(part, "text", None)
        )
        self.assertIn("step1_generation_output0.png", prompt_text)
        self.assertIn("Most recent available output files", prompt_text)
        self.assertIn("Delivery context: channel=cli; chat_id=terminal; sender_id=cli-user", prompt_text)
        self.assertIn("Product line: design", prompt_text)
        self.assertIn('"format": "html"', prompt_text)
        self.assertIn("Final response contract", prompt_text)
        self.assertIn("reply_text", prompt_text)
        self.assertIn("final_file_paths", prompt_text)
        self.assertIn("list_session_files(section=\"latest_output\")", prompt_text)

    async def test_before_model_callback_keeps_uploaded_images_as_path_references(self) -> None:
        callback_context = SimpleNamespace(
            state={
                "workflow_status": "running",
                "step": 0,
                "user_prompt": "Use this tldraw selection.",
                "channel": "web",
                "chat_id": "web-session",
                "sender_id": "browser",
                "uploaded": [
                    {
                        "name": "sketch-selection.png",
                        "path": "inbox/web/web-session/turn_1/01_sketch-selection.png",
                        "description": "Selected tldraw canvas export.",
                    }
                ],
                "generated": [],
            }
        )
        llm_request = SimpleNamespace(contents=[])

        await orchestrator_before_model_callback(callback_context, llm_request)

        all_parts = [part for content in llm_request.contents for part in content.parts]
        prompt_text = "\n".join(part.text for part in all_parts if getattr(part, "text", None))
        self.assertIn("sketch-selection.png", prompt_text)
        self.assertIn("Selected tldraw canvas export", prompt_text)
        self.assertFalse(any(getattr(part, "inline_data", None) is not None for part in all_parts))

    async def test_run_agent_stops_after_tool_confirmation_request(self) -> None:
        session_service = InMemorySessionService()
        orchestrator = Orchestrator(
            session_service=session_service,
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        user_id = "user-confirmation"
        session_id = "session-confirmation"
        await session_service.create_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
            state={},
        )

        tool_result = {
            "status": "awaiting_requirement_confirmation",
            "message": "请确认 PPT 需求参数。",
            "confirmation_request": {
                "summary_markdown": "| 参数 | 当前值 |\n| --- | --- |\n| 主题 | 英语单词 |",
                "expected_user_action": "回复“确认”继续。",
            },
        }

        class _FakeRunner:
            def __init__(self) -> None:
                self.continued_after_confirmation = False

            async def run_async(self, **_kwargs):
                yield Event(
                    author="CreativeClawOrchestrator",
                    content=Content(
                        role="user",
                        parts=[
                            Part.from_function_response(
                                name="continue_ppt_product",
                                response=tool_result,
                            )
                        ],
                    ),
                )
                self.continued_after_confirmation = True
                yield Event(
                    author="CreativeClawOrchestrator",
                    content=Content(role="model", parts=[Part(text="should not continue")]),
                )

        fake_runner = _FakeRunner()
        orchestrator.runner = fake_runner

        reply = await orchestrator.run_agent_and_log_events(
            user_id=user_id,
            session_id=session_id,
            new_message=Content(role="user", parts=[Part(text="确认")]),
        )

        self.assertFalse(fake_runner.continued_after_confirmation)
        self.assertIn("请确认 PPT 需求参数。", reply)
        self.assertIn("| 主题 | 英语单词 |", reply)
        session = await session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        structured = session.state[ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY]
        self.assertEqual(structured["reply_text"], reply)
        self.assertEqual(structured["final_file_paths"], [])

    async def test_run_agent_stops_after_design_question_form_request(self) -> None:
        session_service = InMemorySessionService()
        orchestrator = Orchestrator(
            session_service=session_service,
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        user_id = "user-design-form"
        session_id = "session-design-form"
        await session_service.create_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
            state={},
        )

        form_message = (
            "<cc-question-form>\n"
            '{"id":"design-brief","version":"design-brief-form-v1","title":"确认需求","questions":['
            '{"id":"goal","label":"目标","type":"short_text","required":false}'
            "]}\n"
            "</cc-question-form>"
        )
        tool_result = {
            "status": "needs_input",
            "message": form_message,
            "final_file_paths": [],
        }

        class _FakeRunner:
            def __init__(self) -> None:
                self.continued_after_question_form = False

            async def run_async(self, **_kwargs):
                event = Event(
                    author="CreativeClawOrchestrator",
                    content=Content(
                        role="user",
                        parts=[
                            Part.from_function_response(
                                name="run_design_product",
                                response=tool_result,
                            )
                        ],
                    ),
                )
                self.extracted_result = _extract_question_form_tool_result(event)
                yield event
                self.continued_after_question_form = True
                yield Event(
                    author="CreativeClawOrchestrator",
                    content=Content(role="model", parts=[Part(text="should not continue")]),
                )

        fake_runner = _FakeRunner()
        orchestrator.runner = fake_runner

        reply = await orchestrator.run_agent_and_log_events(
            user_id=user_id,
            session_id=session_id,
            new_message=Content(role="user", parts=[Part(text="设计一个餐厅 App")]),
        )

        self.assertFalse(fake_runner.continued_after_question_form)
        self.assertEqual(fake_runner.extracted_result, tool_result)
        self.assertEqual(_format_question_form_reply(tool_result), form_message)
        self.assertEqual(reply, form_message)
        session = await session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        structured = session.state[ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY]
        self.assertEqual(structured["reply_text"], form_message)
        self.assertEqual(structured["final_file_paths"], [])

    async def test_run_agent_streams_reply_text_deltas_with_adk_sse(self) -> None:
        session_service = InMemorySessionService()
        orchestrator = Orchestrator(
            session_service=session_service,
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        user_id = "user-stream"
        session_id = "session-stream"
        await session_service.create_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
            state={"turn_index": 3},
        )

        class _FakeRunner:
            def __init__(self) -> None:
                self.run_config = None

            async def run_async(self, **kwargs):
                self.run_config = kwargs["run_config"]
                yield Event(
                    author="CreativeClawOrchestrator",
                    partial=True,
                    content=Content(role="model", parts=[Part(text='{"reply_text":"Hel')]),
                )
                yield Event(
                    author="CreativeClawOrchestrator",
                    partial=True,
                    content=Content(role="model", parts=[Part(text='lo", "final_file_paths":[]}')]),
                )
                yield Event(
                    author="CreativeClawOrchestrator",
                    content=Content(role="model", parts=[Part(text='{"reply_text":"Hello","final_file_paths":[]}')]),
                )

        published = []

        async def _publisher(message):
            published.append(message)

        fake_runner = _FakeRunner()
        orchestrator.runner = fake_runner
        configure_step_event_publisher(_publisher)
        try:
            with route_context("web", "chat-stream"):
                await orchestrator.run_agent_and_log_events(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=Content(role="user", parts=[Part(text="hi")]),
                )
        finally:
            configure_step_event_publisher(None)

        self.assertIs(fake_runner.run_config.streaming_mode, StreamingMode.SSE)
        self.assertEqual([message.text for message in published], ["Hel", "lo"])
        self.assertEqual(published[0].metadata["display_style"], "assistant_delta")
        self.assertEqual(published[0].metadata["turn_index"], 3)

    async def test_design_brief_form_tool_call_streams_placeholder(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(
            state={"channel": "web", "turn_index": 5},
            session=SimpleNamespace(id="session-design-placeholder"),
        )

        async def _runner() -> dict[str, str]:
            return {"status": "needs_input", "message": "<cc-question-form>{}</cc-question-form>"}

        published = []

        async def _publisher(message):
            published.append(message)

        configure_step_event_publisher(_publisher)
        try:
            with route_context("web", "chat-design-placeholder"):
                result = await orchestrator._run_async_tool_with_events(
                    tool_context=tool_context,
                    tool_name="run_design_product",
                    stage="design_planning",
                    args={"task": "帮我设计一个中餐馆的手机app的UI"},
                    runner=_runner,
                )
        finally:
            configure_step_event_publisher(None)

        self.assertEqual(result["status"], "needs_input")
        self.assertTrue(orchestrator._last_run_streamed_reply_text)
        self.assertEqual([message.text for message in published], ["正在准备需求确认表单..."])
        self.assertEqual(published[0].metadata["display_style"], "assistant_delta")
        self.assertEqual(published[0].metadata["turn_index"], 5)

    async def test_run_until_done_uses_structured_final_response(self) -> None:
        session_service = InMemorySessionService()
        artifact_service = InMemoryArtifactService()
        orchestrator = Orchestrator(
            session_service=session_service,
            artifact_service=artifact_service,
            expert_agents={},
        )
        orchestrator.uid = "user-1"
        orchestrator.sid = "session-1"

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            file_path = Path(tmpdir) / "result.png"
            file_path.write_bytes(b"fake-image")
            file_record = build_workspace_file_record(
                file_path,
                description="generated image",
                source="image_generation",
            )
            relative_path = workspace_relative_path(file_path)

            await session_service.create_session(
                app_name=SYS_CONFIG.app_name,
                user_id=orchestrator.uid,
                session_id=orchestrator.sid,
                state={
                    "orchestration_events": [],
                    "generated": [file_record],
                    "generated_history": [],
                    "uploaded": [],
                    "uploaded_history": [],
                    "files_history": [[file_record]],
                    ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY: {
                        "reply_text": "Here is the final image.",
                        "final_file_paths": [relative_path],
                    },
                },
            )

            with patch.object(
                orchestrator,
                "run_agent_and_log_events",
                new=AsyncMock(return_value="raw final text"),
            ):
                result = await orchestrator.run_until_done()

        self.assertEqual(result["final_response"], "Here is the final image.")
        self.assertEqual(result["final_file_paths"], [relative_path])

        session = await session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=orchestrator.uid,
            session_id=orchestrator.sid,
        )
        self.assertEqual(session.state["final_response"], "Here is the final image.")
        self.assertEqual(session.state["final_file_paths"], [relative_path])

    async def test_run_until_done_falls_back_when_final_response_path_is_untracked(self) -> None:
        session_service = InMemorySessionService()
        artifact_service = InMemoryArtifactService()
        orchestrator = Orchestrator(
            session_service=session_service,
            artifact_service=artifact_service,
            expert_agents={},
        )
        orchestrator.uid = "user-1"
        orchestrator.sid = "session-1"

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            pptx_path = Path(tmpdir) / "deck.pptx"
            pptx_path.write_bytes(b"fake-pptx")
            file_record = build_workspace_file_record(
                pptx_path,
                description="generated pptx",
                source="ppt_product_manager",
            )
            relative_path = workspace_relative_path(pptx_path)

            await session_service.create_session(
                app_name=SYS_CONFIG.app_name,
                user_id=orchestrator.uid,
                session_id=orchestrator.sid,
                state={
                    "orchestration_events": [],
                    "generated": [file_record],
                    "generated_history": [],
                    "uploaded": [],
                    "uploaded_history": [],
                    "files_history": [[file_record]],
                    "final_file_paths": [relative_path],
                    ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY: {
                        "reply_text": "PPTX 已生成。",
                        "final_file_paths": [
                            "generated/ai_ppt_for_kids/ai_science_for_primary_students.pptx"
                        ],
                    },
                },
            )

            with patch.object(
                orchestrator,
                "run_agent_and_log_events",
                new=AsyncMock(return_value="raw final text"),
            ):
                result = await orchestrator.run_until_done()

        self.assertEqual(result["final_response"], "PPTX 已生成。")
        self.assertEqual(result["final_file_paths"], [relative_path])

        session = await session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=orchestrator.uid,
            session_id=orchestrator.sid,
        )
        self.assertEqual(session.state["final_file_paths"], [relative_path])

    async def test_run_until_done_requires_structured_final_response(self) -> None:
        session_service = InMemorySessionService()
        artifact_service = InMemoryArtifactService()
        orchestrator = Orchestrator(
            session_service=session_service,
            artifact_service=artifact_service,
            expert_agents={},
        )
        orchestrator.uid = "user-1"
        orchestrator.sid = "session-1"

        await session_service.create_session(
            app_name=SYS_CONFIG.app_name,
            user_id=orchestrator.uid,
            session_id=orchestrator.sid,
            state={"orchestration_events": []},
        )

        with patch.object(
            orchestrator,
            "run_agent_and_log_events",
            new=AsyncMock(return_value="fallback final text"),
        ):
            with self.assertRaisesRegex(ValueError, "Missing structured final response"):
                await orchestrator.run_until_done()


class OrchestratorInvokeAgentIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_invoke_agent_runs_real_grounding_expert_through_dispatcher(self) -> None:
        expert_origin_path = Path(__file__).resolve().parents[1] / "src" / "agents"
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={
                "ImageGroundingAgent": annotate_agent_origin(
                    ImageGroundingAgent(name="ImageGroundingAgent"),
                    app_name=SYS_CONFIG.app_name,
                    origin_path=expert_origin_path,
                )
            },
        )
        tool_context = SimpleNamespace(
            state=State(
                {
                    "step": 0,
                    "files_history": [],
                    "summary_history": [],
                    "text_history": [],
                    "message_history": [],
                    "expert_history": [],
                },
                {},
            ),
            _invocation_context=SimpleNamespace(user_id="user-1"),
        )
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            image_path = Path(tmpdir) / "grounding_input.png"
            image_path.write_bytes(b"fake-image")
            relative_image_path = workspace_relative_path(image_path)

            with patch(
                "src.agents.experts.image_grounding.image_grounding_agent.dino_xseek_detection_tool",
                new=AsyncMock(
                    return_value={
                        "status": "success",
                        "message": "Detected 1 object.",
                        "input_path": relative_image_path,
                        "prompt": "cat",
                        "objects": [{"bbox": [1.0, 2.0, 3.0, 4.0]}],
                        "bboxes": [[1.0, 2.0, 3.0, 4.0]],
                        "task_uuid": "task-1",
                        "session_id": "child-session",
                        "provider": "deepdataspace",
                        "model_name": "DINO-XSeek-1.0",
                    }
                ),
            ):
                result = await orchestrator.invoke_agent(
                    "ImageGroundingAgent",
                    f'{{"input_path":"{relative_image_path}","prompt":"cat"}}',
                    tool_context=tool_context,
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["agent_name"], "ImageGroundingAgent")
        self.assertIn("image_ground_results", result["structured_data"])
        self.assertEqual(
            result["structured_data"]["image_ground_results"][0]["bboxes"][0],
            [1.0, 2.0, 3.0, 4.0],
        )
        self.assertEqual(tool_context.state["step"], 1)
        self.assertEqual(tool_context.state["current_output"]["status"], "success")
        self.assertEqual(tool_context.state["expert_history"][-1]["agent_name"], "ImageGroundingAgent")
        self.assertEqual(tool_context.state["orchestration_events"][0]["title"], "invoke_agent")
        self.assertIn("agent_name=ImageGroundingAgent", tool_context.state["orchestration_events"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
