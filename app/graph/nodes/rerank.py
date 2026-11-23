"""节点：重排。

向量前 3 名强制入围（不参与淘汰，防止语义相似但被模型误杀），
第 4 名之后由 fast 档批量打分，取前若干补足 keep 数。
模型不可用时降级为纯向量序——宁可用差一点的结果，也不能让整条链路断掉。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import get_settings
from app.graph.state import QAState
from app.llm.providers import LLMUnavailable
from app.llm.router import complete

log = logging.getLogger(__name__)

_PROMPT = """判断下列条款片段与问题的相关程度，按 0-1 打分（1=直接回答该问题，0=无关）。
只输出 JSON 数组，元素形如 {{"id":"片段id","score":0.0}}，不要解释。

问题：{question}

片段：
{blocks}"""


def rerank_node(state: QAState) -> dict[str, Any]:
    cfg = get_settings()
    keep_n = int(cfg.get("retrieval.rerank_keep", 5))
    force_n = int(cfg.get("retrieval.force_keep_topn", 3))

    cands = state.get("candidates") or []
    if not cands:
        return {"kept": [], "dropped": [], "forced_keep": 0}

    if len(cands) <= keep_n:
        return {"kept": cands, "dropped": [], "forced_keep": min(force_n, len(cands))}

    forced = cands[:force_n]
    rest = cands[force_n:]

    scores: dict[str, float] = {}
    try:
        blocks = "\n\n".join(
            f'[id={c["chunk_id"]}] {c["text"][:400]}' for c in rest[:17]
        )
        res = complete(
            "fast",
            [{"role": "user", "content": _PROMPT.format(question=state["question"], blocks=blocks)}],
            max_tokens=600,
        )
        raw = res.text.strip().strip("`").removeprefix("json").strip()
        for item in json.loads(raw):
            scores[str(item.get("id", ""))] = float(item.get("score", 0.0))
    except (LLMUnavailable, json.JSONDecodeError, ValueError, TypeError) as exc:
        log.warning("rerank 降级为向量序：%s", exc)
        scores = {}

    if scores:
        ranked = sorted(rest, key=lambda c: scores.get(c["chunk_id"], 0.0), reverse=True)
        picked = ranked[: max(0, keep_n - len(forced))]
        dropped = [
            {"chunk_id": c["chunk_id"], "score": round(scores.get(c["chunk_id"], 0.0), 3)}
            for c in ranked[len(picked) :]
        ]
    else:
        picked = rest[: max(0, keep_n - len(forced))]
        dropped = [
            {"chunk_id": c["chunk_id"], "score": c.get("score", 0.0)}
            for c in rest[len(picked) :]
        ]

    kept = forced + picked
    return {"kept": kept, "dropped": dropped, "forced_keep": len(forced)}
