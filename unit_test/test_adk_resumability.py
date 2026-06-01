import unittest
import warnings
from typing import Any, AsyncGenerator

from google.adk import Context, Workflow
from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, RequestInput
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.adk.workflow import node
from google.genai.types import Content, FunctionCall, FunctionResponse, Part
from pydantic import PrivateAttr


ADK_REQUEST_INPUT_FUNCTION_NAME = "adk_request_input"
ADK_REQUEST_CONFIRMATION_FUNCTION_NAME = "adk_request_confirmation"


class _ToolCallingFakeLlm(BaseLlm):
    """Fake model that calls one tool, then returns final text."""

    _function_call: FunctionCall = PrivateAttr()
    _requests: list[LlmRequest] = PrivateAttr(default_factory=list)

    def __init__(self, *, function_call: FunctionCall) -> None:
        super().__init__(model="fake-tool-calling-model")
        self._function_call = function_call

    @property
    def requests(self) -> list[LlmRequest]:
        """Return captured ADK model requests."""
        return self._requests

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        self._requests.append(llm_request)
        if len(self._requests) == 1:
            yield LlmResponse(content=Content(role="model", parts=[Part(function_call=self._function_call)]))
            return
        yield LlmResponse(content=Content(role="model", parts=[Part(text="done")]))


def _build_resumable_app(*, name: str, workflow: Workflow) -> App:
    """Build an ADK app with resumability enabled while keeping tests quiet."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"\[EXPERIMENTAL\] ResumabilityConfig.*",
            category=UserWarning,
        )
        return App(
            name=name,
            root_agent=workflow,
            resumability_config=ResumabilityConfig(is_resumable=True),
        )


def _build_resumable_tool_app(*, name: str, agent: LlmAgent) -> App:
    """Build a resumable ADK app for LlmAgent tool-call characterization."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"\[EXPERIMENTAL\].*",
            category=UserWarning,
        )
        return App(
            name=name,
            root_agent=agent,
            resumability_config=ResumabilityConfig(is_resumable=True),
        )


async def _collect_events(events: AsyncGenerator[Event, None]) -> list[Event]:
    """Drain an ADK event stream into a list."""
    return [event async for event in events]


class AdkResumabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_input_function_response_resumes_same_workflow_invocation(self) -> None:
        """Characterize the ADK-native HITL bridge Creative Claw can build on."""

        @node(name="ReviewConfirmationNode", rerun_on_resume=True)
        def review_confirmation(ctx: Context, node_input: str):
            resume_value = ctx.resume_inputs.get("ppt_confirmation")
            run_count = int(ctx.state.get("confirmation_node_runs", 0) or 0) + 1
            if resume_value is None:
                yield Event(
                    state={
                        "confirmation_node_runs": run_count,
                        "pending_task": node_input,
                    }
                )
                yield RequestInput(
                    interrupt_id="ppt_confirmation",
                    message="Confirm the PPT requirement.",
                )
                return

            yield Event(
                state={
                    "confirmation_node_runs": run_count,
                    "confirmation_response": resume_value,
                }
            )

        workflow = Workflow(
            name="RequestInputResumeWorkflow",
            edges=[("START", review_confirmation)],
        )
        app = _build_resumable_app(name="RequestInputResumeApp", workflow=workflow)
        session_service = InMemorySessionService()
        runner = Runner(
            app=app,
            session_service=session_service,
            artifact_service=InMemoryArtifactService(),
        )
        user_id = "user-request-input-resume"
        session_id = "session-request-input-resume"
        try:
            await session_service.create_session(app_name=app.name, user_id=user_id, session_id=session_id, state={})

            first_events = await _collect_events(
                runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=Content(role="user", parts=[Part(text="Make a three-page PPT.")]),
                )
            )
            first_invocation_id = first_events[0].invocation_id
            request_events = [event for event in first_events if event.long_running_tool_ids]
            self.assertEqual(len(request_events), 1)
            request_call = request_events[0].get_function_calls()[0]
            self.assertEqual(request_call.name, ADK_REQUEST_INPUT_FUNCTION_NAME)
            self.assertEqual(request_call.id, "ppt_confirmation")

            resume_message = Content(
                role="user",
                parts=[
                    Part(
                        function_response=FunctionResponse(
                            id=request_call.id,
                            name=ADK_REQUEST_INPUT_FUNCTION_NAME,
                            response={"result": "确认"},
                        )
                    )
                ],
            )
            second_events = await _collect_events(
                runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    invocation_id=first_invocation_id,
                    new_message=resume_message,
                )
            )

            self.assertTrue(second_events)
            self.assertTrue(all(event.invocation_id == first_invocation_id for event in second_events))
            self.assertFalse(any(event.long_running_tool_ids for event in second_events))

            session = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session_id)
            self.assertEqual(session.state["pending_task"], "Make a three-page PPT.")
            self.assertEqual(session.state["confirmation_response"], "确认")
            self.assertEqual(session.state["confirmation_node_runs"], 2)
        finally:
            await runner.close()

    async def test_request_input_inside_llm_tool_does_not_pause_tool_boundary(self) -> None:
        """Guard against hiding PPT HITL behind a child RequestInput workflow."""

        @node(name="NestedToolRequestInputNode", rerun_on_resume=True)
        def nested_confirmation(ctx: Context, node_input: dict[str, Any]):
            resume_value = ctx.resume_inputs.get("nested_tool_confirmation")
            run_count = int(ctx.state.get("nested_tool_request_input_runs", 0) or 0) + 1
            if resume_value is None:
                yield Event(
                    state={
                        "nested_tool_request_input_runs": run_count,
                        "nested_tool_task": node_input.get("task"),
                    }
                )
                yield RequestInput(
                    interrupt_id="nested_tool_confirmation",
                    message="Confirm the nested tool workflow.",
                )
                return

            yield Event(
                state={
                    "nested_tool_request_input_runs": run_count,
                    "nested_tool_resume_value": resume_value,
                }
            )
            return {"status": "continued", "response": resume_value}

        nested_workflow = Workflow(
            name="NestedToolRequestInputWorkflow",
            edges=[("START", nested_confirmation)],
        )

        async def run_nested_confirmation(task: str, tool_context: ToolContext) -> dict[str, Any]:
            result = await tool_context.run_node(
                nested_workflow,
                node_input={"task": task},
                use_sub_branch=True,
                raise_on_wait=True,
            )
            tool_context.state["nested_tool_result"] = result
            return {"status": "done", "result": result}

        fake_llm = _ToolCallingFakeLlm(
            function_call=FunctionCall(
                name="run_nested_confirmation",
                id="run_nested_confirmation_call",
                args={"task": "Make a PPT."},
            )
        )
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\].*", category=UserWarning)
            agent = LlmAgent(
                name="NestedRequestInputToolRoot",
                model=fake_llm,
                instruction="Call the product tool.",
                tools=[run_nested_confirmation],
            )
        app = _build_resumable_tool_app(name="NestedRequestInputToolApp", agent=agent)
        session_service = InMemorySessionService()
        runner = Runner(
            app=app,
            session_service=session_service,
            artifact_service=InMemoryArtifactService(),
        )
        user_id = "user-nested-request-input-tool"
        session_id = "session-nested-request-input-tool"
        try:
            await session_service.create_session(app_name=app.name, user_id=user_id, session_id=session_id, state={})

            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\].*", category=UserWarning)
                first_events = await _collect_events(
                    runner.run_async(
                        user_id=user_id,
                        session_id=session_id,
                        new_message=Content(role="user", parts=[Part(text="Start PPT product.")]),
                    )
                )
            first_invocation_id = first_events[0].invocation_id
            request_events = [
                event
                for event in first_events
                if any(call.name == ADK_REQUEST_INPUT_FUNCTION_NAME for call in event.get_function_calls())
            ]
            self.assertEqual(len(request_events), 1)
            request_call = request_events[0].get_function_calls()[0]
            self.assertEqual(request_call.id, "nested_tool_confirmation")

            tool_response_events = [
                event
                for event in first_events
                for response in event.get_function_responses()
                if response.name == "run_nested_confirmation"
            ]
            self.assertEqual(len(tool_response_events), 1)
            tool_response = tool_response_events[0].get_function_responses()[0]
            self.assertEqual(tool_response.response, {"status": "done", "result": None})

            resume_message = Content(
                role="user",
                parts=[
                    Part(
                        function_response=FunctionResponse(
                            id=request_call.id,
                            name=ADK_REQUEST_INPUT_FUNCTION_NAME,
                            response={"result": {"action": "confirm", "message": ""}},
                        )
                    )
                ],
            )
            second_events = await _collect_events(
                runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    invocation_id=first_invocation_id,
                    new_message=resume_message,
                )
            )

            self.assertTrue(second_events)
            session = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session_id)
            self.assertEqual(session.state["nested_tool_result"], None)
            self.assertEqual(session.state["nested_tool_request_input_runs"], 1)
            self.assertNotIn("nested_tool_resume_value", session.state)
        finally:
            await runner.close()

    async def test_tool_confirmation_payload_resumes_same_tool_call(self) -> None:
        """Prove the ADK-native tool HITL path can carry PPT edit semantics."""

        async def run_ppt_confirmation_tool(task: str, tool_context: ToolContext) -> dict[str, Any]:
            tool_confirmation = tool_context.tool_confirmation
            if tool_confirmation is None:
                tool_context.state["tool_confirmation_waiting_task"] = task
                tool_context.request_confirmation(
                    hint="Confirm or revise the PPT requirement.",
                    payload={
                        "schema_version": "ppt-confirmation-v1",
                        "stage": "requirement_confirmation",
                        "task": task,
                        "allowed_actions": ["confirm", "revise"],
                    },
                )
                tool_context.actions.skip_summarization = True
                return {"status": "awaiting_requirement_confirmation"}

            tool_context.state["tool_confirmation_confirmed"] = tool_confirmation.confirmed
            tool_context.state["tool_confirmation_payload"] = tool_confirmation.payload
            return {
                "status": "continued",
                "confirmed": tool_confirmation.confirmed,
                "payload": tool_confirmation.payload,
            }

        fake_llm = _ToolCallingFakeLlm(
            function_call=FunctionCall(
                name="run_ppt_confirmation_tool",
                id="run_ppt_confirmation_tool_call",
                args={"task": "Make a PPT."},
            )
        )
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\].*", category=UserWarning)
            agent = LlmAgent(
                name="ToolConfirmationPayloadRoot",
                model=fake_llm,
                instruction="Call the product tool.",
                tools=[run_ppt_confirmation_tool],
            )
        app = _build_resumable_tool_app(name="ToolConfirmationPayloadApp", agent=agent)
        session_service = InMemorySessionService()
        runner = Runner(
            app=app,
            session_service=session_service,
            artifact_service=InMemoryArtifactService(),
        )
        user_id = "user-tool-confirmation-payload"
        session_id = "session-tool-confirmation-payload"
        try:
            await session_service.create_session(app_name=app.name, user_id=user_id, session_id=session_id, state={})

            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\].*", category=UserWarning)
                first_events = await _collect_events(
                    runner.run_async(
                        user_id=user_id,
                        session_id=session_id,
                        new_message=Content(role="user", parts=[Part(text="Start PPT product.")]),
                    )
                )
            first_invocation_id = first_events[0].invocation_id
            confirmation_events = [
                event
                for event in first_events
                if any(call.name == ADK_REQUEST_CONFIRMATION_FUNCTION_NAME for call in event.get_function_calls())
            ]
            self.assertEqual(len(confirmation_events), 1)
            confirmation_call = confirmation_events[0].get_function_calls()[0]
            self.assertEqual(confirmation_call.name, ADK_REQUEST_CONFIRMATION_FUNCTION_NAME)
            self.assertEqual(
                confirmation_call.args["toolConfirmation"]["payload"]["stage"],
                "requirement_confirmation",
            )
            self.assertEqual(
                confirmation_call.args["toolConfirmation"]["payload"]["allowed_actions"],
                ["confirm", "revise"],
            )

            resume_payload = {"action": "revise", "message": "改成 5 页，并面向投资人。"}
            resume_message = Content(
                role="user",
                parts=[
                    Part(
                        function_response=FunctionResponse(
                            id=confirmation_call.id,
                            name=ADK_REQUEST_CONFIRMATION_FUNCTION_NAME,
                            response={"confirmed": True, "payload": resume_payload},
                        )
                    )
                ],
            )
            second_events = await _collect_events(
                runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    invocation_id=first_invocation_id,
                    new_message=resume_message,
                )
            )

            self.assertTrue(second_events)
            self.assertTrue(all(event.invocation_id == first_invocation_id for event in second_events))
            tool_response_events = [
                event
                for event in second_events
                for response in event.get_function_responses()
                if response.name == "run_ppt_confirmation_tool"
            ]
            self.assertEqual(len(tool_response_events), 1)
            response_payload = tool_response_events[0].get_function_responses()[0].response
            self.assertEqual(response_payload["status"], "continued")
            self.assertEqual(response_payload["payload"], resume_payload)

            session = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session_id)
            self.assertEqual(session.state["tool_confirmation_waiting_task"], "Make a PPT.")
            self.assertTrue(session.state["tool_confirmation_confirmed"])
            self.assertEqual(session.state["tool_confirmation_payload"], resume_payload)
        finally:
            await runner.close()

    async def test_invocation_id_only_does_not_resume_workflow_node_runtime(self) -> None:
        """Pin the current ADK 2.1 Workflow limitation before PPT HITL migration."""

        @node(name="InvocationOnlyReviewNode", rerun_on_resume=True)
        def review_confirmation(ctx: Context, node_input: Any = None):
            run_count = int(ctx.state.get("invocation_only_node_runs", 0) or 0) + 1
            yield Event(
                state={
                    "invocation_only_node_runs": run_count,
                    "latest_node_input_kind": "none" if node_input is None else "value",
                }
            )
            yield RequestInput(
                interrupt_id="invocation_only_confirmation",
                message="Confirm again.",
            )

        workflow = Workflow(
            name="InvocationOnlyResumeWorkflow",
            edges=[("START", review_confirmation)],
        )
        app = _build_resumable_app(name="InvocationOnlyResumeApp", workflow=workflow)
        session_service = InMemorySessionService()
        runner = Runner(
            app=app,
            session_service=session_service,
            artifact_service=InMemoryArtifactService(),
        )
        user_id = "user-invocation-only-resume"
        session_id = "session-invocation-only-resume"
        try:
            await session_service.create_session(app_name=app.name, user_id=user_id, session_id=session_id, state={})

            first_events = await _collect_events(
                runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=Content(role="user", parts=[Part(text="Initial task")]),
                )
            )
            first_invocation_id = first_events[0].invocation_id
            second_events = await _collect_events(
                runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    invocation_id=first_invocation_id,
                )
            )

            second_invocation_ids = {event.invocation_id for event in second_events}
            self.assertEqual(len(second_invocation_ids), 1)
            self.assertNotEqual(next(iter(second_invocation_ids)), first_invocation_id)

            session = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session_id)
            self.assertEqual(session.state["invocation_only_node_runs"], 2)
            self.assertEqual(session.state["latest_node_input_kind"], "none")
        finally:
            await runner.close()
