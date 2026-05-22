import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from google.adk.events import Event, EventActions
from google.adk.runners import Runner

from conf.system import SYS_CONFIG
from src.productions.design.design_product_manager.brief_form import (
    DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY,
    DESIGN_BRIEF_FORM_SCHEMA_VERSION,
    DESIGN_BRIEF_FORM_STATE_KEY,
)
from src.runtime.models import InboundMessage, MessageAttachment
from src.runtime.workflow_service import CreativeClawRuntime
from src.runtime.workspace import build_workspace_file_record, workspace_relative_path, workspace_root


class RuntimeSessionTests(unittest.IsolatedAsyncioTestCase):
    def test_runtime_registers_image_understanding_expert(self) -> None:
        runtime = CreativeClawRuntime()
        image_understanding_agent = runtime.expert_agents["ImageUnderstandingAgent"]

        self.assertIn("ImageUnderstandingAgent", runtime.expert_agents)
        self.assertEqual(
            getattr(image_understanding_agent, "_adk_origin_app_name", None),
            SYS_CONFIG.app_name,
        )
        self.assertIsNotNone(getattr(image_understanding_agent, "_adk_origin_path", None))

    def test_runtime_registers_image_segmentation_expert(self) -> None:
        runtime = CreativeClawRuntime()
        segmentation_agent = runtime.expert_agents["ImageSegmentationAgent"]

        self.assertIn("ImageSegmentationAgent", runtime.expert_agents)
        self.assertEqual(
            getattr(segmentation_agent, "_adk_origin_app_name", None),
            SYS_CONFIG.app_name,
        )
        self.assertIsNotNone(getattr(segmentation_agent, "_adk_origin_path", None))

    def test_runtime_registers_video_generation_expert(self) -> None:
        runtime = CreativeClawRuntime()
        video_agent = runtime.expert_agents["VideoGenerationAgent"]

        self.assertIn("VideoGenerationAgent", runtime.expert_agents)
        self.assertEqual(
            getattr(video_agent, "_adk_origin_app_name", None),
            SYS_CONFIG.app_name,
        )
        self.assertIsNotNone(getattr(video_agent, "_adk_origin_path", None))

    def test_runtime_registers_3d_generation_expert(self) -> None:
        runtime = CreativeClawRuntime()
        three_d_agent = runtime.expert_agents["3DGeneration"]

        self.assertIn("3DGeneration", runtime.expert_agents)
        self.assertEqual(
            getattr(three_d_agent, "_adk_origin_app_name", None),
            SYS_CONFIG.app_name,
        )
        self.assertIsNotNone(getattr(three_d_agent, "_adk_origin_path", None))

    def test_runtime_registers_new_understanding_and_transform_experts(self) -> None:
        runtime = CreativeClawRuntime()

        for expert_name in (
            "TextTransformExpert",
            "VideoUnderstandingExpert",
            "SpeechRecognitionExpert",
            "SpeechSynthesisExpert",
            "MusicGenerationExpert",
        ):
            self.assertIn(expert_name, runtime.expert_agents)
            self.assertEqual(
                getattr(runtime.expert_agents[expert_name], "_adk_origin_app_name", None),
                SYS_CONFIG.app_name,
            )
            self.assertIsNotNone(getattr(runtime.expert_agents[expert_name], "_adk_origin_path", None))

    def test_runtime_expert_metadata_keeps_runner_app_alignment_clean(self) -> None:
        runtime = CreativeClawRuntime()

        runner = Runner(
            agent=runtime.expert_agents["KnowledgeAgent"],
            app_name=SYS_CONFIG.app_name,
            session_service=runtime.session_service,
            artifact_service=runtime.artifact_service,
        )

        self.assertIsNone(runner._app_name_alignment_hint)

    async def test_ensure_session_reuses_same_channel_chat_pair(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="hello",
        )

        user_id_1, session_id_1 = await runtime._ensure_session(inbound)
        user_id_2, session_id_2 = await runtime._ensure_session(inbound)

        self.assertEqual(user_id_1, user_id_2)
        self.assertEqual(session_id_1, session_id_2)

    async def test_reset_session_creates_new_session_for_same_channel_chat_pair(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="hello",
        )

        _user_id_1, session_id_1 = await runtime._ensure_session(inbound)
        _user_id_2, session_id_2 = await runtime.reset_session(inbound)

        self.assertNotEqual(session_id_1, session_id_2)

        _user_id_3, session_id_3 = await runtime._ensure_session(inbound)
        self.assertEqual(session_id_2, session_id_3)

    async def test_help_command_returns_help_text_without_creating_session(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="/help",
        )

        events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "final")
        self.assertIn("/new", events[0].text)
        self.assertIn("/help", events[0].text)
        self.assertEqual(runtime._session_keys, {})

    async def test_initial_state_uses_runtime_fields(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="hello",
        )

        user_id, session_id = await runtime._ensure_session(inbound)
        await runtime._set_initial_state(user_id, session_id, inbound)
        session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )

        self.assertEqual(session.state["workflow_status"], "running")
        self.assertEqual(session.state["final_summary"], "")
        self.assertEqual(session.state["final_response"], "")
        self.assertEqual(session.state["channel"], "cli")
        self.assertEqual(session.state["chat_id"], "terminal")
        self.assertEqual(session.state["sender_id"], "cli-user")
        self.assertEqual(session.state["product_line"], "")
        self.assertEqual(session.state["product_line_options"], {})
        self.assertEqual(session.state["current_parameters"], {})
        self.assertIsNone(session.state["current_output"])
        self.assertIsNone(session.state["last_expert_result"])
        self.assertEqual(session.state["expert_history"], [])
        self.assertEqual(session.state["input_files"], [])
        self.assertEqual(session.state["new_files"], [])
        self.assertEqual(session.state["final_file_paths"], [])

    async def test_initial_state_persists_design_product_metadata(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="design",
            text="做一个 dashboard",
            metadata={
                "product_line": "design",
                "design": {
                    "scenario": "dashboard",
                    "allow_assumptions": False,
                },
            },
        )

        user_id, session_id = await runtime._ensure_session(inbound)
        await runtime._set_initial_state(user_id, session_id, inbound)
        session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )

        self.assertEqual(session.state["product_line"], "design")
        self.assertEqual(session.state["product_line_options"]["design"]["scenario"], "dashboard")
        self.assertFalse(session.state["product_line_options"]["design"]["allow_assumptions"])

    async def test_initial_state_preserves_pending_ppt_workflow_across_confirmation_turn(self) -> None:
        runtime = CreativeClawRuntime()
        user_id, session_id = await runtime._ensure_session(
            InboundMessage(
                channel="web",
                sender_id="web-client",
                chat_id="ppt-chat",
                text="给我做一个ppt，用来给幼儿园小朋友讲英语单词。3页，分别讲 猫、狗、鸭子。",
            )
        )
        session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        await runtime.session_service.append_event(
            session,
            Event(
                author="unit_test",
                actions=EventActions(
                    state_delta={
                        "product_line": "ppt",
                        "turn_index": 1,
                        "ppt_workflow_state": {
                            "workflow_id": "ppt-workflow-test",
                            "stage": "awaiting_requirement_confirmation",
                            "confirmed_requirement": {"topic": "英语单词"},
                        },
                        "ppt_confirmed_requirement": {"topic": "英语单词"},
                        "ppt_product_result": {"status": "awaiting_requirement_confirmation"},
                        "last_product_result": {"status": "awaiting_requirement_confirmation"},
                        "current_output": {"status": "awaiting_requirement_confirmation"},
                    }
                ),
            ),
        )
        inbound = InboundMessage(
            channel="web",
            sender_id="web-client",
            chat_id="ppt-chat",
            text="确认",
        )

        await runtime._set_initial_state(user_id, session_id, inbound)
        updated_session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )

        self.assertEqual(updated_session.state["turn_index"], 2)
        self.assertEqual(updated_session.state["user_prompt"], "确认")
        self.assertEqual(updated_session.state["product_line"], "ppt")
        self.assertEqual(
            updated_session.state["ppt_workflow_state"]["stage"],
            "awaiting_requirement_confirmation",
        )
        self.assertEqual(updated_session.state["ppt_confirmed_requirement"]["topic"], "英语单词")
        self.assertEqual(
            updated_session.state["ppt_product_result"]["status"],
            "awaiting_requirement_confirmation",
        )
        self.assertIsNone(updated_session.state["current_output"])

    async def test_initial_state_preserves_pending_design_brief_form_across_answer_turn(self) -> None:
        runtime = CreativeClawRuntime()
        user_id, session_id = await runtime._ensure_session(
            InboundMessage(
                channel="web",
                sender_id="web-client",
                chat_id="design-chat",
                text="帮我做一个股票新闻 App 的移动端 UI 设计。",
            )
        )
        session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        await runtime.session_service.append_event(
            session,
            Event(
                author="unit_test",
                actions=EventActions(
                    state_delta={
                        "turn_index": 1,
                        DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY: "帮我做一个股票新闻 App 的移动端 UI 设计。",
                        DESIGN_BRIEF_FORM_STATE_KEY: {
                            "schema_version": DESIGN_BRIEF_FORM_SCHEMA_VERSION,
                            "message": "<cc-question-form>{}</cc-question-form>",
                        },
                        "design_product_result": {"status": "needs_input"},
                        "last_product_result": {"status": "needs_input"},
                        "current_output": {"status": "needs_input"},
                    }
                ),
            ),
        )
        answer_block = (
            '[cc-form-answers id="design-brief" version="design-brief-form-v1"]\n'
            '{"visual_direction":"decide_for_me"}\n'
            "[/cc-form-answers]"
        )
        inbound = InboundMessage(
            channel="web",
            sender_id="web-client",
            chat_id="design-chat",
            text=answer_block,
        )

        await runtime._set_initial_state(user_id, session_id, inbound)
        updated_session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )

        self.assertEqual(updated_session.state["turn_index"], 2)
        self.assertEqual(updated_session.state["user_prompt"], answer_block)
        self.assertEqual(updated_session.state["product_line"], "design")
        self.assertEqual(
            updated_session.state[DESIGN_BRIEF_FORM_PENDING_TASK_STATE_KEY],
            "帮我做一个股票新闻 App 的移动端 UI 设计。",
        )
        self.assertEqual(
            updated_session.state[DESIGN_BRIEF_FORM_STATE_KEY]["schema_version"],
            DESIGN_BRIEF_FORM_SCHEMA_VERSION,
        )
        self.assertIsNone(updated_session.state["current_output"])

    async def test_initial_state_does_not_preserve_completed_ppt_workflow(self) -> None:
        runtime = CreativeClawRuntime()
        user_id, session_id = await runtime._ensure_session(
            InboundMessage(
                channel="web",
                sender_id="web-client",
                chat_id="ppt-chat",
                text="做一个ppt",
            )
        )
        session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        await runtime.session_service.append_event(
            session,
            Event(
                author="unit_test",
                actions=EventActions(
                    state_delta={
                        "product_line": "ppt",
                        "turn_index": 1,
                        "ppt_workflow_state": {"stage": "completed"},
                        "ppt_product_result": {"status": "success"},
                    }
                ),
            ),
        )
        inbound = InboundMessage(
            channel="web",
            sender_id="web-client",
            chat_id="ppt-chat",
            text="帮我生成一张图片",
        )

        await runtime._set_initial_state(user_id, session_id, inbound)
        updated_session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )

        self.assertEqual(updated_session.state["product_line"], "")
        self.assertIsNone(updated_session.state["ppt_workflow_state"])
        self.assertIsNone(updated_session.state["ppt_product_result"])

    async def test_initial_state_persists_uploaded_files_in_history(self) -> None:
        runtime = CreativeClawRuntime()
        with tempfile.TemporaryDirectory() as tmpdir:
            upload_path = Path(tmpdir) / "demo.png"
            upload_path.write_bytes(b"fake-image")
            inbound = InboundMessage(
                channel="cli",
                sender_id="cli-user",
                chat_id="terminal",
                text="describe this image",
                attachments=[
                    MessageAttachment(
                        path=str(upload_path),
                        name="demo.png",
                        mime_type="image/png",
                        description="uploaded test image",
                    )
                ],
            )

            user_id, session_id = await runtime._ensure_session(inbound)
            await runtime._set_initial_state(user_id, session_id, inbound)
            session = await runtime.session_service.get_session(
                app_name=SYS_CONFIG.app_name,
                user_id=user_id,
                session_id=session_id,
            )

        self.assertEqual(len(session.state["input_files"]), 1)
        self.assertEqual(len(session.state["files_history"]), 1)
        self.assertEqual(session.state["files_history"][0][0]["source"], "channel")
        self.assertTrue(session.state["input_files"][0]["path"].startswith("inbox/cli/"))

    async def test_run_message_uses_natural_progress_messages(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="Generate an image for me",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_until_done(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "Image generation is complete.",
                    "final_response": "The image is ready.",
                    "last_output_message": "The image is ready.",
                    "new_orchestration_events": [],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(events[0].event_type, "status")
        self.assertEqual(events[0].text, "The system is getting ready to work on your request.")
        self.assertEqual(events[0].metadata["stage_title"], "Preparing your request")
        self.assertEqual(events[0].metadata["debug_detail"], "Workflow started.")
        self.assertEqual(events[-1].event_type, "final")
        self.assertEqual(events[-1].text, "The image is ready.")
        self.assertNotIn("Image generation is complete.", events[-1].text)

    async def test_run_message_reports_submitted_design_form_progress(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="web",
            sender_id="web-user",
            chat_id="web-chat",
            text=(
                '[cc-form-answers id="design-brief" version="design-brief-form-v1"]\n'
                '{"visual_direction":"decide_for_me"}\n'
                "[/cc-form-answers]"
            ),
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_until_done(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "Done.",
                    "final_response": "Done.",
                    "last_output_message": "Done.",
                    "new_orchestration_events": [],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        progress_events = [event for event in events if event.event_type == "status"]
        self.assertEqual(progress_events[1].text, "已收到需求确认表单，正在继续生成设计方案。")
        self.assertEqual(progress_events[1].metadata["stage"], "design_planning")
        self.assertEqual(progress_events[1].metadata["stage_title"], "Reviewing your answers")
        self.assertEqual(progress_events[1].metadata["user_detail"], "已收到需求确认表单，正在继续生成设计方案。")

    async def test_run_message_with_design_metadata_uses_orchestrator(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="design",
            text="做一个运营数据 dashboard",
            metadata={
                "product_line": "design",
                "design": {
                    "scenario": "dashboard",
                    "allow_assumptions": False,
                },
            },
        )

        class _FakeOrchestrator:
            constructed = False

            def __init__(self, **_kwargs) -> None:
                type(self).constructed = True
                self.uid = ""
                self.sid = ""

            async def run_until_done(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "Design request routed through Orchestrator.",
                    "final_response": "Design request routed through Orchestrator.",
                    "last_output_message": "",
                    "assistant_text_streamed": True,
                    "new_orchestration_events": [],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        self.assertTrue(_FakeOrchestrator.constructed)
        self.assertEqual(events[0].event_type, "status")
        self.assertEqual(events[-1].event_type, "final")
        self.assertEqual(events[-1].text, "Design request routed through Orchestrator.")
        self.assertTrue(events[-1].metadata["disable_stream"])

        session_id = events[-1].metadata["session_id"]
        session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id="cli-user",
            session_id=session_id,
        )
        self.assertEqual(session.state["product_line"], "design")
        self.assertEqual(session.state["product_line_options"]["design"]["scenario"], "dashboard")
        self.assertFalse(session.state["product_line_options"]["design"]["allow_assumptions"])

    async def test_run_message_with_design_prompt_without_metadata_uses_orchestrator(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="设计一个运营数据 dashboard，展示 DAU、留存和渠道 ROI",
        )

        class _FakeOrchestrator:
            constructed = False

            def __init__(self, **_kwargs) -> None:
                type(self).constructed = True
                self.uid = ""
                self.sid = ""

            async def run_until_done(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "Design request routed through Orchestrator.",
                    "final_response": "Design request routed through Orchestrator.",
                    "last_output_message": "",
                    "new_orchestration_events": [],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        self.assertTrue(_FakeOrchestrator.constructed)
        self.assertEqual(events[-1].event_type, "final")
        self.assertEqual(events[-1].text, "Design request routed through Orchestrator.")

        session_id = events[-1].metadata["session_id"]
        session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id="cli-user",
            session_id=session_id,
        )
        self.assertEqual(session.state["product_line"], "")
        self.assertEqual(session.state["product_line_options"], {})
        self.assertIn("运营数据 dashboard", session.state["user_prompt"])

    async def test_run_message_scopes_progress_metadata_by_turn_index(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="feishu",
            sender_id="ou_1",
            chat_id="oc_1",
            text="Generate something",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_until_done(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "Done.",
                    "final_response": "Done.",
                    "last_output_message": "",
                    "new_orchestration_events": [],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            first_events = [event async for event in runtime.run_message(inbound)]
            second_events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(first_events[0].metadata["turn_index"], 1)
        self.assertEqual(first_events[-1].metadata["turn_index"], 1)
        self.assertEqual(second_events[0].metadata["turn_index"], 2)
        self.assertEqual(second_events[-1].metadata["turn_index"], 2)

    async def test_run_message_emits_granular_orchestration_events(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="Analyze this directory",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_until_done(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "The analysis is ready.",
                    "last_output_message": "internal-output",
                    "final_response": "The analysis is ready.",
                    "new_orchestration_events": [
                        {
                            "title": "List Skills",
                            "detail": "Checking the currently available skills.",
                            "stage": "planning",
                        },
                        {
                            "title": "invoke_agent",
                            "detail": "Status: success\nArgs: agent_name=KnowledgeAgent; prompt={\"prompt\":\"analyze\"}\nResult: KnowledgeAgent finished with status=success; message=done",
                            "stage": "expert_execution",
                        },
                    ],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        progress_events = [event for event in events if event.event_type == "status"]
        self.assertEqual(progress_events[1].metadata["stage_title"], "Checking capabilities")
        self.assertEqual(progress_events[1].metadata["debug_title"], "List Skills")
        self.assertEqual(progress_events[1].metadata["stage"], "planning")
        self.assertEqual(progress_events[1].metadata["turn_index"], 1)
        self.assertEqual(progress_events[1].text, "The system is checking available capabilities.")
        self.assertIn("Checking the currently available skills.", progress_events[1].metadata["debug_detail"])
        self.assertEqual(progress_events[2].metadata["stage_title"], "Generating content")
        self.assertEqual(progress_events[2].metadata["debug_title"], "invoke_agent")
        self.assertEqual(progress_events[2].metadata["stage"], "expert_execution")
        self.assertEqual(progress_events[2].metadata["turn_index"], 1)
        self.assertEqual(progress_events[2].text, "The system is using a specialist capability.")
        self.assertIn("agent_name=KnowledgeAgent", progress_events[2].metadata["debug_detail"])

    async def test_run_message_renders_tool_args_and_result_summary(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="Check this file",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_until_done(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "Done.",
                    "final_response": "Done.",
                    "last_output_message": "",
                    "new_orchestration_events": [
                        {
                            "title": "read_file",
                            "detail": "Status: started\nArgs: path=README.md",
                            "stage": "inspection",
                        },
                        {
                            "title": "read_file",
                            "detail": "Status: success\nArgs: path=README.md\nResult: Hello world",
                            "stage": "inspection",
                        },
                    ],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        progress_events = [event for event in events if event.event_type == "status"]
        self.assertEqual(progress_events[-1].text, "The system is reading relevant workspace content.")
        self.assertNotIn("README.md", progress_events[-1].text)
        self.assertIn("Args: path=README.md", progress_events[-1].metadata["debug_detail"])
        self.assertIn("Result: Hello world", progress_events[-1].metadata["debug_detail"])

    async def test_run_message_keeps_smart_tool_summary_in_timeline(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="List this directory",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_until_done(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "Done.",
                    "final_response": "Done.",
                    "last_output_message": "",
                    "new_orchestration_events": [
                        {
                            "title": "list_dir",
                            "detail": "Status: started\nArgs: path=.",
                            "stage": "inspection",
                        },
                        {
                            "title": "list_dir",
                            "detail": "Status: success\nArgs: path=.\nResult: 3 entries. Preview: [D] src; [F] README.md; [F] pyproject.toml",
                            "stage": "inspection",
                        },
                    ],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        progress_events = [event for event in events if event.event_type == "status"]
        self.assertEqual(progress_events[-1].text, "The system is reviewing relevant workspace files.")
        self.assertNotIn("README.md", progress_events[-1].text)
        self.assertIn("3 entries", progress_events[-1].metadata["debug_detail"])
        self.assertIn("README.md", progress_events[-1].metadata["debug_detail"])

    async def test_build_final_event_prefers_state_final_response_over_text_history(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="hello",
        )

        user_id, session_id = await runtime._ensure_session(inbound)
        session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        await runtime.session_service.append_event(
            session,
            Event(
                author="unit_test",
                actions=EventActions(
                    state_delta={
                        "files_history": [],
                        "text_history": ["This is a long expert output."],
                        "summary_history": [],
                        "final_summary": "Internal completion summary.",
                        "final_response": "This is the final reply shown to the user.",
                    }
                ),
            ),
        )

        final_event = await runtime._build_final_event(
            user_id=user_id,
            session_id=session_id,
            final_summary="fallback reply",
        )

        self.assertEqual(final_event.event_type, "final")
        self.assertEqual(final_event.text, "This is the final reply shown to the user.")

    async def test_build_final_event_prefers_explicit_final_file_paths_over_latest_outputs(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="send this exact file",
        )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            selected_file = Path(tmpdir) / "selected.png"
            fallback_file = Path(tmpdir) / "fallback.png"
            selected_file.write_bytes(b"selected")
            fallback_file.write_bytes(b"fallback")

            selected_relative = workspace_relative_path(selected_file)
            fallback_record = build_workspace_file_record(
                fallback_file,
                description="fallback file",
                source="image_generation",
            )

            user_id, session_id = await runtime._ensure_session(inbound)
            session = await runtime.session_service.get_session(
                app_name=SYS_CONFIG.app_name,
                user_id=user_id,
                session_id=session_id,
            )
            await runtime.session_service.append_event(
                session,
                Event(
                    author="unit_test",
                    actions=EventActions(
                        state_delta={
                            "files_history": [[fallback_record]],
                            "final_file_paths": [selected_relative],
                            "final_response": "Sent the selected file.",
                        }
                    ),
                ),
            )

            final_event = await runtime._build_final_event(
                user_id=user_id,
                session_id=session_id,
                final_summary="fallback reply",
            )

        self.assertEqual(final_event.artifact_paths, [str(selected_file.resolve())])
        self.assertEqual(final_event.text, "Sent the selected file.")

    async def test_build_final_event_does_not_replay_latest_generated_outputs_when_selection_is_unset(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="which model generated that video",
        )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            generated_file = Path(tmpdir) / "latest.mp4"
            generated_file.write_bytes(b"video")
            generated_record = build_workspace_file_record(
                generated_file,
                description="generated video",
                source="video_generation",
            )

            user_id, session_id = await runtime._ensure_session(inbound)
            session = await runtime.session_service.get_session(
                app_name=SYS_CONFIG.app_name,
                user_id=user_id,
                session_id=session_id,
            )
            await runtime.session_service.append_event(
                session,
                Event(
                    author="unit_test",
                    actions=EventActions(
                        state_delta={
                            "files_history": [[generated_record]],
                            "final_file_paths": [],
                            "final_response": "It used the Veo provider.",
                        }
                    ),
                ),
            )

            final_event = await runtime._build_final_event(
                user_id=user_id,
                session_id=session_id,
                final_summary="fallback reply",
            )

        self.assertEqual(final_event.artifact_paths, [])
        self.assertEqual(final_event.text, "It used the Veo provider.")

    async def test_build_final_event_respects_explicit_empty_final_file_selection(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="reply without attachments",
        )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            fallback_file = Path(tmpdir) / "fallback.png"
            fallback_file.write_bytes(b"fallback")
            fallback_record = build_workspace_file_record(
                fallback_file,
                description="fallback file",
                source="image_generation",
            )

            user_id, session_id = await runtime._ensure_session(inbound)
            session = await runtime.session_service.get_session(
                app_name=SYS_CONFIG.app_name,
                user_id=user_id,
                session_id=session_id,
            )
            await runtime.session_service.append_event(
                session,
                Event(
                    author="unit_test",
                    actions=EventActions(
                        state_delta={
                            "files_history": [[fallback_record]],
                            "final_file_paths": [],
                            "final_response": "Return text only.",
                        }
                    ),
                ),
            )

            final_event = await runtime._build_final_event(
                user_id=user_id,
                session_id=session_id,
                final_summary="fallback reply",
            )

        self.assertEqual(final_event.artifact_paths, [])
        self.assertEqual(final_event.text, "Return text only.")

    async def test_run_message_surfaces_orchestrator_failure(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="Describe this image",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_until_done(self) -> dict:
                raise KeyError("error")

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(events[-1].event_type, "error")
        self.assertIn("Workflow failed", events[-1].text)
        self.assertIn("session_id=", events[-1].text)
        self.assertIn("KeyError: 'error'", events[-1].text)

    async def test_run_message_does_not_resend_channel_only_upload_as_final_artifact(self) -> None:
        runtime = CreativeClawRuntime()
        with tempfile.TemporaryDirectory() as tmpdir:
            upload_path = Path(tmpdir) / "demo.png"
            upload_path.write_bytes(b"fake-image")
            inbound = InboundMessage(
                channel="cli",
                sender_id="cli-user",
                chat_id="terminal",
                text="Describe this image",
                attachments=[MessageAttachment(path=str(upload_path), name="demo.png", mime_type="image/png")],
            )

            class _FakeOrchestrator:
                def __init__(self, **_kwargs) -> None:
                    self.uid = ""
                    self.sid = ""

                async def run_until_done(self) -> dict:
                    return {
                        "workflow_status": "finished",
                        "final_summary": "Image description completed.",
                        "final_response": "Image description completed.",
                        "last_output_message": "Image description completed.",
                        "new_orchestration_events": [],
                    }

            with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
                events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(events[-1].event_type, "final")
        self.assertEqual(events[-1].artifact_paths, [])

    async def test_run_message_follow_up_does_not_replay_previous_generated_video(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="which model generated that video",
        )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            generated_file = Path(tmpdir) / "generated_video.mp4"
            generated_file.write_bytes(b"video")
            generated_record = build_workspace_file_record(
                generated_file,
                description="previous generated video",
                source="video_generation",
            )

            user_id, session_id = await runtime._ensure_session(inbound)
            session = await runtime.session_service.get_session(
                app_name=SYS_CONFIG.app_name,
                user_id=user_id,
                session_id=session_id,
            )
            await runtime.session_service.append_event(
                session,
                Event(
                    author="unit_test",
                    actions=EventActions(
                        state_delta={
                            "files_history": [[generated_record]],
                            "summary_history": [],
                            "message_history": [],
                            "text_history": [],
                            "final_file_paths": [],
                        }
                    ),
                ),
            )

            class _FakeOrchestrator:
                def __init__(self, **_kwargs) -> None:
                    self.uid = ""
                    self.sid = ""

                async def run_until_done(self) -> dict:
                    return {
                        "workflow_status": "finished",
                        "final_summary": "It was generated with Veo.",
                        "final_response": "It was generated with Veo.",
                        "last_output_message": "It was generated with Veo.",
                        "new_orchestration_events": [],
                    }

            with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
                events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(events[-1].event_type, "final")
        self.assertEqual(events[-1].text, "It was generated with Veo.")
        self.assertEqual(events[-1].artifact_paths, [])


if __name__ == "__main__":
    unittest.main()
