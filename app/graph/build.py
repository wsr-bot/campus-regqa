"""图装配。流程是静态的，7 个节点 + 1 条回环，刻意不用动态规划——固定图好调试。

route → retrieve → rerank → agent ⇄ tools → compose → verify → END
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.graph.nodes.agent import agent_node
from app.graph.nodes.compose import compose_node
from app.graph.nodes.rerank import rerank_node
from app.graph.nodes.retrieve import retrieve_node
from app.graph.nodes.route import route_node
from app.graph.nodes.tools_node import tools_node
from app.graph.nodes.verify import verify_node
from app.graph.state import QAState


def _after_agent(state: QAState) -> str:
    return "tools" if (state.get("tool_calls") or []) else "compose"


def build_graph(checkpointer: Any | None = None):
    g = StateGraph(QAState)

    g.add_node("route", route_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("rerank", rerank_node)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node)
    g.add_node("compose", compose_node)
    g.add_node("verify", verify_node)

    g.set_entry_point("route")
    g.add_edge("route", "retrieve")
    g.add_edge("retrieve", "rerank")
    g.add_edge("rerank", "agent")
    g.add_conditional_edges("agent", _after_agent, {"tools": "tools", "compose": "compose"})
    g.add_edge("tools", "agent")
    g.add_edge("compose", "verify")
    g.add_edge("verify", END)

    # MVP 用内存 checkpointer：一问一答之间不依赖持久化，会话历史另存 messages 表。
    # 需要跨进程恢复会话时，再换成 SqliteSaver（多装一个包即可）。
    return g.compile(checkpointer=checkpointer or MemorySaver())


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
