import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from src.runtime.process_sessions import ProcessSessionManager


def _wait_for(predicate, *, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class ProcessSessionCancelTests(unittest.TestCase):
    def test_kill_sessions_only_targets_requested_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProcessSessionManager()
            cwd = Path(tmpdir)
            first = manager.start_session(command="sleep 30", cwd=cwd, scope_key="scope-a")
            second = manager.start_session(command="sleep 30", cwd=cwd, scope_key="scope-b")

            summary = manager.kill_sessions(scope_key="scope-a")

            self.assertEqual(summary.found, 1)
            self.assertEqual(summary.killed, 1)
            self.assertEqual(summary.failed, 0)
            self.assertIn(first.session_id, summary.session_ids)
            self.assertIsNotNone(manager.poll_session(second.session_id, scope_key="scope-b"))

            manager.kill_session(second.session_id, scope_key="scope-b")

    @unittest.skipIf(sys.platform == "win32", "POSIX process-group assertion")
    def test_start_session_creates_posix_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProcessSessionManager()
            session = manager.start_session(command="sleep 30", cwd=Path(tmpdir), scope_key="scope")
            try:
                self.assertEqual(os.getpgid(session.process.pid), session.process.pid)
            finally:
                manager.kill_session(session.session_id, scope_key="scope")

    @unittest.skipIf(sys.platform == "win32", "POSIX child process assertion")
    def test_kill_session_terminates_child_process_group_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProcessSessionManager()
            session = manager.start_session(
                command="bash -c 'sleep 30 & echo child:$!; wait'",
                cwd=Path(tmpdir),
                scope_key="scope",
            )

            child_pid: int | None = None
            try:
                for _ in range(40):
                    payload = manager.poll_session(session.session_id, timeout_ms=50, scope_key="scope")
                    output = str((payload or {}).get("output") or "")
                    for line in output.splitlines():
                        if line.startswith("child:"):
                            child_pid = int(line.split(":", 1)[1])
                            break
                    if child_pid is not None:
                        break
                self.assertIsNotNone(child_pid)

                self.assertTrue(manager.kill_session(session.session_id, scope_key="scope"))
                assert child_pid is not None
                self.assertTrue(_wait_for(lambda: not _process_exists(child_pid), timeout=3.0))
            finally:
                manager.kill_session(session.session_id, scope_key="scope")

    def test_repeated_kill_session_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProcessSessionManager()
            session = manager.start_session(command="sleep 30", cwd=Path(tmpdir), scope_key="scope")

            self.assertTrue(manager.kill_session(session.session_id, scope_key="scope"))
            self.assertTrue(manager.kill_session(session.session_id, scope_key="scope"))


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


if __name__ == "__main__":
    unittest.main()
