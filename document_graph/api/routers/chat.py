from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from document_graph.api.deps import DBDep
from document_graph.db.models import Conversation, Workspace
from document_graph.langgraph.chat_flow import run_chat
from document_graph.redis_utils import conversation_lock, redis_client
from document_graph.settings import load_settings


router = APIRouter()
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1)
    top_k: int = 8


class ChatResponse(BaseModel):
    conversation_id: uuid.UUID
    answer: str
    refs: list[dict]


def _ensure_conversation(db: Session, *, workspace_id: uuid.UUID, conversation_id: uuid.UUID | None) -> uuid.UUID:
    if conversation_id is not None:
        conv = db.query(Conversation).filter(Conversation.id == conversation_id).one_or_none()
        if conv is None or conv.workspace_id != workspace_id:
            raise HTTPException(status_code=404, detail="conversation_not_found")
        return conversation_id

    conv = Conversation(workspace_id=workspace_id, title="")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv.id


@router.post("/chat", response_model=ChatResponse)
def chat(workspace_id: uuid.UUID, payload: ChatRequest, db: Session = DBDep) -> ChatResponse:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace_not_found")

    conv_id = _ensure_conversation(db, workspace_id=workspace_id, conversation_id=payload.conversation_id)
    logger.info("chat_request workspace_id=%s conversation_id=%s", workspace_id, conv_id)

    settings = load_settings()
    r = redis_client(settings.redis.url)

    try:
        with conversation_lock(r, conversation_id=str(conv_id), ttl_s=60):
            out = run_chat(
                db=db,
                workspace_id=str(workspace_id),
                conversation_id=str(conv_id),
                user_message=payload.message,
                top_k=int(payload.top_k),
            )
    except RuntimeError as e:
        if str(e) == "conversation_locked":
            raise HTTPException(status_code=409, detail="conversation_locked") from e
        raise

    logger.info("chat_response workspace_id=%s conversation_id=%s", workspace_id, conv_id)
    return ChatResponse(conversation_id=conv_id, answer=str(out.get("answer") or ""), refs=list(out.get("refs") or []))
