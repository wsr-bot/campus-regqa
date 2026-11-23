"""初始化数据库与目录。可反复执行，不会清空已有数据。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.store import repo  # noqa: E402
from app.store.db import get_conn, init_schema  # noqa: E402


def main() -> int:
    cfg = get_settings()
    for d in (
        cfg.db_path.parent,
        cfg.chroma_dir,
        cfg.exports_dir,
        cfg.root / "data" / "raw",
        cfg.root / "data" / "processed",
        cfg.root / "data" / "registry",
        cfg.structured_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)

    with get_conn() as conn:
        init_schema(conn)
        stats = repo.channel_stats(conn)
        version = repo.knowledge_version(conn)

    print(f"数据库：{cfg.db_path}")
    print(f"向量库：{cfg.chroma_dir}")
    print(f"文件 {stats['docs_total']} 份（现行 {stats['docs_current']}），知识块 {stats['chunks']} 个，版本 {version}")
    if stats["chunks"] == 0:
        print("\n知识库为空。下一步：按 README 填 data/registry/manifest.yaml，再执行入库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
