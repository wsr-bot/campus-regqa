"""健康检查：报告知识库规模与各档模型是否配了密钥。

不探活模型（会烧钱），只报「配没配」，让运维一眼看出问题在哪。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.llm.providers import is_configured
from app.retrieval.store import count as chroma_count
from app.store import repo
from app.store.db import get_conn

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    with get_conn() as conn:
        stats = repo.channel_stats(conn)
        version = repo.knowledge_version(conn)

    llm = {
        "anthropic": is_configured("anthropic"),
        "deepseek": is_configured("deepseek"),
        "qwen": is_configured("qwen"),
    }
    degraded = stats["chunks"] == 0 or not any(llm.values())

    return {
        "status": "degraded" if degraded else "ok",
        "db": {"docs": stats["docs_total"], "docs_current": stats["docs_current"], "chunks": stats["chunks"]},
        "chroma_chunks": chroma_count(),
        "llm": llm,
        "knowledge_version": version,
    }
