from __future__ import annotations

import uuid

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm


def to_distance(name: str) -> qm.Distance:
    name = name.lower()
    if name == "cosine":
        return qm.Distance.COSINE
    if name == "dot":
        return qm.Distance.DOT
    if name in {"euclid", "euclidean", "l2"}:
        return qm.Distance.EUCLID
    raise ValueError(f"unsupported distance: {name}")


def ensure_collection(
    client: QdrantClient,
    *,
    collection: str,
    vector_size: int,
    distance: qm.Distance,
    named_vectors: dict[str, int] | None = None,
) -> None:
    collections = client.get_collections().collections
    if any(c.name == collection for c in collections):
        info = client.get_collection(collection)
        vectors = info.config.params.vectors
        if named_vectors:
            if isinstance(vectors, qm.VectorParams) or (isinstance(vectors, dict) and "default" in vectors):
                raise RuntimeError(f"collection {collection} is single-vector; expected named vectors")
            if not isinstance(vectors, dict):
                raise RuntimeError(f"unsupported vectors config for collection {collection}: {vectors}")
            for name, size in named_vectors.items():
                if name not in vectors:
                    raise RuntimeError(f"collection {collection} missing vector name: {name}")
                vp = vectors[name]
                if vp.size != size:
                    raise RuntimeError(f"collection {collection} vector size mismatch for {name}: existing={vp.size}, new={size}")
                if vp.distance != distance:
                    raise RuntimeError(
                        f"collection {collection} distance mismatch for {name}: existing={vp.distance}, new={distance}"
                    )
        else:
            if isinstance(vectors, qm.VectorParams):
                existing_size = vectors.size
                existing_distance = vectors.distance
            elif isinstance(vectors, dict) and "default" in vectors:
                existing_size = vectors["default"].size
                existing_distance = vectors["default"].distance
            else:
                raise RuntimeError(f"unsupported vectors config for collection {collection}: {vectors}")

            if existing_size != vector_size:
                raise RuntimeError(
                    f"collection {collection} vector size mismatch: existing={existing_size}, new={vector_size}"
                )
            if existing_distance != distance:
                raise RuntimeError(
                    f"collection {collection} distance mismatch: existing={existing_distance}, new={distance}"
                )
        return

    if named_vectors:
        vectors_config: dict[str, qm.VectorParams] = {
            name: qm.VectorParams(size=int(size), distance=distance) for name, size in named_vectors.items()
        }
    else:
        vectors_config = qm.VectorParams(size=vector_size, distance=distance)

    client.create_collection(
        collection_name=collection,
        vectors_config=vectors_config,
    )
    client.create_payload_index(collection_name=collection, field_name="chunk_uid", field_schema=qm.PayloadSchemaType.KEYWORD)
    client.create_payload_index(collection_name=collection, field_name="document_id", field_schema=qm.PayloadSchemaType.KEYWORD)
    client.create_payload_index(
        collection_name=collection, field_name="document_version_id", field_schema=qm.PayloadSchemaType.KEYWORD
    )
    client.create_payload_index(collection_name=collection, field_name="modality", field_schema=qm.PayloadSchemaType.KEYWORD)


def stable_point_id(chunk_uid: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_uid))


def upsert_points(
    client: QdrantClient,
    *,
    collection: str,
    vectors: list[list[float]],
    payloads: list[dict],
    chunk_uids: list[str],
    vector_name: str | None = None,
) -> None:
    points = []
    for vec, payload, chunk_uid in zip(vectors, payloads, chunk_uids):
        vector: list[float] | dict[str, list[float]]
        if vector_name:
            vector = {vector_name: vec}
        else:
            vector = vec
        points.append(qm.PointStruct(id=stable_point_id(chunk_uid), vector=vector, payload=payload))
    client.upsert(collection_name=collection, points=points)


def delete_by_doc_version(
    client: QdrantClient,
    *,
    collection: str,
    document_id: str,
    document_version_id: str,
) -> None:
    client.delete(
        collection_name=collection,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(
                must=[
                    qm.FieldCondition(key="document_id", match=qm.MatchValue(value=document_id)),
                    qm.FieldCondition(key="document_version_id", match=qm.MatchValue(value=document_version_id)),
                ]
            )
        ),
    )


def delete_by_document(
    client: QdrantClient,
    *,
    collection: str,
    document_id: str,
) -> None:
    client.delete(
        collection_name=collection,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(
                must=[qm.FieldCondition(key="document_id", match=qm.MatchValue(value=document_id))]
            )
        ),
    )

