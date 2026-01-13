from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from document_graph.api.deps import DBDep
from document_graph.db.models import Task


router = APIRouter()
logger = logging.getLogger(__name__)


class TaskResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    document_id: uuid.UUID | None
    type: str
    status: str
    stage: str | None
    progress: float | None
    error: dict
    result: dict
    attempt: int
    max_attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: uuid.UUID, db: Session = DBDep) -> TaskResponse:
    task = db.query(Task).filter(Task.id == task_id).one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="task_not_found")
    logger.debug("task_fetched task_id=%s status=%s", task_id, task.status)
    return TaskResponse.model_validate(task, from_attributes=True)

