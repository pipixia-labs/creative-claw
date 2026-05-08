import unittest
import uuid

from conf.system import SYS_CONFIG
from src.runtime.cancellation import TaskCancelledError, get_cancellation_manager
from src.runtime.models import InboundMessage
from src.runtime.workflow_service import CreativeClawRuntime


class WorkflowCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_cancel_stops_before_initial_state(self) -> None:
        runtime = CreativeClawRuntime()
        run_id = f"run-{uuid.uuid4().hex}"
        inbound = InboundMessage(
            channel="web",
            sender_id="client-1",
            chat_id="chat-1",
            text="make a long task",
            metadata={"run_id": run_id},
        )
        cancellation = get_cancellation_manager()
        cancellation.register_run(run_id=run_id, channel="web", chat_id="chat-1")
        cancellation.request_cancel_by_run_id(run_id)

        with self.assertRaises(TaskCancelledError):
            _events = [event async for event in runtime.run_message(inbound)]

        user_id, session_id = await runtime._ensure_session(inbound)
        session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        self.assertNotIn("workflow_status", session.state)


if __name__ == "__main__":
    unittest.main()
