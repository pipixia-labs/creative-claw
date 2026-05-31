from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.runtime.agent_tool_transport import run_agent_tool, supports_agent_tool_context


class _State(dict):
    def to_dict(self) -> dict:
        return dict(self)


class AgentToolTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_agent_tool_overlays_child_initial_state(self) -> None:
        calls: list[dict] = []

        class FakeAgentTool:
            def __init__(self, *, agent: object, include_plugins: bool) -> None:
                calls.append({"agent": agent, "include_plugins": include_plugins})

            async def run_async(self, *, args: dict, tool_context: object) -> dict:
                calls.append(
                    {
                        "args": args,
                        "state_snapshot": tool_context.state.to_dict(),
                    }
                )
                tool_context.state.update({"child_output": "written"})
                return {"status": "ok"}

        agent = object()
        state = _State({"parent_key": "parent"})
        tool_context = SimpleNamespace(
            state=state,
            _invocation_context=SimpleNamespace(
                plugin_manager=SimpleNamespace(plugins=[object()])
            ),
        )

        with patch("src.runtime.agent_tool_transport.AgentTool", FakeAgentTool):
            result = await run_agent_tool(
                agent=agent,
                request="Run child agent",
                tool_context=tool_context,
                initial_state={"child_key": "child"},
            )

        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(calls[0]["agent"], agent)
        self.assertTrue(calls[0]["include_plugins"])
        self.assertEqual(calls[1]["args"], {"request": "Run child agent"})
        self.assertEqual(calls[1]["state_snapshot"]["parent_key"], "parent")
        self.assertEqual(calls[1]["state_snapshot"]["child_key"], "child")
        self.assertNotIn("child_key", state)
        self.assertEqual(state["child_output"], "written")

    async def test_run_agent_tool_omits_empty_plugin_inheritance(self) -> None:
        calls: list[dict] = []

        class FakeAgentTool:
            def __init__(self, *, agent: object, include_plugins: bool) -> None:
                calls.append({"include_plugins": include_plugins})

            async def run_async(self, *, args: dict, tool_context: object) -> str:
                return "done"

        tool_context = SimpleNamespace(
            state=_State(),
            _invocation_context=SimpleNamespace(plugin_manager=SimpleNamespace(plugins=[])),
        )

        with patch("src.runtime.agent_tool_transport.AgentTool", FakeAgentTool):
            result = await run_agent_tool(
                agent=object(),
                request="Run child agent",
                tool_context=tool_context,
            )

        self.assertEqual(result, "done")
        self.assertFalse(calls[0]["include_plugins"])

    def test_supports_agent_tool_context_requires_invocation_and_state_snapshot(self) -> None:
        self.assertTrue(
            supports_agent_tool_context(
                SimpleNamespace(state=_State(), _invocation_context=SimpleNamespace())
            )
        )
        self.assertFalse(supports_agent_tool_context(SimpleNamespace(state=_State())))
        self.assertFalse(
            supports_agent_tool_context(
                SimpleNamespace(state={}, _invocation_context=SimpleNamespace())
            )
        )
