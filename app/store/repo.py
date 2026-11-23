"""纯 SQL 读写。不放业务判断，不做跨表推导（推导在 ingest/manifest.py）。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from app.store.db import get_conn


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- documents

def upsert_document(conn: sqlite3.Connection, doc: dict[str, Any]) -> None:
    cols = [
        "doc_id", "title", "title_key", "doc_no", "publisher", "publish_date",
        "effective_year", "expire_year", "is_current", "supersedes", "superseded_by",
        "file_type", "file_path", "file_hash", "page_count", "parse_status",
        "chunk_count", "updated_at",
    ]
    values = [doc.get(c) for c in cols]
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "doc_id")
    conn.execute(
        f"INSERT INTO documents ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(doc_id) DO UPDATE SET {updates}",
        values,
    )


def get_document(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()


def list_documents(conn: sqlite3.Connection, current_only: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM documents"
    if current_only:
        sql += " WHERE is_current=1"
    sql += " ORDER BY file_type, effective_year DESC"
    return conn.execute(sql).fetchall()


def find_current_by_title_key(conn: sqlite3.Connection, title_key: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM documents WHERE title_key=? AND is_current=1", (title_key,)
    ).fetchall()


def set_current_flag(conn: sqlite3.Connection, doc_id: str, is_current: int) -> None:
    conn.execute(
        "UPDATE documents SET is_current=?, updated_at=? WHERE doc_id=?",
        (is_current, now_iso(), doc_id),
    )


# ---------------------------------------------------------------- chunks

def replace_chunks(conn: sqlite3.Connection, doc_id: str, chunks: Iterable[dict[str, Any]]) -> int:
    """事务内先删后插，保证重入库幂等。返回写入条数。"""
    conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    rows = list(chunks)
    conn.executemany(
        """INSERT INTO chunks
           (chunk_id, doc_id, chapter_no, article_no, part_no, seq, text, text_hash,
            page_from, page_to, effective_year, is_current, file_type, merged_from, token_len)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                c["chunk_id"], c["doc_id"], c.get("chapter_no", ""), c.get("article_no", ""),
                c.get("part_no", 1), c["seq"], c["text"], c["text_hash"],
                c.get("page_from"), c.get("page_to"), c["effective_year"], c["is_current"],
                c["file_type"], c.get("merged_from"), c.get("token_len"),
            )
            for c in rows
        ],
    )
    conn.execute(
        "UPDATE documents SET chunk_count=?, updated_at=? WHERE doc_id=?",
        (len(rows), now_iso(), doc_id),
    )
    return len(rows)


def get_chunks_by_ids(conn: sqlite3.Connection, chunk_ids: list[str]) -> list[sqlite3.Row]:
    if not chunk_ids:
        return []
    marks = ",".join("?" for _ in chunk_ids)
    rows = conn.execute(
        f"""SELECT c.*, d.title AS doc_title FROM chunks c
            JOIN documents d ON d.doc_id=c.doc_id
            WHERE c.chunk_id IN ({marks})""",
        chunk_ids,
    ).fetchall()
    order = {cid: i for i, cid in enumerate(chunk_ids)}
    return sorted(rows, key=lambda r: order.get(r["chunk_id"], 999))


# ---------------------------------------------------------------- meta / version

def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def rebuild_knowledge_version(conn: sqlite3.Connection) -> str:
    """知识库版本 = 块数与块内容哈希的摘要，任何入库都会改变它。"""
    row = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(text)),0) AS chars FROM chunks").fetchone()
    h = hashlib.sha256(f"{row['n']}:{row['chars']}".encode()).hexdigest()[:12]
    set_meta(conn, "knowledge_version", h)
    set_meta(conn, "knowledge_updated_at", now_iso())
    return h


def knowledge_version(conn: sqlite3.Connection) -> str:
    return get_meta(conn, "knowledge_version", "empty")


# ---------------------------------------------------------------- sessions / messages

def touch_session(conn: sqlite3.Connection, sid: str, ip_hash: str | None = None) -> None:
    ts = now_iso()
    conn.execute(
        """INSERT INTO sessions (sid, created_at, last_active_at, client_ip_hash)
           VALUES (?,?,?,?)
           ON CONFLICT(sid) DO UPDATE SET last_active_at=excluded.last_active_at""",
        (sid, ts, ts, ip_hash),
    )


def insert_message(conn: sqlite3.Connection, msg: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO messages
           (message_id, sid, role, content, qtype, refused, refusal_reason,
            citation_chunk_ids, tool_calls, knowledge_version, elapsed_ms, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            msg["message_id"], msg["sid"], msg.get("role", "assistant"), msg["content"],
            msg.get("qtype"), int(bool(msg.get("refused"))), msg.get("refusal_reason"),
            json.dumps(msg.get("citation_chunk_ids", []), ensure_ascii=False),
            json.dumps(msg.get("tool_calls", []), ensure_ascii=False),
            msg.get("knowledge_version"), msg.get("elapsed_ms"), now_iso(),
        ),
    )


# ---------------------------------------------------------------- tool logs

def log_tool_call(
    conn: sqlite3.Connection,
    *,
    session_id: str | None,
    message_id: str | None,
    tool_name: str,
    args_json: str,
    result_digest: str | None,
    status: str,
    latency_ms: int | None,
) -> None:
    conn.execute(
        """INSERT INTO tool_call_logs
           (session_id, message_id, tool_name, args_json, result_digest, status, latency_ms, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (session_id, message_id, tool_name, args_json, result_digest, status, latency_ms, now_iso()),
    )


def channel_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    docs = conn.execute(
        "SELECT COUNT(*) AS total, SUM(is_current) AS current FROM documents"
    ).fetchone()
    chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()
    return {
        "docs_total": docs["total"] or 0,
        "docs_current": docs["current"] or 0,
        "chunks": chunks["n"] or 0,
    }


__all__ = [name for name in dir() if not name.startswith("_")]
