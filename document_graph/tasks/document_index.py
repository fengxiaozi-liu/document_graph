from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from celery.utils.log import get_task_logger
from qdrant_client import QdrantClient
from sqlalchemy import func
from sqlalchemy.orm import Session

import logging

from document_graph.chunking import iter_chunks_for_file
from document_graph.config import load_app_config
from document_graph.db.engine import create_engine_and_sessionmaker
from document_graph.db.models import Chunk, Document, DocumentVersion, Task, Workspace
from document_graph.document_parsing import MissingParserDependency, UnsupportedDocumentType, guess_mime_type
from document_graph.openai_compat import OpenAICompatClient
from document_graph.settings import load_settings
from document_graph.tasks.celery_app import celery_app
from document_graph.vectorstore.qdrant_admin import ensure_alias
from document_graph.vectorstore.qdrant_index import delete_by_doc_version, ensure_collection, to_distance, upsert_points


logger = get_task_logger(__name__)
std_logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _sha256_hex_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _db_session() -> Session:
    settings = load_settings()
    _engine, SessionLocal = create_engine_and_sessionmaker(settings)
    return SessionLocal()


def _set_task(
    db: Session,
    task: Task,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress: float | None = None,
    error: dict | None = None,
    result: dict | None = None,
    finished_at: datetime | None = None,
) -> None:
    if status is not None:
        task.status = status
    if stage is not None:
        task.stage = stage
    if progress is not None:
        task.progress = progress
    if error is not None:
        task.error = error
    if result is not None:
        task.result = result
    if finished_at is not None:
        task.finished_at = finished_at
    task.updated_at = _utcnow()
    db.add(task)
    db.commit()


def _next_version(db: Session, document_id: uuid.UUID) -> int:
    max_version = db.query(func.max(DocumentVersion.version)).filter(DocumentVersion.document_id == document_id).scalar()
    return int(max_version or 0) + 1


