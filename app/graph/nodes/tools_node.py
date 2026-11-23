"""节点：执行工具。

工具是纯函数，参数先过 pydantic 校验——非法参数在这一层就被拦下并记 blocked，
不让脏参数进到实现里，也不让异常冒到图外把整条链路打断。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.graph.state import QAState
from app.store import repo
from app.store.db import get_conn
from app.tools.impl import call as call_tool
from app.tools.impl import digest

log = logging.getLogger(__name__)


def tools_node(state: QAState) -> dict[str, Any]:
    calls = state.get("tool_calls") or []
    messages = list(state.get("messages") or [])
    records: list[dict[str, Any]] = []
    sid = state.get("sid")

    for tc in calls:
        started = time.time()
        status = "ok"
        content = ""
        result_digest: str | None = None

        try:
            result = call_tool(tc["name"], tc.get("arguments") or {})
            payload = result.model_dump() if hasattr(result, "model_dump") else result
            content = json.dumps(payload, ensure_ascii=False)
            result_digest = digest(result)
        except (TypeError, ValueError) as exc:
            status = "blocked"
            content = f"参数不合法，未执行：{exc}"
            log.warning("工具参数被拦下 tool=%s err=%s", tc["name"], exc)
        except Exception as exc:  # 工具内部错误不致命，回喂给模型让它换路子
            status = "error"
            content = f"工具执行失败：{type(exc).__name__}: {exc}"
            log.error("工具失败 tool=%s err=%s", tc["name"], exc)

        latency = int((time.time() - started) * 1000)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": tc["name"],
                "content": content,
            }
        )
        records.append(
            {
                "call_id": tc["id"],
                "name": tc["name"],
                "args": tc.get("arguments") or {},
                "status": status,
                "latency_ms": latency,
                "result_digest": result_digest,
            }
        )

        try:
            with get_conn() as conn:
                repo.log_tool_call(
                    conn,
                    session_id=sid,
                    message_id=None,
                    tool_name=tc["name"],
                    args_json=json.dumps(tc.get("arguments") or {}, ensure_ascii=False),
                    result_digest=result_digest,
                    status=status,
                    latency_ms=latency,
                )
        except Exception as exc:  # 日志写失败不影响回答
            log.warning("写工具日志失败：%s", exc)

    return {"messages": messages, "tool_calls": [], "tool_call_records": records}
