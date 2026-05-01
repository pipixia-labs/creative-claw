"""Root agent used by ADK live evals for Design product routing."""

from __future__ import annotations

from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService

from src.agents.orchestrator.orchestrator_agent import Orchestrator
from src.runtime.expert_registry import build_expert_agents

_APP_NAME = "creative_claw_design_eval"
_session_service = InMemorySessionService()
_artifact_service = InMemoryArtifactService()
_expert_agents = build_expert_agents(app_name=_APP_NAME)
_orchestrator = Orchestrator(
    session_service=_session_service,
    artifact_service=_artifact_service,
    expert_agents=_expert_agents,
    app_name=_APP_NAME,
)

root_agent = _orchestrator.agent
