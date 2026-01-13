from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from document_graph.api.deps import DBDep
from document_graph.db.models import Document, DocumentVersion, Source, Task, Workspace
from document_graph.document_parsing import (
    MissingParserDependency,
    UnsupportedDocumentType,
    guess_mime_type,
    is_supported_extension,
    read_for_preview,
)
from document_graph.tasks.celery_app import celery_app


router = APIRouter()
logger = logging.getLogger(__name__)


def _storage_root() -> Path:
    return Path("data/workspaces")


def _safe_filename(name: str) -> str:
    return Path(name).name.replace("\\", "_").replace("/", "_")


def _sha256_hex_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_storage_path(*, workspace_id: uuid.UUID, storage_uri: str) -> Path:
    root = (_storage_root() / str(workspace_id) / "raw").resolve()
    raw_path = Path(storage_uri)
    if not raw_path.is_absolute():
        raw_path = (Path.cwd() / raw_path).resolve()
    else:
        raw_path = raw_path.resolve()
    try:
        raw_path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_storage_uri") from exc
    if not raw_path.exists():
        raise HTTPException(status_code=404, detail="document_file_not_found")
    return raw_path


class UploadResponse(BaseModel):
    workspace_id: uuid.UUID
    document_id: uuid.UUID
    task_id: uuid.UUID
    task_status: str


class DocumentListItem(BaseModel):
    id: uuid.UUID
    title: str
    external_key: str
    updated_at: datetime
    latest_task: dict | None = None


class DocumentPreview(BaseModel):
    document_id: uuid.UUID
    title: str
    file_ext: str | None
    mime_type: str | None
    content: str
    content_type: str
    truncated: bool


