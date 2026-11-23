"""节点：把模型输出里的 [[chunk_id]] 绑定成可点开的引用。

只做解析与校验，不再调模型。MVP 的取舍：引用缺失不给第二次机会，
直接交给 verify 判定拒答——与其让模型补一个可能编造的标记，不如承认答不出。
"""

from __future__ import annotations

import re
from typing import Any

from app.graph.state import QAState
from app.store import repo
from app.store.db import get_conn

MARK = re.compile(r"\[\[([^\[\]]+)\]\]")


def compose_node(state: QAState) -> dict[str, Any]:
    raw = state.get("raw_answer") or ""
    kept = state.get("kept") or []
    by_id = {c["chunk_id"]: c for c in kept}

    order: list[str] = []
    for cid in MARK.findall(raw):
        cid = cid.strip()
        if cid in by_id and cid not in order:
            order.append(cid)

    # 引用了列表外的 id：只接受库里确实存在、且本次检索到的块，其余一律丢
    with get_conn() as conn:
        rows = repo.get_chunks_by_ids(conn, order)
    row_by_id = {r["chunk_id"]: r for r in rows}

    citations: list[dict[str, Any]] = []
    index_of: dict[str, int] = {}
    for cid in order:
        row = row_by_id.get(cid)
        if row is None:
            continue
        index_of[cid] = len(citations) + 1
        citations.append(
            {
                "chunk_id": cid,
                "doc_id": row["doc_id"],
                "doc_title": row["doc_title"],
                "article_no": row["article_no"],
                "chapter_no": row["chapter_no"],
                "effective_year": row["effective_year"],
                "is_current": bool(row["is_current"]),
                "quote": row["text"][:200],
            }
        )

    def _replace(match: re.Match[str]) -> str:
        n = index_of.get(match.group(1).strip())
        return f"[{n}]" if n else ""

    answer = MARK.sub(_replace, raw).strip()
    answer = re.sub(r"\s{2,}", " ", answer)

    return {"answer_md": answer, "citations": citations}
