"""目录对齐检测。

不做强制对齐（错位对齐比不对齐更危险），只检测并报告：
正文里出现的章标题数量 vs 目录里列的条数，对不上就提示人工核对。
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

TOC_LINE = re.compile(r"^\s*(第[一二三四五六七八九十百零〇两]+[章条])\s*[\.·…\s]{3,}\s*(\d+)\s*$")
HEADING = re.compile(r"^\s*第\s*([一二三四五六七八九十百零〇两]+)\s*([章条])")


@dataclass
class OutlineReport:
    toc_entries: list[tuple[str, int]] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    matched: int = 0
    missing_in_body: list[str] = field(default_factory=list)
    extra_in_body: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_in_body and not self.extra_in_body

    def summary(self) -> str:
        if not self.toc_entries:
            return f"未检测到目录；正文标题 {len(self.headings)} 个"
        return (
            f"目录 {len(self.toc_entries)} 项 / 正文标题 {len(self.headings)} 个 / "
            f"对上级 {self.matched}；目录有正文无 {len(self.missing_in_body)}，正文有目录无 {len(self.extra_in_body)}"
        )


def scan(text: str) -> OutlineReport:
    report = OutlineReport()
    for line in text.splitlines():
        m = TOC_LINE.match(line)
        if m:
            report.toc_entries.append((m.group(1), int(m.group(2))))
            continue
        h = HEADING.match(line)
        if h:
            report.headings.append(f"第{h.group(1)}{h.group(2)}")

    if not report.toc_entries:
        return report

    body_set = set(report.headings)
    toc_names = [name for name, _ in report.toc_entries]

    for name in toc_names:
        if name in body_set:
            report.matched += 1
        else:
            close = difflib.get_close_matches(name, body_set, n=1, cutoff=0.8)
            if close:
                report.matched += 1
            else:
                report.missing_in_body.append(name)

    for name in body_set:
        if name not in toc_names and not difflib.get_close_matches(name, toc_names, n=1, cutoff=0.8):
            report.extra_in_body.append(name)

    return report
