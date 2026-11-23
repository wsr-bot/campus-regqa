"""5 个工具的输入输出模型：全项目唯一真相源。

HTTP 层、Agent 的 Function Calling schema、MCP Server 三处都从这里取，
禁止在别处另写一份字段定义。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Course(BaseModel):
    code: str = Field(description="课程代码")
    name: str = Field(description="课程名称")
    credits: float = Field(description="学分")
    score: float | None = Field(default=None, description="百分制成绩；等级制课程留空")
    grade: str | None = Field(default=None, description="等级制成绩，如 优秀/良好/及格/不及格")
    module: Literal["必修", "选修", "通识", "实践"] = Field(description="课程模块")
    passed: bool = Field(description="是否通过")


class Window(BaseModel):
    name: str = Field(description="办理窗口或科室名称")
    location: str = Field(description="办理地点")
    open_hours: str = Field(description="对外办公时间")
    phone: str | None = Field(default=None, description="联系电话")
    appointment_required: bool = Field(default=False, description="是否需要预约")


# ---------------------------------------------------------------- T1 学分与绩点核算

class CreditRequirement(BaseModel):
    module: str = Field(description="模块名，如 必修/选修/通识/实践")
    required_credits: float = Field(description="该模块要求的学分")


class CalcCreditsInput(BaseModel):
    completed: list[Course] = Field(description="已修课程列表，含未通过的")
    requirements: list[CreditRequirement] = Field(description="毕业学分要求，按模块给出")
    gpa_rule: Literal["std_4_0", "five_band"] = Field(
        default="std_4_0", description="绩点算法：std_4_0 为常见 4.0 制，five_band 为五档制"
    )


class CreditGapItem(BaseModel):
    module: str
    required: float
    earned: float
    gap: float


class CalcCreditsOutput(BaseModel):
    credit_gap: list[CreditGapItem] = Field(description="按模块的学分缺口")
    total_gap: float = Field(description="总缺口学分")
    gpa: float | None = Field(default=None, description="按所选算法算出的绩点，无法算时为 null")
    weighted_avg: float = Field(description="加权平均分")
    assumptions: list[str] = Field(description="口径说明，必须原样展示给用户")
    rule_version: str = Field(description="算法版本号")


# ---------------------------------------------------------------- T2 选课冲突检查

class Section(BaseModel):
    course_code: str = Field(description="课程代码")
    course_name: str = Field(description="课程名称")
    credits: float = Field(description="学分")
    weekday: int = Field(description="星期几，1=周一 … 7=周日")
    periods: list[int] = Field(description="节次，如 [1,2]")
    weeks: list[int] = Field(description="上课周次，如 [1,2,...,16]")
    teacher: str | None = Field(default=None, description="任课教师")
    prereq: list[str] = Field(default_factory=list, description="先修课程代码")


class CheckConflictInput(BaseModel):
    selected: list[Section] = Field(description="已选课程")
    candidate: Section = Field(description="想新增的课程")
    max_credits: float = Field(default=30.0, description="本学期学分上限")
    completed_courses: list[str] = Field(default_factory=list, description="已通过的课程代码")


class ConflictItem(BaseModel):
    with_course: str
    weekday: int
    periods: list[int]
    weeks: list[int]


class CheckConflictOutput(BaseModel):
    conflicts: list[ConflictItem] = Field(description="冲突明细，空列表表示无冲突")
    credit_after: float = Field(description="加上候选课后的总学分")
    over_limit: bool = Field(description="是否超过学分上限")
    prereq_missing: list[str] = Field(description="缺失的先修课程代码")
    warnings: list[str] = Field(description="其他提示，如同一门课重复选")


# ---------------------------------------------------------------- T3 材料清单

class StudentStatus(BaseModel):
    level: str = Field(description="层次，如 本科/专科")
    grade: int = Field(description="年级，如 2024")
    college: str | None = Field(default=None, description="学院名称")


class MaterialChecklistInput(BaseModel):
    matter_type: str = Field(description="事项类型，如 重修申请/成绩证明/在读证明/毕业审核/学籍异动")
    student_status: StudentStatus = Field(description="学生身份信息")
    extra: dict[str, Any] = Field(default_factory=dict, description="补充条件")


class MaterialItem(BaseModel):
    name: str
    copies: int
    source: str
    template: str | None = None


class MaterialChecklistOutput(BaseModel):
    items: list[MaterialItem] = Field(description="所需材料清单")
    window: Window | None = Field(default=None, description="办理窗口")
    notes: list[str] = Field(description="注意事项")
    found: bool = Field(description="是否在材料库中找到该事项")


# ---------------------------------------------------------------- T4 办理窗口

class ServiceWindowInput(BaseModel):
    matter_type: str = Field(description="事项类型")
    campus: str | None = Field(default=None, description="校区，如 主校区")


class ServiceWindowOutput(BaseModel):
    windows: list[Window] = Field(description="可办理窗口")
    found: bool = Field(description="是否查到")
    source_doc_ids: list[str] = Field(default_factory=list, description="依据的文件 doc_id")


# ---------------------------------------------------------------- T5 导出

class CitationRef(BaseModel):
    doc_title: str
    article_no: str
    effective_year: int


class ExportInput(BaseModel):
    format: Literal["markdown", "txt"] = Field(default="markdown", description="导出格式")
    title: str = Field(description="标题，不超过 60 字")
    body_md: str = Field(description="正文，不超过 20000 字")
    citations: list[CitationRef] = Field(default_factory=list, description="引用条款")


class ExportOutput(BaseModel):
    file_name: str
    download_url: str
    expires_at: str
    bytes: int


# ---------------------------------------------------------------- 工具清单（固定序）

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "calc_credits_and_gpa",
        "input": CalcCreditsInput,
        "output": CalcCreditsOutput,
        "description": (
            "计算学分缺口与绩点。当用户给出已修课程、或问「还差多少学分」「绩点够不够毕业」时使用。"
            "不要在用户只是询问规定原文时使用，那属于检索。"
        ),
    },
    {
        "name": "check_course_conflict",
        "input": CheckConflictInput,
        "output": CheckConflictOutput,
        "description": (
            "检查选课时间冲突、学分上限与先修要求。当用户想加一门课、问「会不会冲突」「能不能选」时使用。"
            "不要用它查询开课列表或课程简介，那属于检索。"
        ),
    },
    {
        "name": "make_material_checklist",
        "input": MaterialChecklistInput,
        "output": MaterialChecklistOutput,
        "description": (
            "生成办事材料清单。当用户问「办某件事要带什么材料」时使用。"
            "不要用它回答办事流程的依据条款，那属于检索。"
        ),
    },
    {
        "name": "find_service_window",
        "input": ServiceWindowInput,
        "output": ServiceWindowOutput,
        "description": (
            "查询办理窗口的地点、时间与电话。当用户问「去哪里办」「什么时候能办」时使用。"
            "不要用它查询规定内容。"
        ),
    },
    {
        "name": "export_answer",
        "input": ExportInput,
        "output": ExportOutput,
        "description": (
            "把当前答案导出为可下载的 markdown 或 txt 文件。仅当用户明确要求下载、导出、保存成文件时使用。"
            "不要无端调用，它会写磁盘。"
        ),
    },
]

TOOL_BY_NAME: dict[str, dict[str, Any]] = {spec["name"]: spec for spec in TOOL_SPECS}


def openai_tools() -> list[dict[str, Any]]:
    """转成 OpenAI 兼容的 tools 数组。"""
    return [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["input"].model_json_schema(),
            },
        }
        for spec in TOOL_SPECS
    ]
