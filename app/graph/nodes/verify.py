"""节点：代码判定，不看模型脸色。

四条规则逐条走，任一触发即拒答。判定放在生成之后、独立于生成，
生成节点无权宣布自己「答对了」。
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.graph.state import QAState
from app.tools import data_access

NO_EVIDENCE = "现行有效文件中未查到明确规定。"
SERVICE_DOWN = "生成服务暂时不可用，请稍后重试。"


def _guidance(matter_type: str) -> dict[str, Any]:
    """拒答时指路：优先给结构化窗口信息，没有就给通用去处。"""
    table = data_access.windows()
    rows = table.get(matter_type)
    if rows:
        if isinstance(rows, dict):
            rows = [rows]
        windows = [
            {
                "name": r.get("name", ""),
                "location": r.get("location", ""),
                "open_hours": r.get("open_hours", ""),
                "phone": r.get("phone"),
                "appointment_required": bool(r.get("appointment_required", False)),
            }
            for r in rows
        ]
        return {"windows": windows, "note": "以上窗口信息以教务处公开信息为准。"}
    return {
        "windows": [],
        "note": "建议咨询所在学院教务办，或教务处学籍科。本系统尚未收录该项窗口信息。",
    }


def verify_node(state: QAState) -> dict[str, Any]:
    cfg = get_settings()
    min_score = float(cfg.get("retrieval.min_score", 0.55))

    answer = (state.get("answer_md") or "").strip()
    citations = state.get("citations") or []
    error = state.get("error") or ""
    top_score = float(state.get("top_score") or 0.0)
    qtype = state.get("qtype") or "general"
    kept = state.get("kept") or []

    # ① 服务层失败，无法判定内容
    if error.startswith(("LLM_UNAVAILABLE", "EMBED_FAILED", "MAX_TURNS")):
        return {
            "refused": True,
            "refusal_reason": error,
            "answer_md": SERVICE_DOWN,
            "citations": [],
            "guidance": None,
        }

    # ② 检索就没找到相关条款，或最高分低于阈值
    if not kept or (top_score < min_score and not citations):
        return {
            "refused": True,
            "refusal_reason": "NO_EVIDENCE",
            "answer_md": NO_EVIDENCE + "以下为办理建议，不含规定结论。",
            "citations": [],
            "guidance": _guidance(qtype),
        }

    # ③ 有答案但没有任何可用引用 —— 无依据的答案不放行
    if answer and not citations:
        return {
            "refused": True,
            "refusal_reason": "NO_CITATION",
            "answer_md": NO_EVIDENCE + "以下为办理建议，不含规定结论。",
            "citations": [],
            "guidance": _guidance(qtype),
        }

    # ④ 模型自己说查不到，但给了引用 —— 以引用为准，不推翻
    if not answer:
        return {
            "refused": True,
            "refusal_reason": "EMPTY_ANSWER",
            "answer_md": NO_EVIDENCE,
            "citations": [],
            "guidance": _guidance(qtype),
        }

    return {"refused": False, "refusal_reason": "", "answer_md": answer, "citations": citations}
