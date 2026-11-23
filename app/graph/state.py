"""问答全局状态。节点之间只通过这个结构传数据，不传隐式全局变量。"""

from __future__ import annotations

from typing import Any, TypedDict


class QAState(TypedDict, total=False):
    # 输入
    sid: str
    question: str
    year_filter: int | None

    # 路由
    qtype: str                 # credit | course | material | window | general | out_of_scope
    file_types: list[str]
    route_by: str              # rule | llm | fallback
    route_confidence: float

    # 检索
    recalled: int
    candidates: list[dict[str, Any]]   # [{chunk_id, score, metadata, text}]
    kept: list[dict[str, Any]]
    top_score: float
    dropped: list[dict[str, Any]]
    forced_keep: int

    # 生成
    messages: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    tool_call_records: list[dict[str, Any]]
    turns: int
    raw_answer: str
    answer_md: str
    citations: list[dict[str, Any]]

    # 判定
    refused: bool
    refusal_reason: str
    guidance: dict[str, Any] | None

    # 元信息
    knowledge_version: str
    error: str
    elapsed_ms: int
