"""入库 CLI。

用法：
    python -m app.ingest.run_ingest --doc-id xueji_2021
    python -m app.ingest.run_ingest --all --dry-run
    python -m app.ingest.run_ingest --all --report

--dry-run 只抽文本、切块、打质量报告，不写库、不调 Embedding（不花钱）。
扫描件会标 needs_ocr 并停下，等你放 sidecar 再跑。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from app.config import get_settings
from app.ingest import chunker, outline
from app.ingest.extract import extract
from app.ingest.indexer import index_chunks
from app.ingest.manifest import ManifestEntry, file_sha256, load_manifest, register, validate
from app.store.db import get_conn, init_schema

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger("ingest")


def ingest_one(entry: ManifestEntry, *, dry_run: bool = False) -> dict:
    root = get_settings().root
    pdf_path = root / entry.file_path

    result: dict = {"doc_id": entry.doc_id, "title": entry.title}

    extracted = extract(entry.doc_id, pdf_path)
    result["source"] = extracted.source
    result["pages"] = extracted.page_count
    result["low_text_pages"] = extracted.low_text_pages

    if extracted.needs_ocr:
        result["status"] = "needs_ocr"
        result["hint"] = f"全文有效字符不足，请把 OCR 结果存为 data/raw/{entry.doc_id}.txt 后重跑。"
        if not dry_run:
            register(entry, page_count=extracted.page_count, parse_status="needs_ocr",
                     file_hash=file_sha256(pdf_path))
        return result

    chapters = outline.scan(extracted.text)
    result["outline"] = chapters.summary()

    chunks = chunker.chunk_document(
        doc_id=entry.doc_id,
        text=extracted.text,
        effective_year=entry.effective_year,
        is_current=entry.is_current,
        file_type=entry.file_type,
        page_boundaries=extracted.page_boundaries,
    )
    result["chunks"] = len(chunks)
    result["articles"] = len({c.article_no for c in chunks if c.article_no})
    result["suspicious"] = chunker.suspicious(chunks)[:10]

    if not chunks:
        result["status"] = "failed"
        result["hint"] = "切块为空：检查文本是否正常抽取，或改用 sidecar。"
        return result

    if dry_run:
        result["status"] = "dry_run"
        result["sample"] = [
            {"chunk_id": c.chunk_id, "article_no": c.article_no, "chars": len(c.text)}
            for c in chunks[:3]
        ]
        return result

    register(entry, page_count=extracted.page_count, parse_status="ok",
             file_hash=file_sha256(pdf_path))
    stats = index_chunks(chunks)
    result.update(stats)
    result["status"] = "ok"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="教务文件入库")
    parser.add_argument("--doc-id", help="只入库指定文件")
    parser.add_argument("--all", action="store_true", help="入库 manifest 中全部文件")
    parser.add_argument("--dry-run", action="store_true", help="只切块与打报告，不写库不调 Embedding")
    parser.add_argument("--report", action="store_true", help="把报告写到 data/processed/report.json")
    args = parser.parse_args(argv)

    if not args.doc_id and not args.all:
        parser.print_help()
        return 2

    entries = load_manifest()
    problems = validate(entries)
    if problems:
        print("登记表校验失败，已中止：", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 3

    targets = [e for e in entries if args.all or e.doc_id == args.doc_id]
    if args.doc_id and not targets:
        print(f"manifest 中没有 doc_id={args.doc_id}", file=sys.stderr)
        return 4

    with get_conn() as conn:
        init_schema(conn)

    results = []
    for entry in targets:
        try:
            results.append(ingest_one(entry, dry_run=args.dry_run))
        except Exception as exc:
            log.error("入库失败 doc=%s err=%s", entry.doc_id, exc)
            results.append({"doc_id": entry.doc_id, "status": "error", "error": str(exc)})

    print(json.dumps(results, ensure_ascii=False, indent=2))

    if args.report:
        out = get_settings().root / "data" / "processed" / "report.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入 {out}")

    bad = [r for r in results if r.get("status") in {"error", "failed", "needs_ocr"}]
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
