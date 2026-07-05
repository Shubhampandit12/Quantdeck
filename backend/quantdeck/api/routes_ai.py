"""AI trading REST endpoints: status, manual chat, and autonomous sessions.

All AI actions ultimately route through `engine.place_order()` via the tool
dispatch layer — the AI has no privileged path around the shared risk checks.
When no Anthropic key is configured the session manager degrades gracefully
(returns an "AI disabled" reply), so these endpoints still respond 200.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.config import settings
from .deps import get_ai_manager

router = APIRouter()


class ChatBody(BaseModel):
    portfolio_id: str
    message: str


class SessionStartBody(BaseModel):
    portfolio_id: str
    strategy_prompt: str
    guardrails: Optional[dict[str, Any]] = None


@router.get("/ai/status")
async def ai_status() -> dict:
    """Whether the AI is usable and which model it would use."""
    return {"enabled": settings.ai_enabled, "model": settings.ai_model}


@router.post("/ai/chat")
async def ai_chat(body: ChatBody) -> dict:
    """One-off manual chat / natural-language order. Returns reply + trace."""
    return await get_ai_manager().chat(body.portfolio_id, body.message)


@router.post("/ai/session/start")
async def ai_session_start(body: SessionStartBody) -> dict:
    """Launch an autonomous trading session. Returns {session_id, status}."""
    return await get_ai_manager().start_session(
        body.portfolio_id,
        body.strategy_prompt,
        guardrails=body.guardrails,
    )


@router.post("/ai/session/{session_id}/pause")
async def ai_session_pause(session_id: str) -> dict:
    # The manager returns an {status:"error", message:"no such session"} dict for
    # unknown ids rather than raising, so we surface it verbatim (HTTP 200).
    return await get_ai_manager().pause(session_id)


@router.post("/ai/session/{session_id}/resume")
async def ai_session_resume(session_id: str) -> dict:
    return await get_ai_manager().resume(session_id)


@router.post("/ai/session/{session_id}/stop")
async def ai_session_stop(session_id: str) -> dict:
    """Stop a session (kill-switch: flattens everything on the way out)."""
    return await get_ai_manager().stop(session_id)


@router.get("/ai/session/{session_id}")
async def ai_session_get(session_id: str) -> dict:
    # get_session() is synchronous and returns an error dict for unknown ids.
    return get_ai_manager().get_session(session_id)
