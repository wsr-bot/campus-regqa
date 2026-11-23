"""FastAPI 装配。单进程 uvicorn 起来即可服务，无外部依赖。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

from app.api import chat, documents, export, health
from app.config import get_settings
from app.store import repo
from app.store.db import get_conn, init_schema

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("app")

WEB_DIR = Path(__file__).resolve().parent / "web"


@asynccontextmanager
async def lifespan(_: FastAPI):
    with get_conn() as conn:
        init_schema(conn)
        stats = repo.channel_stats(conn)
        version = repo.knowledge_version(conn)
    log.info(
        "启动自检：文件 %s 份（现行 %s），知识块 %s 个，版本 %s",
        stats["docs_total"], stats["docs_current"], stats["chunks"], version,
    )
    if stats["chunks"] == 0:
        log.warning("知识库为空。先入库再提问，否则所有回答都会走拒答分支。")
    yield


app = FastAPI(title="校园教务规章问答 Agent", version="0.1.0", lifespan=lifespan)

app.include_router(chat.router, tags=["chat"])
app.include_router(documents.router, tags=["documents"])
app.include_router(export.router, tags=["export"])
app.include_router(health.router, tags=["health"])


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(str(WEB_DIR / "index.html"), media_type="text/html; charset=utf-8")


@app.get("/app.js", response_class=HTMLResponse)
def app_js() -> FileResponse:
    return FileResponse(str(WEB_DIR / "app.js"), media_type="application/javascript; charset=utf-8")


@app.get("/app.css", response_class=HTMLResponse)
def app_css() -> FileResponse:
    return FileResponse(str(WEB_DIR / "app.css"), media_type="text/css; charset=utf-8")


def run() -> None:
    import uvicorn

    cfg = get_settings()
    uvicorn.run(
        "app.main:app",
        host=cfg.get("server.host"),
        port=int(cfg.get("server.port")),
        reload=False,
    )


if __name__ == "__main__":
    run()
