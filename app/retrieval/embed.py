"""Embedding 抽象 + 落库缓存。

缓存键是文本 sha256 + 模型名：纯函数无失效问题，换模型等于换缓存行，
不需要任何失效逻辑。
"""

from __future__ import annotations

import hashlib
import os
import struct
from typing import Sequence

from app.config import get_settings
from app.store.db import get_conn

_KEY_ENV = {"dashscope": "DASHSCOPE_API_KEY", "fastembed": "FASTEMBED_API_KEY"}
_COMPAT_BASE = {"dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1"}


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pack(vec: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


class Embedder:
    """按 config.embedding.active 选择实现。"""

    def __init__(self) -> None:
        cfg = get_settings()
        self.active = cfg.get("embedding.active")
        self.model = cfg.get(f"embedding.{self.active}.model")
        self.dim = int(cfg.get(f"embedding.{self.active}.dim"))
        self.batch_size = int(cfg.get(f"embedding.{self.active}.batch_size", 10))

    # ---------------------------------------------------------- 对外
    def embed(self, texts: list[str], use_cache: bool = True) -> list[list[float]]:
        if not texts:
            return []

        result: list[list[float] | None] = [None] * len(texts)
        hashes = [text_hash(t) for t in texts]

        if use_cache:
            self._fill_from_cache(hashes, result)

        missing = [i for i, v in enumerate(result) if v is None]
        if missing:
            batch_texts = [texts[i] for i in missing]
            vectors = self._call_api(batch_texts)
            for idx, vec in zip(missing, vectors):
                if len(vec) != self.dim:
                    raise RuntimeError(
                        f"Embedding 维度不符：期望 {self.dim}，实际 {len(vec)}。"
                        f"请检查 config.embedding.{self.active}.dim 与模型是否匹配。"
                    )
                result[idx] = vec
            if use_cache:
                self._write_cache(
                    [(hashes[i], result[i]) for i in missing]  # type: ignore[list-item]
                )

        return [v for v in result if v is not None]  # type: ignore[misc]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    # ---------------------------------------------------------- 缓存
    def _fill_from_cache(self, hashes: list[str], out: list[list[float] | None]) -> None:
        uniq = list({h for h in hashes})
        if not uniq:
            return
        marks = ",".join("?" for _ in uniq)
        with get_conn() as conn:
            rows = conn.execute(
                f"SELECT text_hash, vector FROM embedding_cache WHERE model=? AND text_hash IN ({marks})",
                [self.model, *uniq],
            ).fetchall()
        hit = {r["text_hash"]: _unpack(r["vector"]) for r in rows}
        for i, h in enumerate(hashes):
            if h in hit:
                out[i] = hit[h]

    def _write_cache(self, pairs: list[tuple[str, Sequence[float]]]) -> None:
        if not pairs:
            return
        with get_conn() as conn:
            conn.executemany(
                """INSERT INTO embedding_cache (text_hash, model, dim, vector, created_at)
                   VALUES (?,?,?,?,datetime('now'))
                   ON CONFLICT(text_hash) DO UPDATE SET vector=excluded.vector""",
                [(h, self.model, self.dim, _pack(v)) for h, v in pairs],
            )

    # ---------------------------------------------------------- API
    def _call_api(self, texts: list[str]) -> list[list[float]]:
        if self.active == "dashscope":
            return self._call_dashscope(texts)
        raise RuntimeError(
            f"未实现的 embedding.active={self.active}。"
            f"MVP 只实现 dashscope；如需本地模型请先在 requirements.txt 增加依赖并实现 _call_ 方法。"
        )

    def _call_dashscope(self, texts: list[str]) -> list[list[float]]:
        key = os.getenv(_KEY_ENV["dashscope"])
        if not key:
            raise RuntimeError(
                "缺少环境变量 DASHSCOPE_API_KEY，无法计算 Embedding。"
                "请在启动前设置该变量（不要写进代码或提交到仓库）。"
            )
        from openai import OpenAI

        client = OpenAI(api_key=key, base_url=_COMPAT_BASE["dashscope"])
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            chunk = texts[i : i + self.batch_size]
            resp = client.embeddings.create(model=self.model, input=chunk)
            out.extend([d.embedding for d in resp.data])
        return out


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
