from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from document_graph.api.deps import DBDep
from document_graph.config import load_app_config
from document_graph.db.models import Workspace
from document_graph.vectorstore.qdrant_admin import drop_alias_if_exists, drop_collection_if_exists, qdrant_client


router = APIRouter()
logger = logging.getLogger(__name__)


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    qdrant_alias: str | None = None


class WorkspaceResponse(BaseModel):
    id: uuid.UUID
    name: str
    qdrant_collection: str
    qdrant_alias: str | None
    created_at: datetime
    updated_at: datetime


def _collection_name(workspace_id: uuid.UUID) -> str:
    return f"ws_{workspace_id.hex}"


@router.post("", response_model=WorkspaceResponse)
def create_workspace(payload: WorkspaceCreateRequest, db: Session = DBDep) -> WorkspaceResponse:
    workspace_id = uuid.uuid4()
    ws = Workspace(
        id=workspace_id,
        name=payload.name,
        qdrant_collection=_collection_name(workspace_id),
        qdrant_alias=payload.qdrant_alias,
    )
    db.add(ws)
    db.commit()
    db.refresh(ws)
    logger.info("workspace_created workspace_id=%s", ws.id)
    return WorkspaceResponse.model_validate(ws, from_attributes=True)


@router.get("", response_model=list[WorkspaceResponse])
def list_workspaces(db: Session = DBDep) -> list[WorkspaceResponse]:
    rows = db.query(Workspace).order_by(Workspace.updated_at.desc()).all()
    return [WorkspaceResponse.model_validate(r, from_attributes=True) for r in rows]


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
def get_workspace(workspace_id: uuid.UUID, db: Session = DBDep) -> WorkspaceResponse:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace_not_found")
    return WorkspaceResponse.model_validate(ws, from_attributes=True)


@router.delete("/{workspace_id}")
def delete_workspace(workspace_id: uuid.UUID, db: Session = DBDep) -> dict[str, str]:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace_not_found")

    cfg = load_app_config()
    qc = qdrant_client(cfg.qdrant.url)
    if ws.qdrant_alias:
        drop_alias_if_exists(qc, ws.qdrant_alias)
    drop_collection_if_exists(qc, ws.qdrant_collection)

    db.delete(ws)
    db.commit()
    logger.info("workspace_deleted workspace_id=%s", workspace_id)
    return {"status": "deleted"}

