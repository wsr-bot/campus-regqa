"""问答接口：一次性返回 + SSE 流式。

流式说明（如实标注）：节点级事件（route/retrieval/rerank/tool）由图的真实执行进度推送；
正文 token 是按 12 字切片推送，不是模型原生逐字流——providers 层目前是整块返回。
前端体验一致，但不要对外宣称「逐字流式生成」。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.deps import client_key, guard, new_message_id, new_sid
from app.graph.build import get_graph
from app.store import repo
from app.store.db import get_conn

log = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, description="会话 id，不传则新建")
    question: str = Field(min_length=1, max_length=500, description="学生问题")
    year_filter: int | None = Field(default=None, description="只看该年份及以前生效的条款")


class Citation(BaseModel):
    chunk_id: str
    doc_id: str
    doc_title: str
    article_no: str
    chapter_no: str
    effective_year: int
    is_current: bool
    quote: str


class ToolCallRecord(BaseModel):
    call_id: str
    name: str
    args: dict[str, Any]
    status: str
    latency_ms: int
    result_digest: str | None = None


class ChatResponse(BaseModel):
    message_id: str
    session_id: str
    answer_md: str
    citations: list[Citation]
    refused: bool
    refusal_reason: str = ""
    guidance: dict[str, Any] | None = None
    tool_calls: list[ToolCallRecord] = []
    elapsed_ms: int
    knowledge_version: str


def _prepare(req: ChatRequest, request: Request) -> tuple[str, dict[str, Any]]:
    guard(request)
    sid = req.session_id or new_sid()
    ip_hash = client_key(request)

    with get_conn() as conn:
        repo.touch_session(conn, sid, ip_hash)
        version = repo.knowledge_version(conn)

    state: dict[str, Any] = {
        "sid": sid,
        "question": req.question.strip(),
        "year_filter": req.year_filter,
        "messages": [],
        "tool_calls": [],
        "tool_call_records": [],
        "turns": 0,
        "knowledge_version": version,
    }
    return sid, state


def _config(sid: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": sid}, "recursion_limit": 24}


def _persist(sid: str, message_id: str, state: dict[str, Any], elapsed_ms: int) -> None:
    citation_ids = [c["chunk_id"] for c in (state.get("citations") or [])]
    with get_conn() as conn:
        repo.insert_message(
            conn,
            {
                "message_id": message_id,
                "sid": sid,
                "role": "assistant",
                "content": state.get("answer_md") or "",
                "qtype": state.get("qtype"),
                "refused": state.get("refused"),
                "refusal_reason": state.get("refusal_reason"),
                "citation_chunk_ids": citation_ids,
                "tool_calls": state.get("tool_call_records") or [],
                "knowledge_version": state.get("knowledge_version"),
                "elapsed_ms": elapsed_ms,
            },
        )


def _build_response(sid: str, message_id: str, state: dict[str, Any], elapsed_ms: int) -> ChatResponse:
    return ChatResponse(
        message_id=message_id,
        session_id=sid,
        answer_md=state.get("answer_md") or "",
        citations=[Citation(**c) for c in (state.get("citations") or [])],
        refused=bool(state.get("refused")),
        refusal_reason=state.get("refusal_reason") or "",
        guidance=state.get("guidance"),
        tool_calls=[ToolCallRecord(**r) for r in (state.get("tool_call_records") or [])],
        elapsed_ms=elapsed_ms,
        knowledge_version=state.get("knowledge_version") or "",
    )


@router.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request) -> ChatResponse:
    started = time.time()
    sid, state = _prepare(req, request)
    message_id = new_message_id()

    final = get_graph().invoke(state, config=_config(sid))
    elapsed = int((time.time() - started) * 1000)

    _persist(sid, message_id, final, elapsed)
    return _build_response(sid, message_id, final, elapsed)


# ---------------------------------------------------------------- SSE

def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _chunks(text: str, size: int = 12):
    for i in range(0, len(text), size):
        yield text[i : i + size]


@router.post("/api/chat/stream")
def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    started = time.time()
    sid, state = _prepare(req, request)
    message_id = new_message_id()

    def gen():
        merged: dict[str, Any] = dict(state)
        try:
            for chunk in get_graph().stream(state, config=_config(sid), stream_mode="updates"):
                for node, delta in chunk.items():
                    if not isinstance(delta, dict):
                        continue
                    merged.update(delta)

                    if node == "route":
                        yield _sse(
                            "route",
                            {
                                "qtype": delta.get("qtype"),
                                "file_types": delta.get("file_types", []),
                                "by": delta.get("route_by"),
                                "confidence": delta.get("route_confidence"),
                            },
                        )
                    elif node == "retrieve":
                        yield _sse(
                            "retrieval",
                            {
                                "recalled": delta.get("recalled", 0),
                                "docs": sorted({c["doc_title"] for c in delta.get("candidates", [])}),
                                "top_score": delta.get("top_score", 0.0),
                            },
                        )
                    elif node == "rerank":
                        yield _sse(
                            "rerank",
                            {
                                "kept": len(delta.get("kept", []) or []),
                                "dropped": delta.get("dropped", []),
                                "forced_keep": delta.get("forced_keep", 0),
                            },
                        )
                    elif node == "tools":
                        for rec in delta.get("tool_call_records", []) or []:
                            yield _sse(
                                "tool_call",
                                {"call_id": rec["call_id"], "name": rec["name"], "args": rec["args"]},
                            )
                            yield _sse(
                                "tool_result",
                                {
                                    "call_id": rec["call_id"],
                                    "status": rec["status"],
                                    "latency_ms": rec["latency_ms"],
                                    "result_digest": rec.get("result_digest"),
                                },
                            )

            answer = merged.get("answer_md") or ""
            for piece in _chunks(answer):
                yield _sse("token", {"text": piece})

            for c in merged.get("citations") or []:
                yield _sse(
                    "citation",
                    {
                        "chunk_id": c["chunk_id"],
                        "doc_title": c["doc_title"],
                        "article_no": c["article_no"],
                        "effective_year": c["effective_year"],
                    },
                )

            elapsed = int((time.time() - started) * 1000)
            _persist(sid, message_id, merged, elapsed)
            yield _sse(
                "done",
                {
                    "message_id": message_id,
                    "session_id": sid,
                    "refused": bool(merged.get("refused")),
                    "refusal_reason": merged.get("refusal_reason") or "",
                    "guidance": merged.get("guidance"),
                    "elapsed_ms": elapsed,
                    "knowledge_version": merged.get("knowledge_version") or "",
                },
            )
        except Exception as exc:  # 流一旦开出去，错误只能作为事件告知前端
            log.exception("stream 失败")
            yield _sse(
                "error",
                {"code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}", "retryable": True},
            )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
