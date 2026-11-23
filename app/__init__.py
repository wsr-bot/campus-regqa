"""校园教务规章问答 Agent。

依赖方向（单向，禁反向）：
    api -> graph -> tools | retrieval | llm -> store
    ingest -> store | retrieval
    mcp -> tools

tools/impl.py 不 import graph、不 import llm，保证纯函数可独立单测。
"""

__version__ = "0.1.0"
