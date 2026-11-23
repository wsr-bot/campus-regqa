"""环境自检：只报告，不安装、不修改任何东西。

按铁律：安装失败不重试、不换源；这里只告诉你缺什么，装不装由你决定。
"""

from __future__ import annotations

import importlib
import os
import sys

REQUIRED = [
    ("fastapi", "Web 框架"),
    ("uvicorn", "ASGI 服务器"),
    ("pydantic", "数据校验"),
    ("yaml", "配置文件"),
    ("chromadb", "向量库"),
    ("openai", "DeepSeek / Qwen 调用"),
    ("httpx", "Anthropic 调用"),
    ("fitz", "PDF 抽取（PyMuPDF）"),
    ("langgraph", "编排图"),
]

KEYS = [
    ("ANTHROPIC_API_KEY", "anthropic（可选，强档首选）"),
    ("DEEPSEEK_API_KEY", "deepseek（强/快档备选）"),
    ("DASHSCOPE_API_KEY", "qwen + Embedding（至少要有这个）"),
]


def main() -> int:
    print(f"Python {sys.version.split()[0]}  ({sys.executable})\n")

    missing = []
    print("依赖：")
    for mod, desc in REQUIRED:
        try:
            importlib.import_module(mod)
            print(f"  [OK]   {mod:<12} {desc}")
        except Exception as exc:
            print(f"  [MISS] {mod:<12} {desc}  ({type(exc).__name__})")
            missing.append(mod)

    print("\n环境变量（只显示配没配，不打印值）：")
    for key, desc in KEYS:
        print(f"  [{'OK' if os.getenv(key) else '--'}] {key:<22} {desc}")

    print()
    if missing:
        print("缺少依赖。请在项目目录内用虚拟环境安装，不要装到系统 Python：")
        print("  python -m venv .venv")
        print("  .venv\\Scripts\\activate        （Windows）")
        print("  pip install -r requirements.txt")
        print("\n按铁律：安装失败一次就停，不要换源反复重试。")
        return 1

    print("依赖齐全。")
    if not os.getenv("DASHSCOPE_API_KEY"):
        print("但 DASHSCOPE_API_KEY 未设置，Embedding 算不出来，入库会失败。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
