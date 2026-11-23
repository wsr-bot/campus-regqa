"""请求级依赖：会话 id、限流。限流用进程内滑动窗口，单实例够用。"""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.config import get_settings

_windows: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def new_sid() -> str:
    return uuid.uuid4().hex[:16]


def new_message_id() -> str:
    return "m_" + uuid.uuid4().hex[:10]


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def check_rate_limit(key: str, per_min: int) -> None:
    now = time.time()
    with _lock:
        q = _windows[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= per_min:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试。")
        q.append(now)


def guard(request: Request, session_scoped: bool = False) -> None:
    cfg = get_settings()
    if session_scoped:
        limit = int(cfg.get("server.rate_limit.per_session_per_min", 6))
        check_rate_limit("s:" + client_key(request), limit)
    else:
        limit = int(cfg.get("server.rate_limit.per_ip_per_min", 10))
        check_rate_limit("ip:" + client_key(request), limit)
