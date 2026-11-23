"""节点：意图路由。

规则只兜「措辞高度固定」的说法（省一次模型调用）；拿不准的一律交给 fast 档模型判断。
不做关键词模糊匹配硬猜类型——猜错会把检索范围带偏，比多调一次模型更贵。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.graph.state import QAState
from app.llm.providers import LLMUnavailable
from app.llm.router import complete

log = logging.getLogger(__name__)

QTYPES = {
    "credit": ["学籍管理", "培养方案"],
    "course": ["选课", "培养方案"],
    "material": ["证明办理", "学籍管理"],
    "window": ["证明办理"],
    "general": [],
    "out_of_scope": [],
}

# 只在整句语义几乎无歧义时命中；命不中就走模型
_RULES: list[tuple[tuple[str, ...], str]] = [
    (("差多少学分", "差几学分", "还差多少分", "学分不够", "学分缺口", "绩点", "gpa", "GPA"), "credit"),
    (("会不会冲突", "时间冲突", "冲突吗", "能不能选这门", "还能选吗"), "course"),
    (("带什么材料", "什么材料", "带哪些", "需要哪些资料"), "material"),
    (("在哪办", "哪里办", "几点上班", "哪个窗口", "办公时间", "电话是多少"), "window"),
]

_PROMPT = """你是校园教务问答系统的分类器。把学生问题归入下列一类，只输出 JSON，不要解释。

类型：
- credit：涉及学分、绩点、毕业学分要求、还差多少学分
- course：涉及选课、退课、课程冲突、学分上限、先修课
- material：涉及办某件事需要哪些材料、表格、证明
- window：涉及办理地点、办公时间、联系电话、找哪个科室
- general：其他教务规定查询（考试、成绩、重修规则、毕业审核条件、学籍异动等）
- out_of_scope：与学校教务无关

问题：{question}

输出格式：{{"qtype":"...","confidence":0.0}}"""


def _hit_rule(question: str) -> str | None:
    q = question.lower()
    for phrases, qtype in _RULES:
        if any(p.lower() in q for p in phrases):
            return qtype
    return None


def route_node(state: QAState) -> dict[str, Any]:
    question = state["question"]

    hit = _hit_rule(question)
    if hit:
        return {
            "qtype": hit,
            "file_types": QTYPES[hit],
            "route_by": "rule",
            "route_confidence": 0.92,
        }

    qtype, confidence, by = "general", 0.5, "fallback"
    try:
        res = complete("fast", [{"role": "user", "content": _PROMPT.format(question=question)}], max_tokens=120)
        data = json.loads(res.text.strip().strip("`").removeprefix("json").strip())
        candidate = str(data.get("qtype", "")).strip()
        if candidate in QTYPES:
            qtype = candidate
            confidence = float(data.get("confidence", 0.7))
            by = "llm"
    except (LLMUnavailable, json.JSONDecodeError, ValueError) as exc:
        log.warning("route 兜底到 general：%s", exc)
        by = "fallback"

    return {
        "qtype": qtype,
        "file_types": QTYPES[qtype],
        "route_by": by,
        "route_confidence": confidence,
    }
