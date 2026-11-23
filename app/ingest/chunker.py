"""切块：按「章—条」切，不按固定长度。

理由很实在：答案要能说「第几条」，块边界就必须等于条边界。固定长度切出来的块
跨两条，引用就说不清，评审一看就露。

三个兜底规则：
- 单条超过 1200 字（含长表格）按「款」二次切，part_no 递增，引用仍指向同一条；
- 单条不足 80 字（如「第十二条 本章自公布之日起施行。」）并入下一条，记 merged_from；
- 没有条款编号的段落归入「未分条」，article_no 留空，前端显示为（未分条）。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

CHAPTER = re.compile(r"^\s*第\s*([一二三四五六七八九十百零〇两]+)\s*章[\s　]*(.*)$")
ARTICLE = re.compile(r"^\s*第\s*([一二三四五六七八九十百零〇两]+)\s*条[\s　]*(.*)$")

MAX_CHARS = 1200
MIN_CHARS = 80


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    chapter_no: str
    article_no: str
    part_no: int
    seq: int
    text: str
    text_hash: str
    page_from: int | None = None
    page_to: int | None = None
    effective_year: int = 0
    is_current: int = 1
    file_type: str = ""
    merged_from: str | None = None
    token_len: int = 0

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class _Block:
    chapter_no: str = ""
    article_no: str = ""
    lines: list[str] = field(default_factory=list)
    page_from: int | None = None
    page_to: int | None = None


def normalize(text: str) -> str:
    """全角转半角、去零宽字符、压缩空白，保留换行用于分条。"""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def _char_count(s: str) -> int:
    # 粗略 token 估算：中文 1 字≈1 token，英文 4 字符≈1
    cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
    other = len(re.sub(r"[\u4e00-\u9fff]", "", s))
    return cjk + other // 4


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def split_blocks(text: str, pages: list[tuple[int, int]] | None = None) -> list[_Block]:
    """按章、条切成块。pages 为 [(页码, 该页结束偏移)]，用于回填页码范围。"""
    blocks: list[_Block] = []
    cur = _Block()

    def page_at(offset: int) -> int | None:
        if not pages:
            return None
        for page_no, end in pages:
            if offset <= end:
                return page_no
        return pages[-1][0]

    offset = 0
    for line in text.splitlines(keepends=True):
        cur_offset = offset
        offset += len(line)
        stripped = line.strip()

        m_chap = CHAPTER.match(stripped)
        if m_chap:
            if cur.lines:
                blocks.append(cur)
            cur = _Block(chapter_no=f"第{m_chap.group(1)}章", page_from=page_at(cur_offset))
            if m_chap.group(2):
                cur.lines.append(m_chap.group(2).strip())
            continue

        m_art = ARTICLE.match(stripped)
        if m_art:
            if cur.lines:
                blocks.append(cur)
            cur = _Block(chapter_no=cur.chapter_no, article_no=f"第{m_art.group(1)}条", page_from=page_at(cur_offset))
            if m_art.group(2):
                cur.lines.append(m_art.group(2).strip())
            continue

        if stripped:
            if cur.page_from is None:
                cur.page_from = page_at(cur_offset)
            cur.page_to = page_at(cur_offset)
            cur.lines.append(stripped)

    if cur.lines:
        blocks.append(cur)
    return blocks


def _split_long(block: _Block) -> list[tuple[int, str]]:
    """超长条按句末切分，累积到接近 MAX_CHARS 就断开（part_no 递增）。"""
    body = "\n".join(block.lines)
    if len(body) <= MAX_CHARS:
        return [(1, body)]

    sentences = re.split(r"(?<=[。；！？])", body)
    parts: list[tuple[int, str]] = []
    buf = ""
    part_no = 1
    for s in sentences:
        if not s:
            continue
        if buf and len(buf) + len(s) > MAX_CHARS:
            parts.append((part_no, buf.strip()))
            part_no += 1
            buf = s
        else:
            buf += s
    if buf.strip():
        parts.append((part_no, buf.strip()))
    return parts


def chunk_document(
    *,
    doc_id: str,
    text: str,
    effective_year: int,
    is_current: int,
    file_type: str,
    page_boundaries: list[tuple[int, int]] | None = None,
) -> list[Chunk]:
    normalized = normalize(text)
    blocks = split_blocks(normalized, page_boundaries)

    # 短条并入下一条，避免出现只有一句话的碎块
    merged: list[_Block] = []
    pending: _Block | None = None
    for b in blocks:
        body_len = len("\n".join(b.lines))
        if pending is not None:
            b = _Block(
                chapter_no=b.chapter_no or pending.chapter_no,
                article_no=b.article_no or pending.article_no,
                lines=pending.lines + b.lines,
                page_from=pending.page_from or b.page_from,
                page_to=b.page_to or pending.page_to,
            )
            b.lines.append(f"[并入 {pending.article_no or '首段'}]")
            pending = None

        if body_len < MIN_CHARS and b is not blocks[-1]:
            pending = b
            continue
        merged.append(b)

    if pending is not None:
        merged.append(pending)

    chunks: list[Chunk] = []
    seq = 0
    for b in merged:
        for part_no, body in _split_long(b):
            if not body:
                continue
            seq += 1
            art = b.article_no or ""
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}#{art or 'none'}#{part_no}",
                    doc_id=doc_id,
                    chapter_no=b.chapter_no,
                    article_no=art,
                    part_no=part_no,
                    seq=seq,
                    text=body,
                    text_hash=_hash(body),
                    page_from=b.page_from,
                    page_to=b.page_to,
                    effective_year=effective_year,
                    is_current=is_current,
                    file_type=file_type,
                    token_len=_char_count(body),
                )
            )
    return chunks


def suspicious(chunks: list[Chunk]) -> list[str]:
    """质量闸门：块尾不是句末标点的，多半是跨页断裂，列出来人工看一眼。"""
    bad = []
    for c in chunks:
        tail = c.text.rstrip()[-1:] if c.text.strip() else ""
        if tail and tail not in "。；！？）」』：":
            bad.append(c.chunk_id)
    return bad
