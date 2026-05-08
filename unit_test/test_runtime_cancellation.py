import asyncio
import unittest

from src.runtime.cancellation import CancellationManager, TaskCancelledError


class RuntimeCancellationTests(unittest.TestCase):
    def test_pending_cancel_survives_until_runtime_session_bind(self) -> None:
        manager = CancellationManager()
        manager.register_run(run_id="run-1", channel="web", chat_id="chat-1")

        summary = manager.request_cancel_by_run_id("run-1")
        record = manager.bind_runtime_session("run-1", "runtime-1")

        self.assertIsNone(summary)
        self.assertIsNotNone(record)
        self.assertTrue(record.requested)
        self.assertTrue(manager.is_cancel_requested("runtime-1"))

    def test_bound_cancel_marks_runtime_session_cancelled(self) -> None:
        manager = CancellationManager()
        manager.register_run(run_id="run-1", channel="web", chat_id="chat-1")
        manager.bind_runtime_session("run-1", "runtime-1")

        summary = manager.request_cancel_by_run_id("run-1")

        self.assertIsNotNone(summary)
        self.assertTrue(manager.is_cancel_requested("runtime-1"))

    def test_raise_if_cancelled_uses_cancelled_error_control_flow(self) -> None:
        manager = CancellationManager()
        manager.register_run(run_id="run-1", channel="web", chat_id="chat-1")
        manager.bind_runtime_session("run-1", "runtime-1")
        manager.request_cancel_by_run_id("run-1", reason="user_stop")

        with self.assertRaises(TaskCancelledError) as raised:
            manager.raise_if_cancelled("runtime-1")

        self.assertIsInstance(raised.exception, asyncio.CancelledError)
        self.assertEqual(raised.exception.runtime_session_id, "runtime-1")
        self.assertEqual(raised.exception.reason, "user_stop")

    def test_task_cancelled_error_is_not_caught_by_exception(self) -> None:
        caught_by_exception = False

        try:
            raise TaskCancelledError("runtime-1")
        except Exception:  # noqa: BLE001 - this asserts CancelledError bypasses Exception.
            caught_by_exception = True
        except asyncio.CancelledError:
            pass

        self.assertFalse(caught_by_exception)

    def test_complete_run_clears_runtime_session_mapping(self) -> None:
        manager = CancellationManager()
        manager.register_run(run_id="run-1", channel="web", chat_id="chat-1")
        manager.bind_runtime_session("run-1", "runtime-1")
        manager.request_cancel_by_run_id("run-1")

        manager.complete_run("run-1")

        self.assertFalse(manager.is_cancel_requested("runtime-1"))
        record = manager.get_record("run-1")
        self.assertIsNotNone(record)
        self.assertTrue(record.completed)


if __name__ == "__main__":
    unittest.main()
