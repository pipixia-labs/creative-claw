"""Small helpers for ADK AgentTool-based internal agent calls."""

from __future__ import annotations

import copy
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext

from src.runtime.adk_compat import get_invocation_context, has_invocation_context


class _StateWithAgentInitialOverlay:
    """State proxy that injects AgentTool child initial state without parent writes."""

    def __init__(self, state: Any, overlay: dict[str, Any]) -> None:
        self._state = state
        self._overlay = copy.deepcopy(overlay)

    def to_dict(self) -> dict[str, Any]:
        if hasattr(self._state, "to_dict"):
            state_dict = dict(self._state.to_dict())
        else:
            state_dict = dict(self._state)
        state_dict.update(copy.deepcopy(self._overlay))
        return state_dict

    def update(self, delta: dict[str, Any]) -> None:
        self._state.update(delta)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._state, name)


class _ToolContextWithAgentInitialState:
    """ToolContext proxy used only to seed an AgentTool child session."""

    def __init__(self, tool_context: ToolContext, initial_state: dict[str, Any]) -> None:
        self._tool_context = tool_context
        self.state = _StateWithAgentInitialOverlay(tool_context.state, initial_state)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tool_context, name)


def supports_agent_tool_context(tool_context: ToolContext) -> bool:
    """Return whether the context can safely run an ADK AgentTool child agent."""
    return has_invocation_context(tool_context) and hasattr(tool_context.state, "to_dict")


async def run_agent_tool(
    *,
    agent: BaseAgent,
    request: str,
    tool_context: ToolContext,
    initial_state: dict[str, Any] | None = None,
) -> Any:
    """Run one internal agent through ADK AgentTool and return its tool result."""
    agent_tool_context: ToolContext = (
        _ToolContextWithAgentInitialState(tool_context, initial_state)
        if initial_state
        else tool_context
    )
    parent_plugins = getattr(
        getattr(get_invocation_context(agent_tool_context), "plugin_manager", None),
        "plugins",
        None,
    )
    agent_tool = AgentTool(agent=agent, include_plugins=bool(parent_plugins))
    return await agent_tool.run_async(
        args={"request": request},
        tool_context=agent_tool_context,
    )
