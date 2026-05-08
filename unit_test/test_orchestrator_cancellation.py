import asyncio
import unittest
import uuid

from src.agents.orchestrator.orchestrator_agent import Orchestrator
from src.runtime.cancellation import TaskCancelledError, get_cancellation_manager


class _Session:
    def __init__(self, session_id: str) -> None:
        self.id = session_id


class _ToolContext:
    def __init__(self, session_id: str) -> None:
        self.session = _Session(session_id)
        self.state: dict[str, object] = {}


class _DummyOrchestrator:
    _resolve_tool_context_session_id = staticmethod(Orchestrator._resolve_tool_context_session_id)
    _advance_tool_counters = staticmethod(Orchestrator._advance_tool_counters)
    _normalize_generated_tool_result = staticmethod(Orchestrator._normalize_generated_tool_result)

    def _record_tool_started(self, *_args, **_kwargs) -> None:
        pass

    def _record_tool_finished(self, *_args, **_kwargs) -> None:
        pass

    def _snapshot_workspace_files(self):
        return {}

    def _maybe_record_tool_files(self, *_args, **_kwargs) -> None:
        pass


class OrchestratorCancellationTests(unittest.IsolatedAsyncioTestCase):
    def _register_bound_run(self) -> tuple[str, str]:
        run_id = f"run-{uuid.uuid4().hex}"
        session_id = f"session-{uuid.uuid4().hex}"
        manager = get_cancellation_manager()
        manager.register_run(run_id=run_id, channel="web", chat_id="chat")
        manager.bind_runtime_session(run_id, session_id)
        return run_id, session_id

    async def test_sync_tool_gate_raises_before_runner(self) -> None:
        run_id, session_id = self._register_bound_run()
        get_cancellation_manager().request_cancel_by_run_id(run_id)
        ran = False

        def _runner() -> str:
            nonlocal ran
            ran = True
            return "done"

        with self.assertRaises(TaskCancelledError):
            Orchestrator._run_tool_with_events(
                _DummyOrchestrator(),
                tool_context=_ToolContext(session_id),
                tool_name="read_file",
                stage="inspection",
                args={},
                runner=_runner,
            )

        self.assertFalse(ran)

    async def test_sync_tool_gate_raises_after_runner(self) -> None:
        run_id, session_id = self._register_bound_run()

        def _runner() -> str:
            get_cancellation_manager().request_cancel_by_run_id(run_id)
            return "done"

        with self.assertRaises(TaskCancelledError):
            Orchestrator._run_tool_with_events(
                _DummyOrchestrator(),
                tool_context=_ToolContext(session_id),
                tool_name="read_file",
                stage="inspection",
                args={},
                runner=_runner,
            )

    async def test_async_tool_gate_raises_before_runner(self) -> None:
        run_id, session_id = self._register_bound_run()
        get_cancellation_manager().request_cancel_by_run_id(run_id)
        ran = False

        async def _runner() -> str:
            nonlocal ran
            ran = True
            return "done"

        with self.assertRaises(TaskCancelledError):
            await Orchestrator._run_async_tool_with_events(
                _DummyOrchestrator(),
                tool_context=_ToolContext(session_id),
                tool_name="invoke_agent",
                stage="expert_execution",
                args={},
                runner=_runner,
            )

        self.assertFalse(ran)

    async def test_async_tool_gate_raises_after_runner(self) -> None:
        run_id, session_id = self._register_bound_run()

        async def _runner() -> str:
            await asyncio.sleep(0)
            get_cancellation_manager().request_cancel_by_run_id(run_id)
            return "done"

        with self.assertRaises(TaskCancelledError):
            await Orchestrator._run_async_tool_with_events(
                _DummyOrchestrator(),
                tool_context=_ToolContext(session_id),
                tool_name="invoke_agent",
                stage="expert_execution",
                args={},
                runner=_runner,
            )


if __name__ == "__main__":
    unittest.main()
