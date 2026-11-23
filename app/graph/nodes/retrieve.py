"""节点：向量召回。

先按 file_type 圈范围，再向量召回，最后按单文件上限截流（防止一份文件霸屏）。
块正文以 SQLite 为正本，Chroma 只给 id 与分数。
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.graph.state import QAState
from app.retrieval import store
from app.retrieval.embed import get_embedder
from app.store import repo
from app.store.db import get_conn

log = logging.getLogger(__name__)


def retrieve_node(state: QAState) -> dict[str, Any]:
    cfg = get_settings()
    top_k = int(cfg.get("retrieval.top_k", 20))
    cap = int(cfg.get("retrieval.per_file_cap", 3))
    year_filter = state.get("year_filter")

    try:
        vector = get_embedder().embed_one(state["question"])
    except Exception as exc:
        log.error("embedding 失败：%s", exc)
        return {"candidates": [], "recalled": 0, "top_score": 0.0, "error": f"EMBED_FAILED: {exc}"}

    is_current = None if year_filter else 1
    hits = store.query(
        embedding=vector,
        n_results=top_k,
        file_types=state.get("file_types") or None,
        is_current=is_current,
    )

    # 按年份过滤旧版条款：「去年是不是这样」的场景
    if year_filter:
        hits = [h for h in hits if int(h["metadata"].get("effective_year", 0)) <= year_filter]

    per_doc: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    for h in hits:
        doc_id = h["metadata"].get("doc_id", "")
        if per_doc.get(doc_id, 0) >= cap:
            continue
        per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
        kept.append(h)

    with get_conn() as conn:
        rows = repo.get_chunks_by_ids(conn, [k["chunk_id"] for k in kept])

    text_by_id = {r["chunk_id"]: r["text"] for r in rows}
    docs_by_id = {r["chunk_id"]: r["doc_title"] for r in rows}
    art_by_id = {r["chunk_id"]: r["article_no"] for r in rows}

    candidates = []
    for k in kept:
        cid = k["chunk_id"]
        if cid not in text_by_id:
            continue  # Chroma 有、SQLite 无 = 索引脏了，宁可丢掉
        candidates.append(
            {
                "chunk_id": cid,
                "score": k["score"],
                "doc_id": k["metadata"].get("doc_id", ""),
                "doc_title": docs_by_id.get(cid, ""),
                "article_no": art_by_id.get(cid, ""),
                "chapter_no": k["metadata"].get("chapter_no", ""),
                "effective_year": int(k["metadata"].get("effective_year", 0) or 0),
                "file_type": k["metadata"].get("file_type", ""),
                "text": text_by_id[cid],
            }
        )

    top_score = candidates[0]["score"] if candidates else 0.0
    return {
        "candidates": candidates,
        "recalled": len(candidates),
        "top_score": top_score,
    }
