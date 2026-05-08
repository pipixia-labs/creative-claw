import tempfile
import threading
import time
import sys
import unittest
import uuid
from pathlib import Path

from src.runtime.cancellation import get_cancellation_manager
from src.tools.builtin_tools import BuiltinToolbox, builtin_tool_scope


class ExecCommandCancelTests(unittest.TestCase):
    def test_foreground_exec_command_keeps_string_result_and_can_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_id = f"run-{uuid.uuid4().hex}"
            scope_key = f"session-{uuid.uuid4().hex}"
            cancellation = get_cancellation_manager()
            cancellation.register_run(run_id=run_id, channel="web", chat_id="chat")
            cancellation.bind_runtime_session(run_id, scope_key)
            toolbox = BuiltinToolbox(Path(tmpdir))
            result: list[str] = []

            thread = threading.Thread(
                target=lambda: result.append(
                    toolbox.exec_command("sleep 30", timeout=30, scope_key=scope_key)
                ),
                daemon=True,
            )
            thread.start()
            time.sleep(0.2)

            cancellation.request_cancel_by_run_id(run_id)
            thread.join(timeout=5)

            self.assertFalse(thread.is_alive())
            self.assertEqual(result, ["Error: Command cancelled"])

    def test_foreground_exec_command_success_format_stays_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            toolbox = BuiltinToolbox(Path(tmpdir))

            result = toolbox.exec_command("printf hello", timeout=5)

            self.assertEqual(result, "hello")

    def test_foreground_exec_command_stderr_format_stays_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            toolbox = BuiltinToolbox(Path(tmpdir))

            result = toolbox.exec_command(
                f"{sys.executable} -c \"import sys; sys.stderr.write('warn')\"",
                timeout=5,
            )

            self.assertEqual(result, "STDERR:\nwarn")

    def test_scoped_subprocess_helper_can_cancel_long_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_id = f"run-{uuid.uuid4().hex}"
            scope_key = f"session-{uuid.uuid4().hex}"
            cancellation = get_cancellation_manager()
            cancellation.register_run(run_id=run_id, channel="web", chat_id="chat")
            cancellation.bind_runtime_session(run_id, scope_key)
            toolbox = BuiltinToolbox(Path(tmpdir))
            errors: list[str] = []

            def _target() -> None:
                try:
                    with builtin_tool_scope(scope_key):
                        toolbox._run_subprocess_checked(
                            [sys.executable, "-c", "import time; time.sleep(30)"],
                            timeout=30,
                        )
                except RuntimeError as exc:
                    errors.append(str(exc))

            thread = threading.Thread(target=_target, daemon=True)
            thread.start()
            time.sleep(0.2)

            cancellation.request_cancel_by_run_id(run_id)
            thread.join(timeout=5)

            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, ["Command cancelled"])


if __name__ == "__main__":
    unittest.main()
