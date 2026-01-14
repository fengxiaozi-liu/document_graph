from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from sqlalchemy.orm import Session

from document_graph.api.deps import DBDep
from document_graph.config import load_app_config
from document_graph.db.models import Document, DocumentVersion, Source, Task, Workspace
from document_graph.document_parsing import (
    MissingParserDependency,
    UnsupportedDocumentType,
    guess_mime_type,
    is_supported_extension,
    read_for_preview,
)
from document_graph.tasks.celery_app import celery_app
from document_graph.vectorstore.qdrant_index import delete_by_document


router = APIRouter()
logger = logging.getLogger(__name__)


def _storage_root() -> Path:
    return Path("data/workspaces")


def _safe_filename(name: str) -> str:
    return Path(name).name.replace("\\", "_").replace("/", "_")


def _safe_relative_path(path: str) -> str:
    raw = (path or "").strip().replace("\\", "/")
    if not raw:
        raise HTTPException(status_code=400, detail="invalid_relative_path")
    if raw.startswith("/") or raw.startswith("\\"):
        raise HTTPException(status_code=400, detail="invalid_relative_path")
    if len(raw) >= 2 and raw[1] == ":":
        raise HTTPException(status_code=400, detail="invalid_relative_path")
    parts = [p for p in raw.split("/") if p not in {"", "."}]
    if any(p == ".." for p in parts):
        raise HTTPException(status_code=400, detail="invalid_relative_path")
    safe = "/".join(parts)
    if not safe:
        raise HTTPException(status_code=400, detail="invalid_relative_path")
    return safe


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


def _best_effort_unlink(path: Path) -> None:
    try:
        if path.exists() and path.is_file():
            path.unlink()
    except Exception:
        return


def _best_effort_rmdir_empty_dirs(path: Path, *, stop_at: Path) -> None:
    try:
        cur = path.parent
        stop_at = stop_at.resolve()
        while True:
            cur = cur.resolve()
            if cur == stop_at or stop_at not in cur.parents and cur != stop_at:
                break
            try:
                cur.rmdir()
            except Exception:
                break
            cur = cur.parent
    except Exception:
        return


class UploadResponse(BaseModel):
    workspace_id: uuid.UUID
    document_id: uuid.UUID
    task_id: uuid.UUID
    task_status: str


class UploadManyItem(BaseModel):
    relative_path: str
    document_id: uuid.UUID
    task_id: uuid.UUID
    task_status: str


class UploadManyResponse(BaseModel):
    workspace_id: uuid.UUID
    items: list[UploadManyItem]


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
    download_url: str
    view_url: str


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


@router.post("/upload_many", response_model=UploadManyResponse)
async def upload_many(
    workspace_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    relative_paths: list[str] = Form(...),
    db: Session = DBDep,
) -> UploadManyResponse:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace_not_found")
    if not files:
        raise HTTPException(status_code=400, detail="empty_files")
    if len(files) != len(relative_paths):
        raise HTTPException(status_code=400, detail="mismatched_files_and_paths")

    root = _storage_root() / str(workspace_id) / "raw"
    root.mkdir(parents=True, exist_ok=True)
    root_resolved = root.resolve()

    source = _get_or_create_upload_source(db, workspace_id)

    out_items: list[UploadManyItem] = []
    for file, rel in zip(files, relative_paths):
        if not file.filename:
            raise HTTPException(status_code=400, detail="missing_filename")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="empty_file")

        rel_path = _safe_relative_path(rel)
        ext = Path(rel_path).suffix.lower()
        if not is_supported_extension(ext):
            raise HTTPException(status_code=400, detail=f"unsupported_file_type: {ext or 'unknown'}")

        out_path = (root / rel_path).resolve()
        try:
            out_path.relative_to(root_resolved)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid_relative_path") from exc
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(content)

        content_hash = _sha256_hex_bytes(content)
        external_key = rel_path
        title = Path(rel_path).name

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
                title=title,
                status="active",
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)

        task_type = "document_index"
        idempotency_key = f"{workspace_id}:{doc.id}:{content_hash}:{task_type}"
        existing = db.query(Task).filter(Task.idempotency_key == idempotency_key).one_or_none()
        if existing is not None and existing.status in {"pending", "running", "succeeded"}:
            out_items.append(
                UploadManyItem(
                    relative_path=rel_path,
                    document_id=doc.id,
                    task_id=existing.id,
                    task_status=existing.status,
                )
            )
            continue

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
                    "filename": title,
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

        out_items.append(
            UploadManyItem(
                relative_path=rel_path,
                document_id=doc.id,
                task_id=task.id,
                task_status=task.status,
            )
        )

    logger.info("documents_uploaded_many workspace_id=%s count=%s", workspace_id, len(out_items))
    return UploadManyResponse(workspace_id=workspace_id, items=out_items)


