"""读取 data/structured/*.yaml。带进程内缓存，文件改动需重启生效（MVP 可接受）。"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from app.config import get_settings


@lru_cache(maxsize=32)
def load_structured(name: str) -> dict[str, Any]:
    """name 不带扩展名，如 materials / windows / requirements。"""
    path = get_settings().structured_dir / f"{name}.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def materials() -> dict[str, Any]:
    """matter_type -> {items:[{name,copies,source,template}], window, notes}"""
    return load_structured("materials").get("matters", {})


def windows() -> dict[str, Any]:
    """matter_type -> [Window dict]"""
    return load_structured("windows").get("matters", {})


def requirements() -> list[dict[str, Any]]:
    return load_structured("requirements").get("modules", [])


def gpa_rule() -> dict[str, Any]:
    return load_structured("gpa_rule")


def reload_all() -> None:
    load_structured.cache_clear()
