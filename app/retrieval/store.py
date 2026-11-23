"""Chroma collection 管理。Chroma 只是索引，正本永远在 SQLite 的 chunks 表。"""

from __future__ import annotations

from typing import Any, Sequence

from app.config import get_settings

COLLECTION = "chunks"


def _client():
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    path = get_settings().chroma_dir
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(path),
        settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
    )


def get_collection():
    return _client().get_or_create_collection(
        name=COLLECTION, metadata={"hnsw:space": "cosine"}
    )


def upsert(
    ids: Sequence[str],
    embeddings: Sequence[Sequence[float]],
    documents: Sequence[str],
    metadatas: Sequence[dict[str, Any]],
) -> None:
    if not ids:
        return
    get_collection().upsert(
        ids=list(ids),
        embeddings=[list(e) for e in embeddings],
        documents=list(documents),
        metadatas=list(metadatas),
    )


def delete_by_doc(doc_id: str) -> None:
    get_collection().delete(where={"doc_id": doc_id})


def query(
    embedding: Sequence[float],
    n_results: int,
    file_types: list[str] | None = None,
    is_current: int | None = 1,
) -> list[dict[str, Any]]:
    """返回 [{chunk_id, score, metadata}]，score 为 cosine 相似度（越大越像）。"""
    conditions: list[dict[str, Any]] = []
    if is_current is not None:
        conditions.append({"is_current": is_current})
    if file_types:
        conditions.append({"file_type": {"$in": file_types}})

    where: dict[str, Any] | None = None
    if len(conditions) == 1:
        where = conditions[0]
    elif len(conditions) > 1:
        where = {"$and": conditions}

    res = get_collection().query(
        query_embeddings=[list(embedding)],
        n_results=n_results,
        where=where,
        include=["metadatas", "distances"],
    )

    ids = (res.get("ids") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]

    out: list[dict[str, Any]] = []
    for cid, meta, dist in zip(ids, metas, dists):
        out.append(
            {
                "chunk_id": cid,
                "score": round(1.0 - float(dist), 4),  # cosine 距离转相似度
                "metadata": meta or {},
            }
        )
    return out


def count() -> int:
    try:
        return get_collection().count()
    except Exception:
        return 0


def reset() -> None:
    """仅开发期使用：清空整个 collection。"""
    client = _client()
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    client.get_or_create_collection(name=COLLECTION, metadata={"hnsw:space": "cosine"})