def _get_or_create_upload_source(db: Session, workspace_id: uuid.UUID) -> Source:
    src = (
        db.query(Source)
        .filter(Source.workspace_id == workspace_id)
        .filter(Source.type == "local_upload")
        .order_by(Source.created_at.asc())
        .first()
    )
    if src is not None:
        return src
    src = Source(workspace_id=workspace_id, type="local_upload", name="Uploads", config={})
    db.add(src)
    db.commit()
    db.refresh(src)
    return src


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    workspace_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = DBDep,
) -> UploadResponse:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace_not_found")

    if not file.filename:
        raise HTTPException(status_code=400, detail="missing_filename")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty_file")

    filename = _safe_filename(file.filename)
    ext = Path(filename).suffix.lower()
    if not is_supported_extension(ext):
        raise HTTPException(status_code=400, detail="unsupported_file_type")
    content_hash = _sha256_hex_bytes(content)

    root = _storage_root() / str(workspace_id) / "raw"
    root.mkdir(parents=True, exist_ok=True)
    out_path = root / filename
    out_path.write_bytes(content)

    source = _get_or_create_upload_source(db, workspace_id)
    external_key = filename

    doc = (
        db.query(Document)
        .filter(Document.workspace_id == workspace_id)
        .filter(Document.source_id == source.id)
        .filter(Document.external_key == external_key)
        .one_or_none()
    )
    if doc is None:
        doc = Document(
            workspace_id=workspace_id,
            source_id=source.id,
            external_key=external_key,
            title=filename,
            status="active",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

    task_type = "document_index"
    idempotency_key = f"{workspace_id}:{doc.id}:{content_hash}:{task_type}"

    existing = db.query(Task).filter(Task.idempotency_key == idempotency_key).one_or_none()
    if existing is not None and existing.status in {"pending", "running", "succeeded"}:
        return UploadResponse(
            workspace_id=workspace_id,
            document_id=doc.id,
            task_id=existing.id,
            task_status=existing.status,
        )

    if existing is None:
        task = Task(
            workspace_id=workspace_id,
            document_id=doc.id,
            type=task_type,
            status="pending",
            stage="persist_meta",
            progress=0.0,
            idempotency_key=idempotency_key,
            input={
                "workspace_id": str(workspace_id),
                "document_id": str(doc.id),
                "storage_path": str(out_path.as_posix()),
                "filename": filename,
                "content_type": file.content_type,
                "content_hash": content_hash,
                "uploaded_at": datetime.utcnow().isoformat(),
            },
        )
        db.add(task)
        db.commit()
        db.refresh(task)
    else:
        task = existing
        task.status = "pending"
        task.stage = "persist_meta"
        task.progress = 0.0
        task.error = {}
        task.updated_at = datetime.utcnow()
        db.add(task)
        db.commit()

    async_result = celery_app.send_task("document_graph.document_index", kwargs={"task_id": str(task.id)})
    task.celery_task_id = async_result.id
    db.add(task)
    db.commit()

    logger.info("document_uploaded workspace_id=%s document_id=%s task_id=%s", workspace_id, doc.id, task.id)
    return UploadResponse(workspace_id=workspace_id, document_id=doc.id, task_id=task.id, task_status=task.status)


@router.get("", response_model=list[DocumentListItem])
def list_documents(
    workspace_id: uuid.UUID,
    limit: int = 10,
    offset: int = 0,
    db: Session = DBDep,
) -> list[DocumentListItem]:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace_not_found")

    limit = max(1, min(int(limit), 50))
    offset = max(0, int(offset))

    docs = (
        db.query(Document)
        .filter(Document.workspace_id == workspace_id)
        .order_by(Document.updated_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    if not docs:
        return []

    doc_ids = [d.id for d in docs]
    tasks = (
        db.query(Task)
        .filter(Task.document_id.in_(doc_ids))
        .order_by(Task.created_at.desc())
        .all()
    )
    latest_by_doc: dict[uuid.UUID, Task] = {}
    for t in tasks:
        if t.document_id and t.document_id not in latest_by_doc:
            latest_by_doc[t.document_id] = t

    out: list[DocumentListItem] = []
    for d in docs:
        lt = latest_by_doc.get(d.id)
        out.append(
            DocumentListItem(
                id=d.id,
                title=d.title,
                external_key=d.external_key,
                updated_at=d.updated_at,
                latest_task=(
                    {
                        "id": str(lt.id),
                        "status": lt.status,
                        "stage": lt.stage,
                        "progress": lt.progress,
                        "error": lt.error,
                        "updated_at": lt.updated_at.isoformat() if lt.updated_at else None,
                    }
                    if lt is not None
                    else None
                ),
            )
        )
    return out


@router.get("/{document_id}/preview", response_model=DocumentPreview)
def preview_document(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    db: Session = DBDep,
) -> DocumentPreview:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace_not_found")

    doc = (
        db.query(Document)
        .filter(Document.id == document_id)
        .filter(Document.workspace_id == workspace_id)
        .one_or_none()
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="document_not_found")

    dv = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version.desc())
        .first()
    )
    if dv is None:
        raise HTTPException(status_code=404, detail="document_version_not_found")

    file_path = _resolve_storage_path(workspace_id=workspace_id, storage_uri=dv.storage_uri)
    try:
        content, content_type = read_for_preview(file_path)
    except UnsupportedDocumentType as exc:
        logger.warning("preview_unsupported document_id=%s error=%s", document_id, exc)
        raise HTTPException(status_code=400, detail="unsupported_file_type") from exc
    except MissingParserDependency as exc:
        logger.warning("preview_missing_dependency document_id=%s error=%s", document_id, exc)
        raise HTTPException(status_code=500, detail="missing_parser_dependency") from exc

    max_chars = 200_000
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]

    mime_type = dv.mime_type or guess_mime_type(file_path, None)
    logger.info("document_preview workspace_id=%s document_id=%s", workspace_id, document_id)
    return DocumentPreview(
        document_id=document_id,
        title=doc.title,
        file_ext=dv.file_ext,
        mime_type=mime_type,
        content=content,
        content_type=content_type,
        truncated=truncated,
    )


@router.get("/{document_id}/download")
def download_document(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    db: Session = DBDep,
) -> FileResponse:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace_not_found")

    doc = (
        db.query(Document)
        .filter(Document.id == document_id)
        .filter(Document.workspace_id == workspace_id)
        .one_or_none()
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="document_not_found")

    dv = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version.desc())
        .first()
    )
    if dv is None:
        raise HTTPException(status_code=404, detail="document_version_not_found")

    file_path = _resolve_storage_path(workspace_id=workspace_id, storage_uri=dv.storage_uri)
    logger.info("document_download workspace_id=%s document_id=%s", workspace_id, document_id)
    return FileResponse(path=file_path, filename=doc.title, media_type="application/octet-stream")
