"""导出下载。文件名由服务端生成，客户端只传文件名，路径穿越在校验层拦死。"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import get_settings

router = APIRouter()

_SAFE = re.compile(r"^export_[0-9a-f]{8}\.(md|txt)$")


@router.get("/api/exports/{file_name}")
def download(file_name: str) -> FileResponse:
    if not _SAFE.match(file_name):
        raise HTTPException(status_code=400, detail="文件名不合法")

    path = get_settings().exports_dir / file_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在或已过期")

    media = "text/markdown; charset=utf-8" if file_name.endswith(".md") else "text/plain; charset=utf-8"
    return FileResponse(str(path), media_type=media, filename=file_name)
