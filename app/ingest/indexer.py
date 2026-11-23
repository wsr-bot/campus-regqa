"""写入索引：SQLite 事务内先删后插，再同步 Chroma，最后重建知识库版本。"""

from __future__ import annotations

import logging
from typing import Any

from app.ingest.chunker import Chunk
from app.retrieval import store
from app.retrieval.embed import get_embedder
from app.store import repo
from app.store.db import get_conn

log = logging.getLogger(__name__)


def index_chunks(chunks: list[Chunk], *, rebuild_embeddings: bool = True) -> dict[str, Any]:
    if not chunks:
        return {"chunks": 0, "embedded": 0, "version": ""}

    doc_id = chunks[0].doc_id

    with get_conn() as conn:
        count = repo.replace_chunks(conn, doc_id, (c.to_dict() for c in chunks))

    store.delete_by_doc(doc_id)

    embedded = 0
    if rebuild_embeddings:
        embedder = get_embedder()
        texts = [c.text for c in chunks]
        vectors = embedder.embed(texts)
        store.upsert(
            ids=[c.chunk_id for c in chunks],
            embeddings=vectors,
            documents=texts,
            metadatas=[
                {
                    "doc_id": c.doc_id,
                    "article_no": c.article_no,
                    "chapter_no": c.chapter_no,
                    "effective_year": c.effective_year,
                    "is_current": c.is_current,
                    "file_type": c.file_type,
                }
                for c in chunks
            ],
        )
        embedded = len(vectors)

    with get_conn() as conn:
        version = repo.rebuild_knowledge_version(conn)

    log.info("入库完成 doc=%s chunks=%s embedded=%s version=%s", doc_id, count, embedded, version)
    return {"chunks": count, "embedded": embedded, "version": version}
