"""文件清单接口：让学生和面试官都能看到「库里到底有哪些文件、哪些是现行版」。"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.store import repo
from app.store.db import get_conn

router = APIRouter()


class DocumentItem(BaseModel):
    doc_id: str
    title: str
    effective_year: int
    is_current: bool
    file_type: str
    parse_status: str
    chunk_count: int


@router.get("/api/documents", response_model=list[DocumentItem])
def list_documents(current_only: bool = Query(default=True)) -> list[DocumentItem]:
    with get_conn() as conn:
        rows = repo.list_documents(conn, current_only=current_only)
    return [
        DocumentItem(
            doc_id=r["doc_id"],
            title=r["title"],
            effective_year=r["effective_year"],
            is_current=bool(r["is_current"]),
            file_type=r["file_type"],
            parse_status=r["parse_status"],
            chunk_count=r["chunk_count"] or 0,
        )
        for r in rows
    ]
