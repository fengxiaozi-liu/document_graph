from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from document_graph.api.deps import DBDep
from document_graph.db.models import Conversation, Message


router = APIRouter()


class MessageItem(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    metadata: dict
    created_at: datetime


class MessageListResponse(BaseModel):
    items: list[MessageItem]
    next_before: uuid.UUID | None = None


@router.get("/conversations/{conversation_id}/messages", response_model=MessageListResponse)
def list_messages(
    conversation_id: uuid.UUID,
    before: uuid.UUID | None = Query(default=None, description="Cursor: message id; returns messages older than this."),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = DBDep,
) -> MessageListResponse:
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation_not_found")

    q = db.query(Message).filter(Message.conversation_id == conversation_id)

    if before is not None:
        pivot = (
            db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .filter(Message.id == before)
            .one_or_none()
        )
        if pivot is None:
            raise HTTPException(status_code=404, detail="before_message_not_found")
        q = q.filter(
            or_(
                Message.created_at < pivot.created_at,
                and_(Message.created_at == pivot.created_at, Message.id < pivot.id),
            )
        )

    rows = q.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1).all()
    next_before: uuid.UUID | None = None
    if len(rows) > limit:
        next_before = rows[-1].id
        rows = rows[:limit]

    rows.reverse()
    return MessageListResponse(
        items=[
            MessageItem(
                id=r.id,
                conversation_id=r.conversation_id,
                role=r.role,
                content=r.content,
                metadata=getattr(r, "metadata_", {}) or {},
                created_at=r.created_at,
            )
            for r in rows
        ],
        next_before=next_before,
    )
