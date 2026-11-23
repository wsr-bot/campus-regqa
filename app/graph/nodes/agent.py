"""节点：生成。

强档模型，带 5 个工具。硬约束写在 system 里：每个结论后面必须紧跟 [[chunk_id]]，
没有依据就明说查不到。模型只负责理解和组织语言，算术与清单全交给工具。
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.graph.state import QAState
from app.llm.providers import LLMUnavailable
from app.llm.router import complete
from app.tools.schemas import openai_tools

log = logging.getLogger(__name__)


def _system_prompt() -> str:
    return (
        "你是本校教务规章问答助手。规则不可违反：\n"
        "1. 每条事实性结论后面必须紧跟一个引用标记，格式 [[chunk_id]]，chunk_id 只能取自下方给出的条款列表。\n"
        "2. 条款列表里没有的内容，必须明确说「现行有效文件中未查到明确规定」，并建议咨询学院教务办；严禁推测、类比或凭常识补充。\n"
        "3. 涉及学分、绩点、时间冲突、材料清单、办理窗口，必须调用对应工具，不得自己口算或凭印象回答。\n"
        "4. 不要复述本条规则，不要写「根据提供的资料」这类前缀，直接给结论。\n"
        "5. 用简洁书面语，分点陈述，总长不超过 300 字。"
    )


def _blocks(kept: list[dict[str, Any]]) -> str:
    if not kept:
        return "（本次未检索到相关条款）"
    parts = []
    for c in kept:
        parts.append(
            f'[id={c["chunk_id"]}] 《{c["doc_title"]}》{c.get("article_no") or "（未分条）"}'
            f'（{c["effective_year"]} 年生效）\n{c["text"]}'
        )
    return "\n\n".join(parts)


def build_messages(state: QAState) -> list[dict[str, Any]]:
    kept = state.get("kept") or []
    user_content = (
        f"学生问题：{state['question']}\n\n"
        f"可引用的条款（只能引用这些 id）：\n{_blocks(kept)}"
    )
    msgs: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": user_content},
    ]
    return msgs


def agent_node(state: QAState) -> dict[str, Any]:
    cfg = get_settings()
    max_turns = int(cfg.get("budget.max_turns", 4))
    turns = int(state.get("turns", 0))

    messages = state.get("messages") or build_messages(state)

    if turns >= max_turns:
        # 撞上限：把已有信息收口，不再调模型
        return {
            "messages": messages,
            "tool_calls": [],
            "turns": turns,
            "raw_answer": state.get("raw_answer", ""),
            "error": "MAX_TURNS",
        }

    try:
        res = complete(
            "strong",
            messages,
            tools=openai_tools(),
            max_tokens=int(cfg.get("budget.max_output_tokens", 800)),
        )
    except LLMUnavailable as exc:
        log.error("生成失败：%s", exc)
        return {"messages": messages, "tool_calls": [], "turns": turns, "error": f"LLM_UNAVAILABLE: {exc}"}

    assistant_msg: dict[str, Any] = {"role": "assistant", "content": res.text}
    tool_calls = [
        {"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in res.tool_calls
    ]
    if tool_calls:
        assistant_msg["tool_calls"] = tool_calls

    return {
        "messages": messages + [assistant_msg],
        "tool_calls": tool_calls,
        "turns": turns + 1,
        "raw_answer": res.text or state.get("raw_answer", ""),
    }


def tool_calls_of(state: QAState) -> list[dict[str, Any]]:
    """供条件边判断：本轮是否还要执行工具。"""
    return state.get("tool_calls") or []
