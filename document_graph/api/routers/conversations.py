from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from document_graph.api.deps import DBDep
from document_graph.db.models import Conversation, Workspace


router = APIRouter()
logger = logging.getLogger(__name__)


class ConversationCreateResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationListItem(BaseModel):
    id: uuid.UUID
    title: str
    updated_at: datetime


@router.post("", response_model=ConversationCreateResponse)
def create_conversation(workspace_id: uuid.UUID, db: Session = DBDep) -> ConversationCreateResponse:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace_not_found")
    conv = Conversation(workspace_id=workspace_id, title="")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    logger.info("conversation_created workspace_id=%s conversation_id=%s", workspace_id, conv.id)
    return ConversationCreateResponse.model_validate(conv, from_attributes=True)


@router.get("", response_model=list[ConversationListItem])
def list_conversations(workspace_id: uuid.UUID, db: Session = DBDep) -> list[ConversationListItem]:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace_not_found")
    rows = (
        db.query(Conversation)
        .filter(Conversation.workspace_id == workspace_id)
        .order_by(Conversation.updated_at.desc())
        .limit(50)
        .all()
    )
    return [ConversationListItem.model_validate(r, from_attributes=True) for r in rows]

