"""Curio's agent engine: the domain-independent half of every recommendation agent.

A domain implements a `Toolkit` (its tools, their policies, how READ tools run, how
APPROVE effects are validated, how answers are grounded) and the `AgentEngine` runs the
turn: plan, a bounded tool loop, signed human-in-the-loop proposals, a grounding check
with one self-correction, and a deterministic fallback. Nothing here imports a domain or
the app (`tests/test_architecture.py` enforces it).
"""

from curio.engine.agent import MAX_HISTORY_TURNS, MAX_TOOL_CALLS, TURN_DEADLINE_SECONDS, AgentEngine
from curio.engine.grounding import grounding_report, merge_reports
from curio.engine.schemas import (
    ActionDecision,
    AgentEvent,
    AgentStep,
    AppliedEffect,
    AssistantReply,
    ChatTurn,
    GroundingReport,
    Intent,
    PendingAction,
    ReplyEvent,
    Schemas,
    StepEvent,
    TurnContext,
)
from curio.engine.signing import ActionSigner, InvalidActionError, narrow_args
from curio.engine.toolkit import ToolError, Toolkit, Turn
from curio.engine.tools import AskShopper, Policy

__all__ = [
    "MAX_HISTORY_TURNS",
    "MAX_TOOL_CALLS",
    "TURN_DEADLINE_SECONDS",
    "ActionDecision",
    "ActionSigner",
    "AgentEngine",
    "AgentEvent",
    "AgentStep",
    "AppliedEffect",
    "AskShopper",
    "AssistantReply",
    "ChatTurn",
    "GroundingReport",
    "Intent",
    "InvalidActionError",
    "PendingAction",
    "Policy",
    "ReplyEvent",
    "Schemas",
    "StepEvent",
    "ToolError",
    "Toolkit",
    "Turn",
    "TurnContext",
    "grounding_report",
    "merge_reports",
    "narrow_args",
]
