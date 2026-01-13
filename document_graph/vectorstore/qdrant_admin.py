from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm


def qdrant_client(url: str) -> QdrantClient:
    return QdrantClient(url=url)


def collection_exists(client: QdrantClient, collection: str) -> bool:
    collections = client.get_collections().collections
    return any(c.name == collection for c in collections)


def drop_collection_if_exists(client: QdrantClient, collection: str) -> None:
    if collection_exists(client, collection):
        client.delete_collection(collection_name=collection)


def drop_alias_if_exists(client: QdrantClient, alias: str) -> None:
    try:
        aliases = client.get_aliases().aliases
    except Exception:
        return
    if any(a.alias_name == alias for a in aliases):
        client.update_collection_aliases(
            change_aliases=[qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=alias))]
        )


def ensure_alias(client: QdrantClient, *, alias: str, collection: str) -> None:
    # Qdrant requires alias to be unique; simplest MVP behavior is "replace".
    drop_alias_if_exists(client, alias)
    client.update_collection_aliases(
        change_aliases=[
            qm.CreateAliasOperation(create_alias=qm.CreateAlias(collection_name=collection, alias_name=alias))
        ]
    )