class DocumentTreeNode(BaseModel):
    type: str
    name: str
    path: str
    children: list["DocumentTreeNode"] | None = None
    document: dict[str, Any] | None = None


DocumentTreeNode.model_rebuild()


@router.get("/tree", response_model=DocumentTreeNode)
def get_document_tree(
    workspace_id: uuid.UUID,
    prefix: str | None = None,
    db: Session = DBDep,
) -> DocumentTreeNode:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace_not_found")

    q = db.query(Document).filter(Document.workspace_id == workspace_id)
    if prefix:
        safe_prefix = _safe_relative_path(prefix)
        q = q.filter(Document.external_key.startswith(safe_prefix))
    docs = q.order_by(Document.external_key.asc()).all()
    if not docs:
        return DocumentTreeNode(type="folder", name="", path="", children=[])

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

    root = {"type": "folder", "name": "", "path": "", "children": {}}

    def ensure_folder(parent: dict, name: str, path: str) -> dict:
        children = parent.setdefault("children", {})
        node = children.get(name)
        if node is None:
            node = {"type": "folder", "name": name, "path": path, "children": {}}
            children[name] = node
        return node

    for d in docs:
        parts = [p for p in d.external_key.replace("\\", "/").split("/") if p]
        parent = root
        current_path = ""
        for seg in parts[:-1]:
            current_path = f"{current_path}/{seg}" if current_path else seg
            parent = ensure_folder(parent, seg, current_path)
        filename = parts[-1] if parts else d.external_key
        lt = latest_by_doc.get(d.id)
        file_node = {
            "type": "file",
            "name": filename,
            "path": d.external_key,
            "document": {
                "id": str(d.id),
                "title": d.title,
                "external_key": d.external_key,
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
                "latest_task": (
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
            },
        }
        parent.setdefault("children", {})[filename] = file_node

    def to_node(obj: dict) -> DocumentTreeNode:
        if obj["type"] == "file":
            return DocumentTreeNode(
                type="file",
                name=obj["name"],
                path=obj["path"],
                children=None,
                document=obj.get("document"),
            )
        children_map: dict[str, dict] = obj.get("children", {}) or {}
        children_nodes = [to_node(children_map[k]) for k in sorted(children_map.keys())]
        return DocumentTreeNode(type="folder", name=obj["name"], path=obj["path"], children=children_nodes)

    return to_node(root)

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
    image_exts = {".png", ".jpg", ".jpeg", ".webp"}
    if file_path.suffix.lower() in image_exts:
        content = ""
        content_type = "image"
        truncated = False
        mime_type = dv.mime_type or guess_mime_type(file_path, None)
        return DocumentPreview(
            document_id=document_id,
            title=doc.title,
            file_ext=dv.file_ext,
            mime_type=mime_type,
            content=content,
            content_type=content_type,
            truncated=truncated,
            download_url=f"/workspaces/{workspace_id}/documents/{document_id}/download",
            view_url=f"/workspaces/{workspace_id}/documents/{document_id}/view",
        )

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
        download_url=f"/workspaces/{workspace_id}/documents/{document_id}/download",
        view_url=f"/workspaces/{workspace_id}/documents/{document_id}/view",
    )


