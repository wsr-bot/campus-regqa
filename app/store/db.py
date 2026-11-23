"""SQLite 连接与建表（WAL、幂等）。SQLite 是正本，Chroma 只是索引。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id        TEXT PRIMARY KEY,            -- slug_year，如 xueji_2021
    title         TEXT NOT NULL,
    title_key     TEXT NOT NULL,               -- 去年份版本噪声的比较键
    doc_no        TEXT,
    publisher     TEXT,
    publish_date  TEXT,
    effective_year INTEGER NOT NULL,           -- 引用标签来源
    expire_year   INTEGER,
    is_current    INTEGER NOT NULL DEFAULT 1,  -- 由 supersedes 链推导，非手填
    supersedes    TEXT,
    superseded_by TEXT,
    file_type     TEXT NOT NULL,
    file_path     TEXT,
    file_hash     TEXT,
    page_count    INTEGER,
    parse_status  TEXT NOT NULL DEFAULT 'ok',
    chunk_count   INTEGER DEFAULT 0,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id      TEXT PRIMARY KEY,            -- {doc_id}#{article_no}#{part_no}
    doc_id        TEXT NOT NULL REFERENCES documents(doc_id),
    chapter_no    TEXT NOT NULL DEFAULT '',
    article_no    TEXT NOT NULL DEFAULT '',
    part_no       INTEGER NOT NULL DEFAULT 1,
    seq           INTEGER NOT NULL,
    text          TEXT NOT NULL,
    text_hash     TEXT NOT NULL,
    page_from     INTEGER,
    page_to       INTEGER,
    effective_year INTEGER NOT NULL,           -- 冗余，检索免 join
    is_current    INTEGER NOT NULL,
    file_type     TEXT NOT NULL,
    merged_from   TEXT,
    token_len     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_current ON chunks(is_current, effective_year);
CREATE INDEX IF NOT EXISTS idx_docs_type ON documents(file_type, is_current);

CREATE TABLE IF NOT EXISTS sessions (
    sid            TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    client_ip_hash TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    message_id         TEXT PRIMARY KEY,
    sid                TEXT NOT NULL,
    role               TEXT NOT NULL,
    content            TEXT NOT NULL,
    qtype              TEXT,
    refused            INTEGER NOT NULL DEFAULT 0,
    refusal_reason     TEXT,
    citation_chunk_ids TEXT,                   -- JSON
    tool_calls         TEXT,                   -- JSON
    knowledge_version  TEXT,
    elapsed_ms         INTEGER,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_sid ON messages(sid, created_at);

CREATE TABLE IF NOT EXISTS eval_samples (
    sample_id       TEXT PRIMARY KEY,
    question        TEXT NOT NULL,
    qtype           TEXT,
    gold_answer     TEXT,
    gold_chunk_ids  TEXT,                      -- JSON
    gold_doc_year   INTEGER,
    expected_refusal INTEGER NOT NULL DEFAULT 0,
    judge_verdict   TEXT,
    judge_reason    TEXT,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS eval_runs (
    run_id            TEXT PRIMARY KEY,
    started_at        TEXT NOT NULL,
    knowledge_version TEXT,
    config_json       TEXT,
    answer_acc        REAL,
    citation_acc      REAL,
    refusal_acc       REAL,
    overrefusal_rate  REAL,
    sample_n          INTEGER
);

CREATE TABLE IF NOT EXISTS tool_call_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    message_id  TEXT,
    tool_name   TEXT NOT NULL,
    args_json   TEXT,
    result_digest TEXT,
    status      TEXT NOT NULL,                 -- ok | error | blocked
    latency_ms  INTEGER,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_session ON tool_call_logs(session_id, created_at);

CREATE TABLE IF NOT EXISTS embedding_cache (
    text_hash  TEXT PRIMARY KEY,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """幂等建表，可反复调用。"""
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def get_conn(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        init_schema(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()