@celery_app.task(name="document_graph.document_index", bind=True)
def document_index(self, *, task_id: str) -> None:
    db = _db_session()
    try:
        task = db.query(Task).filter(Task.id == uuid.UUID(task_id)).one_or_none()
        if task is None:
            raise RuntimeError(f"task_not_found: {task_id}")
        std_logger.info("document_index_start task_id=%s", task_id)
        task.celery_task_id = self.request.id
        task.started_at = _utcnow()
        task.status = "running"
        task.stage = "persist_meta"
        task.attempt = int(task.attempt or 0) + 1
        task.updated_at = _utcnow()
        db.add(task)
        db.commit()

        payload = task.input or {}
        workspace_id = uuid.UUID(str(payload["workspace_id"]))
        document_id = uuid.UUID(str(payload["document_id"]))
        storage_path = Path(str(payload["storage_path"]))
        content_type = payload.get("content_type")

        ws = db.query(Workspace).filter(Workspace.id == workspace_id).one()
        doc = db.query(Document).filter(Document.id == document_id).one()

        raw = _read_bytes(storage_path)
        content_hash = _sha256_hex_bytes(raw)
        file_ext = storage_path.suffix.lower().lstrip(".") or None
        mime_type = guess_mime_type(storage_path, content_type)

        previous_version = (
            db.query(DocumentVersion).filter(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version.desc()).first()
        )
        if previous_version is not None and previous_version.content_hash == content_hash:
            _set_task(db, task, status="succeeded", stage=None, progress=1.0, result={"skipped": "same_content_hash"}, finished_at=_utcnow())
            return

        version_num = _next_version(db, doc.id)

        dv = DocumentVersion(
            document_id=doc.id,
            version=version_num,
            content_hash=content_hash,
            size_bytes=len(raw),
            mtime=datetime.fromtimestamp(storage_path.stat().st_mtime) if storage_path.exists() else None,
            storage_uri=str(storage_path.as_posix()),
            mime_type=mime_type,
            file_ext=file_ext,
        )
        db.add(dv)
        db.commit()
        db.refresh(dv)

        _set_task(db, task, stage="chunk", progress=0.25)
        std_logger.info("document_index_chunk_start task_id=%s document_id=%s", task_id, doc.id)

        cfg = load_app_config()
        chunks = []
        try:
            for i, c in enumerate(iter_chunks_for_file(storage_path, chunking=cfg.chunking)):
                chunk_uid = f"chunk_{doc.id.hex}_{version_num}_{i}"
                chunks.append(
                    Chunk(
                        document_version_id=dv.id,
                        chunk_index=i,
                        chunk_uid=chunk_uid,
                        title_path=c.title_path,
                        offset_start=c.offset_start,
                        offset_end=c.offset_end,
                        text=c.text,
                        text_hash=_sha256_hex_bytes(c.text.encode("utf-8", errors="replace")),
                    )
                )
        except (UnsupportedDocumentType, MissingParserDependency) as exc:
            raise RuntimeError(str(exc)) from exc
        if not chunks:
            raise RuntimeError("no_chunks_produced")

        for row in chunks:
            db.add(row)
        db.commit()
        std_logger.info("document_index_chunk_done task_id=%s chunks=%s", task_id, len(chunks))

        _set_task(db, task, stage="embedding_upsert", progress=0.55)
        std_logger.info("document_index_embedding_start task_id=%s", task_id)

        embed = OpenAICompatClient(base_url=cfg.embedding.base_url, api_key=cfg.embedding.api_key)
        qdrant = QdrantClient(url=cfg.qdrant.url)

        # Probe vector size and ensure collection exists.
        probe_vec = embed.embeddings(model=cfg.embedding.model, inputs=[chunks[0].text])[0]
        ensure_collection(
            qdrant,
            collection=ws.qdrant_collection,
            vector_size=len(probe_vec),
            distance=to_distance(cfg.qdrant.distance),
        )

        # Optional: ensure alias points to collection (only if alias exists).
        if ws.qdrant_alias:
            ensure_alias(qdrant, alias=ws.qdrant_alias, collection=ws.qdrant_collection)

        batch_size = int(cfg.embedding.batch_size)
        indexed = 0
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            texts = [b.text for b in batch]
            vectors = embed.embeddings(model=cfg.embedding.model, inputs=texts)
            chunk_uids = [b.chunk_uid for b in batch]
            payloads = [
                {
                    "chunk_uid": b.chunk_uid,
                    "document_id": str(doc.id),
                    "document_version_id": str(dv.id),
                    "offset_start": b.offset_start,
                    "offset_end": b.offset_end,
                }
                for b in batch
            ]
            upsert_points(qdrant, collection=ws.qdrant_collection, vectors=vectors, payloads=payloads, chunk_uids=chunk_uids)
            indexed += len(batch)

        _set_task(db, task, stage="delete_old", progress=0.9, result={"indexed_chunks": indexed})
        std_logger.info("document_index_embedding_done task_id=%s indexed_chunks=%s", task_id, indexed)

        if previous_version is not None:
            delete_by_doc_version(
                qdrant,
                collection=ws.qdrant_collection,
                document_id=str(doc.id),
                document_version_id=str(previous_version.id),
            )

        _set_task(db, task, status="succeeded", stage=None, progress=1.0, finished_at=_utcnow())
        std_logger.info("document_index_done task_id=%s", task_id)
    except Exception as e:
        try:
            task_obj = None
            try:
                task_obj = db.query(Task).filter(Task.id == uuid.UUID(task_id)).one_or_none()
            except Exception:
                task_obj = None
            if task_obj is not None:
                _set_task(
                    db,
                    task_obj,
                    status="failed",
                    error={"message": str(e), "type": type(e).__name__},
                )
        finally:
            logger.exception("document_index failed")
        raise
    finally:
        db.close()
