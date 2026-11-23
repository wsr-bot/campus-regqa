"""文件登记表：年份溯源的地基。

is_current 不手填，由 supersedes 链推导——手填一定会填错，推导不会。
写库前校验「同一 title_key 只能有一份现行版」，冲突就中止并打印冲突清单。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings
from app.store import repo
from app.store.db import get_conn

log = logging.getLogger(__name__)

MANIFEST = "manifest.yaml"


@dataclass
class ManifestEntry:
    doc_id: str
    title: str
    title_key: str
    effective_year: int
    file_type: str
    file_path: str
    doc_no: str = ""
    publisher: str = ""
    publish_date: str = ""
    expire_year: int | None = None
    supersedes: str | None = None
    is_current: int = 1
    superseded_by: str | None = None

    def to_row(self, page_count: int, parse_status: str, file_hash: str) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "title_key": self.title_key,
            "doc_no": self.doc_no,
            "publisher": self.publisher,
            "publish_date": self.publish_date,
            "effective_year": self.effective_year,
            "expire_year": self.expire_year,
            "is_current": self.is_current,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "file_type": self.file_type,
            "file_path": self.file_path,
            "file_hash": file_hash,
            "page_count": page_count,
            "parse_status": parse_status,
            "chunk_count": 0,
            "updated_at": repo.now_iso(),
        }


def manifest_path() -> Path:
    return get_settings().root / "data" / "registry" / MANIFEST


def load_manifest(path: Path | None = None) -> list[ManifestEntry]:
    path = path or manifest_path()
    if not path.exists():
        raise FileNotFoundError(
            f"缺少 {path}。请先按 README 的格式登记文件，再执行入库。"
        )
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    entries: list[ManifestEntry] = []
    for raw in data.get("documents", []):
        entries.append(
            ManifestEntry(
                doc_id=raw["doc_id"],
                title=raw["title"],
                title_key=raw.get("title_key") or raw["title"],
                effective_year=int(raw["effective_year"]),
                file_type=raw["file_type"],
                file_path=raw.get("file_path", f"data/raw/{raw['doc_id']}.pdf"),
                doc_no=raw.get("doc_no", ""),
                publisher=raw.get("publisher", ""),
                publish_date=str(raw.get("publish_date", "")),
                expire_year=raw.get("expire_year"),
                supersedes=raw.get("supersedes"),
            )
        )
    return _derive_current(entries)


def _derive_current(entries: list[ManifestEntry]) -> list[ManifestEntry]:
    """沿 supersedes 链推导现行版：被替代的一律 is_current=0。"""
    by_id = {e.doc_id: e for e in entries}
    for e in entries:
        if e.supersedes and e.supersedes in by_id:
            old = by_id[e.supersedes]
            old.is_current = 0
            old.superseded_by = e.doc_id
    return entries


def validate(entries: list[ManifestEntry]) -> list[str]:
    """现行唯一性校验。返回冲突描述，空列表表示通过。"""
    seen: dict[str, str] = {}
    problems: list[str] = []
    for e in entries:
        if e.is_current != 1:
            continue
        if e.title_key in seen:
            problems.append(
                f"title_key 重复且同时为现行版：{seen[e.title_key]} 与 {e.doc_id}（{e.title_key}）"
            )
        seen[e.title_key] = e.doc_id

    ids = [e.doc_id for e in entries]
    for dup in {i for i in ids if ids.count(i) > 1}:
        problems.append(f"doc_id 重复：{dup}")

    for e in entries:
        if e.supersedes and e.supersedes not in by_id_all(entries):
            problems.append(f"{e.doc_id} 的 supersedes 指向不存在的 {e.supersedes}")
    return problems


def by_id_all(entries: list[ManifestEntry]) -> set[str]:
    return {e.doc_id for e in entries}


def file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def register(entry: ManifestEntry, *, page_count: int, parse_status: str, file_hash: str) -> None:
    with get_conn() as conn:
        repo.upsert_document(conn, entry.to_row(page_count, parse_status, file_hash))
        # 同一 title_key 的其它版本，若本次推导为被替代，要确保库里同步降级
        for row in repo.find_current_by_title_key(conn, entry.title_key):
            if row["doc_id"] != entry.doc_id:
                repo.set_current_flag(conn, row["doc_id"], 0)