@router.get("/{document_id}/view")
def view_document(
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
    mime_type = dv.mime_type or guess_mime_type(file_path, None) or "application/octet-stream"
    logger.info("document_view workspace_id=%s document_id=%s", workspace_id, document_id)
    # Important: no filename => no attachment Content-Disposition; enables inline preview (pdf/img/etc).
    return FileResponse(path=file_path, media_type=mime_type)


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
    mime_type = dv.mime_type or guess_mime_type(file_path, None) or "application/octet-stream"
    logger.info("document_download workspace_id=%s document_id=%s", workspace_id, document_id)
    return FileResponse(path=file_path, filename=doc.title, media_type=mime_type)


class DeleteManyRequest(BaseModel):
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    prefixes: list[str] = Field(default_factory=list)


class DeleteManyResponse(BaseModel):
    deleted_documents: int
    deleted_files: int
    deleted_vectors: int
    revoked_tasks: int


def _revoke_task_if_possible(task: Task) -> bool:
    try:
        if task.celery_task_id and task.status in {"pending", "running"}:
            celery_app.control.revoke(task.celery_task_id, terminate=False)
            return True
    except Exception:
        return False
    return False


def _delete_documents(
    db: Session,
    *,
    workspace: Workspace,
    documents: list[Document],
) -> DeleteManyResponse:
    if not documents:
        return DeleteManyResponse(deleted_documents=0, deleted_files=0, deleted_vectors=0, revoked_tasks=0)

    cfg = load_app_config()
    qdrant = QdrantClient(url=cfg.qdrant.url, timeout=2.0)

    doc_ids = [d.id for d in documents]
    versions = db.query(DocumentVersion).filter(DocumentVersion.document_id.in_(doc_ids)).all()
    tasks = db.query(Task).filter(Task.document_id.in_(doc_ids)).all()

    storage_root = (_storage_root() / str(workspace.id) / "raw").resolve()

    deleted_files = 0
    for dv in versions:
        try:
            p = _resolve_storage_path(workspace_id=workspace.id, storage_uri=dv.storage_uri)
        except HTTPException:
            continue
        _best_effort_unlink(p)
        deleted_files += 1
        _best_effort_rmdir_empty_dirs(p, stop_at=storage_root)

    revoked = 0
    for t in tasks:
        if _revoke_task_if_possible(t):
            revoked += 1

    # Important: tasks.document_id has FK without ON DELETE CASCADE, so tasks must be removed first.
    db.query(Task).filter(Task.document_id.in_(doc_ids)).delete(synchronize_session=False)
    db.flush()

    deleted_vectors = 0
    for d in documents:
        try:
            delete_by_document(qdrant, collection=workspace.qdrant_collection, document_id=str(d.id))
            deleted_vectors += 1
        except Exception:
            # Best-effort: DB remains source of truth; vectors can be cleaned by later reconcile.
            logger.warning("qdrant_delete_failed workspace_id=%s document_id=%s", workspace.id, d.id)

        db.delete(d)

    db.commit()
    logger.info(
        "documents_deleted workspace_id=%s deleted_documents=%s deleted_files=%s deleted_vectors=%s",
        workspace.id,
        len(documents),
        deleted_files,
        deleted_vectors,
    )
    return DeleteManyResponse(
        deleted_documents=len(documents),
        deleted_files=deleted_files,
        deleted_vectors=deleted_vectors,
        revoked_tasks=revoked,
    )


@router.delete("/by_prefix", response_model=DeleteManyResponse)
def delete_by_prefix(
    workspace_id: uuid.UUID,
    prefix: str,
    db: Session = DBDep,
) -> DeleteManyResponse:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace_not_found")

    safe_prefix = _safe_relative_path(prefix)
    folder_prefix = safe_prefix.rstrip("/") + "/"
    docs = (
        db.query(Document)
        .filter(Document.workspace_id == workspace_id)
        .filter(Document.external_key.startswith(folder_prefix))
        .all()
    )
    return _delete_documents(db, workspace=ws, documents=docs)


@router.post("/delete_many", response_model=DeleteManyResponse)
def delete_many(
    workspace_id: uuid.UUID,
    payload: DeleteManyRequest,
    db: Session = DBDep,
) -> DeleteManyResponse:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace_not_found")

    doc_ids = list(dict.fromkeys(payload.document_ids or []))
    prefixes = [p for p in (payload.prefixes or []) if p]

    docs: list[Document] = []
    if doc_ids:
        docs.extend(
            db.query(Document)
            .filter(Document.workspace_id == workspace_id)
            .filter(Document.id.in_(doc_ids))
            .all()
        )

    for p in prefixes:
        safe_prefix = _safe_relative_path(p)
        folder_prefix = safe_prefix.rstrip("/") + "/"
        docs.extend(
            db.query(Document)
            .filter(Document.workspace_id == workspace_id)
            .filter(Document.external_key.startswith(folder_prefix))
            .all()
        )

    # De-dup
    by_id: dict[uuid.UUID, Document] = {d.id: d for d in docs}
    return _delete_documents(db, workspace=ws, documents=list(by_id.values()))


@router.delete("/{document_id}", response_model=DeleteManyResponse)
def delete_document(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    db: Session = DBDep,
) -> DeleteManyResponse:
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
    return _delete_documents(db, workspace=ws, documents=[doc])
