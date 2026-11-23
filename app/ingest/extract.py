"""文本抽取：优先读人工 OCR 的 sidecar，其次 PyMuPDF。

顺序有意如此：扫描件走 OCR 的正确率远低于人工校对，与其自动 OCR 出一堆错字，
不如先认账「这份还没电子化」。sidecar 命名固定为 data/raw/<doc_id>.txt。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)

MIN_TOTAL_CHARS = 200
MIN_PAGE_CHARS = 50


@dataclass
class Extracted:
    text: str
    page_count: int
    page_boundaries: list[tuple[int, int]] = field(default_factory=list)
    low_text_pages: int = 0
    source: str = "pdf"
    needs_ocr: bool = False


def sidecar_path(doc_id: str) -> Path:
    return get_settings().root / "data" / "raw" / f"{doc_id}.txt"


def extract(doc_id: str, pdf_path: Path) -> Extracted:
    sidecar = sidecar_path(doc_id)
    if sidecar.exists():
        text = sidecar.read_text(encoding="utf-8", errors="replace")
        pages = max(1, text.count("\f") + 1)
        return Extracted(text=text, page_count=pages, source="sidecar")

    if not pdf_path.exists():
        raise FileNotFoundError(f"既无 sidecar 也无 PDF：{pdf_path}")

    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError("缺少 PyMuPDF，请先在项目虚拟环境内安装依赖。") from exc

    parts: list[str] = []
    boundaries: list[tuple[int, int]] = []
    low = 0
    with fitz.open(str(pdf_path)) as doc:
        total = 0
        for index, page in enumerate(doc, start=1):
            page_text = page.get_text("text") or ""
            if len(page_text.strip()) < MIN_PAGE_CHARS:
                low += 1
            stamp = f"\n\n<<page:{index}>>\n\n"
            parts.append(page_text)
            total += len(page_text) + len(stamp)
            boundaries.append((index, total))

    text = "".join(parts)
    return Extracted(
        text=text,
        page_count=len(boundaries),
        page_boundaries=boundaries,
        low_text_pages=low,
        source="pdf",
        needs_ocr=len(text.strip()) < MIN_TOTAL_CHARS,
    )
