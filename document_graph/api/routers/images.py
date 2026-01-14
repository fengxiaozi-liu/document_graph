from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from sqlalchemy.orm import Session

from document_graph.api.deps import DBDep
from document_graph.config import load_app_config
from document_graph.db.models import Document, Workspace
from document_graph.multimodal import text_embedding


router = APIRouter()


class ImageSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = 12


class ImageSearchItem(BaseModel):
    score: float
    chunk_uid: str
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    document_title: str | None = None


class ImageSearchResponse(BaseModel):
    items: list[ImageSearchItem]


@router.post("/workspaces/{workspace_id}/images/search", response_model=ImageSearchResponse)
def search_images(workspace_id: uuid.UUID, payload: ImageSearchRequest, db: Session = DBDep) -> ImageSearchResponse:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace_not_found")

    cfg = load_app_config()
    if not cfg.multimodal.enabled:
        raise HTTPException(status_code=400, detail="multimodal_disabled")

    qvec = text_embedding(payload.query)
    qc = QdrantClient(url=cfg.qdrant.url)
    limit = max(1, min(int(payload.top_k), 50))
    hits = qc.search(
        collection_name=ws.qdrant_collection,
        query_vector=("image", qvec),
        limit=limit,
        with_payload=True,
        query_filter=qm.Filter(must=[qm.FieldCondition(key="modality", match=qm.MatchValue(value="image"))]),
    )
    items: list[ImageSearchItem] = []
    doc_ids: list[uuid.UUID] = []
    for h in hits:
        payload = h.payload or {}
        try:
            doc_id = uuid.UUID(str(payload.get("document_id") or ""))
            dv_id = uuid.UUID(str(payload.get("document_version_id") or ""))
        except Exception:
            continue
        cu = str(payload.get("chunk_uid") or "")
        if not cu:
            continue
        doc_ids.append(doc_id)
        items.append(
            ImageSearchItem(
                score=float(getattr(h, "score", 0.0) or 0.0),
                chunk_uid=cu,
                document_id=doc_id,
                document_version_id=dv_id,
                document_title=None,
            )
        )

    if items:
        docs = db.query(Document).filter(Document.id.in_(doc_ids)).all()
        by_id = {d.id: d for d in docs}
        for it in items:
            d = by_id.get(it.document_id)
            if d is not None:
                it.document_title = d.title

    return ImageSearchResponse(items=items)

