"""5 个工具的实现：全是纯函数。

硬约束：本文件不 import `app.graph`、不 import `app.llm`。
理由：保证每个工具都能脱离 Agent 单独单测，也保证 MCP 暴露时不牵连编排层。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.tools import data_access
from app.tools.schemas import (
    CalcCreditsInput,
    CalcCreditsOutput,
    CheckConflictInput,
    CheckConflictOutput,
    ConflictItem,
    CreditGapItem,
    ExportInput,
    ExportOutput,
    MaterialChecklistInput,
    MaterialChecklistOutput,
    MaterialItem,
    ServiceWindowInput,
    ServiceWindowOutput,
    Window,
)

GPA_RULE_VERSION = "gpa-v1"

# 常见 4.0 制分段：分数下限 -> 绩点
_STD_4_0: list[tuple[float, float]] = [
    (90, 4.0), (85, 3.7), (82, 3.3), (78, 3.0), (75, 2.7),
    (72, 2.3), (68, 2.0), (64, 1.5), (60, 1.0), (0, 0.0),
]
# 五档制：优/良/中/及格/不及格
_FIVE_BAND: dict[str, float] = {
    "优秀": 4.0, "优": 4.0, "良好": 3.0, "良": 3.0, "中等": 2.0, "中": 2.0,
    "及格": 1.0, "不及格": 0.0, "不合格": 0.0,
}


def _point_std(score: float) -> float:
    for lower, point in _STD_4_0:
        if score >= lower:
            return point
    return 0.0


def calc_credits_and_gpa(inp: CalcCreditsInput) -> CalcCreditsOutput:
    """T1：学分缺口 + 绩点。算分一律走代码，绝不让模型口算。"""
    reqs = list(inp.requirements)
    if not reqs:
        # 未传要求时回落到 data/structured/requirements.yaml，仍算不出就返回空缺口
        from app.tools.schemas import CreditRequirement

        reqs = [CreditRequirement(**r) for r in data_access.requirements()]
    earned: dict[str, float] = {}
    for c in inp.completed:
        if c.passed:
            earned[c.module] = earned.get(c.module, 0.0) + c.credits

    gaps: list[CreditGapItem] = []
    for r in reqs:
        got = round(earned.get(r.module, 0.0), 2)
        gaps.append(
            CreditGapItem(
                module=r.module,
                required=r.required_credits,
                earned=got,
                gap=round(max(0.0, r.required_credits - got), 2),
            )
        )
    total_gap = round(sum(g.gap for g in gaps), 2)

    gpa: float | None = None
    gpa_credits = 0.0
    gpa_points = 0.0
    weighted_credits = 0.0
    weighted_scores = 0.0
    skipped = 0

    for c in inp.completed:
        if c.score is not None:
            weighted_credits += c.credits
            weighted_scores += c.score * c.credits
            point = _point_std(c.score)
            gpa_credits += c.credits
            gpa_points += point * c.credits
        elif c.grade:
            point = _FIVE_BAND.get(c.grade.strip())
            if point is None:
                skipped += 1
                continue
            gpa_credits += c.credits
            gpa_points += point * c.credits
        else:
            skipped += 1

    if gpa_credits > 0:
        gpa = round(gpa_points / gpa_credits, 2)
    weighted_avg = round(weighted_scores / weighted_credits, 2) if weighted_credits > 0 else 0.0

    assumptions = [
        "学分只统计 passed=True 的课程；未通过课程不计学分。",
        f"绩点口径 {inp.gpa_rule}：未通过课程按 0 分计入分母；分母为参与计算的课程总学分。",
        "加权平均分只统计有百分制成绩的课程，等级制课程不参与。",
        "毕业要求学分为传入值；如与培养方案不一致，以培养方案原文为准。",
    ]
    if skipped:
        assumptions.append(f"有 {skipped} 门课程既无分数也无等级，未参与绩点与均分计算。")

    return CalcCreditsOutput(
        credit_gap=gaps,
        total_gap=total_gap,
        gpa=gpa,
        weighted_avg=weighted_avg,
        assumptions=assumptions,
        rule_version=GPA_RULE_VERSION,
    )


def _overlap(a: list[int], b: list[int]) -> bool:
    return bool(set(a) & set(b))


def check_course_conflict(inp: CheckConflictInput) -> CheckConflictOutput:
    """T2：时间冲突 + 学分上限 + 先修检查。纯集合运算。"""
    cand = inp.candidate
    conflicts: list[ConflictItem] = []
    warnings: list[str] = []

    for s in inp.selected:
        if s.course_code == cand.course_code:
            warnings.append(f"已选课程中已包含 {cand.course_name}（{cand.course_code}），重复添加无意义。")
            continue
        if s.weekday == cand.weekday and _overlap(s.periods, cand.periods) and _overlap(s.weeks, cand.weeks):
            conflicts.append(
                ConflictItem(
                    with_course=f"{s.course_name}（{s.course_code}）",
                    weekday=s.weekday,
                    periods=sorted(set(s.periods) & set(cand.periods)),
                    weeks=sorted(set(s.weeks) & set(cand.weeks)),
                )
            )

    credit_after = round(sum(s.credits for s in inp.selected) + cand.credits, 2)
    over_limit = credit_after > inp.max_credits
    if over_limit:
        warnings.append(f"加上本课共 {credit_after} 学分，超过上限 {inp.max_credits} 学分。")

    prereq_missing = [p for p in cand.prereq if p not in set(inp.completed_courses)]
    if prereq_missing:
        warnings.append("存在未通过的先修课程：" + "、".join(prereq_missing))

    return CheckConflictOutput(
        conflicts=conflicts,
        credit_after=credit_after,
        over_limit=over_limit,
        prereq_missing=prereq_missing,
        warnings=warnings,
    )


def _to_window(raw: dict[str, Any]) -> Window:
    return Window(
        name=raw.get("name", ""),
        location=raw.get("location", ""),
        open_hours=raw.get("open_hours", ""),
        phone=raw.get("phone"),
        appointment_required=bool(raw.get("appointment_required", False)),
    )


def make_material_checklist(inp: MaterialChecklistInput) -> MaterialChecklistOutput:
    """T3：查 materials.yaml 生成清单。查不到就如实 found=False。"""
    table = data_access.materials()
    entry = table.get(inp.matter_type)
    if not entry:
        for key, val in table.items():
            if key in inp.matter_type or inp.matter_type in key:
                entry = val
                break
    if not entry:
        return MaterialChecklistOutput(
            items=[],
            window=None,
            notes=["材料库中未找到该事项，请到学院教务办现场咨询。"],
            found=False,
        )

    items = [
        MaterialItem(
            name=i.get("name", ""),
            copies=int(i.get("copies", 1)),
            source=i.get("source", ""),
            template=i.get("template"),
        )
        for i in entry.get("items", [])
    ]
    window = _to_window(entry["window"]) if entry.get("window") else None
    return MaterialChecklistOutput(
        items=items,
        window=window,
        notes=entry.get("notes", []),
        found=True,
    )


def find_service_window(inp: ServiceWindowInput) -> ServiceWindowOutput:
    """T4：查 windows.yaml。"""
    table = data_access.windows()
    rows = table.get(inp.matter_type)
    if rows is None:
        for key, val in table.items():
            if key in inp.matter_type or inp.matter_type in key:
                rows = val
                break
    if not rows:
        return ServiceWindowOutput(windows=[], found=False, source_doc_ids=[])

    if isinstance(rows, dict):
        rows = [rows]
    windows = [_to_window(r) for r in rows]
    if inp.campus:
        hit = [w for w in windows if inp.campus in w.location or inp.campus in w.name]
        windows = hit or windows
    doc_ids = []
    for r in rows:
        doc_ids.extend(r.get("source_doc_ids", []) or [])
    return ServiceWindowOutput(windows=windows, found=True, source_doc_ids=sorted(set(doc_ids)))


def export_answer(inp: ExportInput) -> ExportOutput:
    """T5：唯一写操作，只写 data/exports/，文件名由本函数生成，不接收外部路径。"""
    from app.config import get_settings

    if len(inp.title) > 60:
        raise ValueError("标题超过 60 字，请缩短后再导出。")
    if len(inp.body_md) > 20000:
        raise ValueError("正文超过 20000 字，请拆分后再导出。")

    ext = "md" if inp.format == "markdown" else "txt"
    name = f"export_{secrets.token_hex(4)}.{ext}"

    lines = [f"# {inp.title}", "", inp.body_md.strip(), ""]
    if inp.citations:
        lines += ["", "## 依据", ""]
        for c in inp.citations:
            lines.append(f"- 《{c.doc_title}》{c.article_no}（{c.effective_year} 年生效）")
    content = "\n".join(lines) + "\n"

    out_dir = get_settings().exports_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(content, encoding="utf-8")

    expires = datetime.now(timezone.utc) + timedelta(days=7)
    return ExportOutput(
        file_name=name,
        download_url=f"/api/exports/{name}",
        expires_at=expires.isoformat(timespec="seconds"),
        bytes=len(content.encode("utf-8")),
    )


def digest(result: Any) -> str:
    """工具回执摘要，写日志用。"""
    raw = result if isinstance(result, str) else json.dumps(
        result.model_dump() if hasattr(result, "model_dump") else result,
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------- 注册表

def _calc(raw: dict[str, Any]) -> CalcCreditsOutput:
    return calc_credits_and_gpa(CalcCreditsInput(**raw))


def _conflict(raw: dict[str, Any]) -> CheckConflictOutput:
    return check_course_conflict(CheckConflictInput(**raw))


def _materials(raw: dict[str, Any]) -> MaterialChecklistOutput:
    return make_material_checklist(MaterialChecklistInput(**raw))


def _windows(raw: dict[str, Any]) -> ServiceWindowOutput:
    return find_service_window(ServiceWindowInput(**raw))


def _export(raw: dict[str, Any]) -> ExportOutput:
    return export_answer(ExportInput(**raw))


IMPL: dict[str, Callable[[dict[str, Any]], Any]] = {
    "calc_credits_and_gpa": _calc,
    "check_course_conflict": _conflict,
    "make_material_checklist": _materials,
    "find_service_window": _windows,
    "export_answer": _export,
}


def call(name: str, args: dict[str, Any]) -> Any:
    """统一入口：参数先过 pydantic 校验，非法参数在这里被拦下。"""
    from app.tools.schemas import TOOL_BY_NAME

    spec = TOOL_BY_NAME.get(name)
    if spec is None:
        raise ValueError(f"未知工具：{name}")
    validated = spec["input"](**args)
    return IMPL[name](validated.model_dump())
