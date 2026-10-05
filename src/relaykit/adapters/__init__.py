"""Adapter registry. ``get(kind)`` returns the adapter for an agent's ``adapter`` (or name)."""
from __future__ import annotations

from .base import Adapter, Caps, Launch, RunSpec, Turn
from .claude import ClaudeAdapter
from .claude_like import AmpAdapter, CursorAdapter, QwenAdapter
from .codex import CodexAdapter
from .custom import CustomAdapter
from .gemini import GeminiAdapter
from .kiro import KiroAdapter
from .others import AiderAdapter, CopilotAdapter, OpencodeAdapter

REGISTRY = {a.id: a for a in (
    ClaudeAdapter(), CodexAdapter(), GeminiAdapter(), KiroAdapter(), QwenAdapter(), AmpAdapter(),
    CursorAdapter(), OpencodeAdapter(), CopilotAdapter(), AiderAdapter(), CustomAdapter(),
)}


def get(kind: str) -> Adapter:
    if kind not in REGISTRY:
        raise KeyError(f"unknown agent adapter {kind!r}; known: {', '.join(sorted(REGISTRY))}")
    return REGISTRY[kind]


__all__ = ["Adapter", "Caps", "Launch", "RunSpec", "Turn", "REGISTRY", "get"]
