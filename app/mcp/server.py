"""MCP Server：把 5 个工具暴露给外部客户端（Claude Desktop、其他 Agent 等）。

复用 tools/schemas.py 与 tools/impl.py，不重写任何逻辑——否则两份实现迟早对不上。
启动：
    python -m app.mcp.server              # stdio，供本地客户端拉起
    MCP_TRANSPORT=streamable-http python -m app.mcp.server
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from app.tools import impl
from app.tools.schemas import (
    CalcCreditsInput,
    CheckConflictInput,
    ExportInput,
    MaterialChecklistInput,
    ServiceWindowInput,
    TOOL_BY_NAME,
)

mcp = FastMCP("campus-regqa")


@mcp.tool(description=TOOL_BY_NAME["calc_credits_and_gpa"]["description"])
def calc_credits_and_gpa(payload: CalcCreditsInput) -> dict:
    return impl.calc_credits_and_gpa(payload).model_dump()


@mcp.tool(description=TOOL_BY_NAME["check_course_conflict"]["description"])
def check_course_conflict(payload: CheckConflictInput) -> dict:
    return impl.check_course_conflict(payload).model_dump()


@mcp.tool(description=TOOL_BY_NAME["make_material_checklist"]["description"])
def make_material_checklist(payload: MaterialChecklistInput) -> dict:
    return impl.make_material_checklist(payload).model_dump()


@mcp.tool(description=TOOL_BY_NAME["find_service_window"]["description"])
def find_service_window(payload: ServiceWindowInput) -> dict:
    return impl.find_service_window(payload).model_dump()


@mcp.tool(description=TOOL_BY_NAME["export_answer"]["description"])
def export_answer(payload: ExportInput) -> dict:
    return impl.export_answer(payload).model_dump()


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
