"""配置单例：读 config/app.yaml，全项目只此一处产出参数。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# 项目根 = 本文件所在目录的上一层
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "app.yaml"


def _env_override(cfg: dict[str, Any]) -> dict[str, Any]:
    """托管环境可能没有持久化磁盘，允许用环境变量覆盖落盘路径。"""
    server = cfg.setdefault("server", {})
    for env_key, cfg_key in (
        ("APP_DB_PATH", "db_path"),
        ("APP_CHROMA_DIR", "chroma_dir"),
        ("APP_EXPORTS_DIR", "exports_dir"),
        ("APP_PORT", "port"),
    ):
        val = os.getenv(env_key)
        if val:
            server[cfg_key] = int(val) if cfg_key == "port" else val
    return cfg


class Settings:
    """薄封装：点号路径取值，缺键即报错（不静默给默认值）。"""

    def __init__(self, data: dict[str, Any], root: Path) -> None:
        self._data = data
        self.root = root

    def get(self, path: str, default: Any = None) -> Any:
        cur: Any = self._data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                if default is not None:
                    return default
                raise KeyError(f"配置缺失：{path}")
            cur = cur[part]
        return cur

    def rel(self, path: str) -> Path:
        """把配置里的相对路径解析成绝对路径（相对项目根）。"""
        p = Path(path)
        return p if p.is_absolute() else (self.root / p).resolve()

    @property
    def db_path(self) -> Path:
        return self.rel(self.get("server.db_path"))

    @property
    def chroma_dir(self) -> Path:
        return self.rel(self.get("server.chroma_dir"))

    @property
    def exports_dir(self) -> Path:
        return self.rel(self.get("server.exports_dir"))

    @property
    def structured_dir(self) -> Path:
        return (self.root / "data" / "structured").resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    cfg_path = Path(os.getenv("APP_CONFIG", DEFAULT_CONFIG))
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data = _env_override(data)
    return Settings(data, ROOT)
