FROM python:3.11-slim

WORKDIR /app

# 依赖先装，利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config
COPY data/structured ./data/structured
COPY data/registry ./data/registry
COPY scripts ./scripts

# 托管环境通常没有持久化磁盘：库与索引落到 /tmp，重启即重建
ENV APP_DB_PATH=/tmp/runtime.db \
    APP_CHROMA_DIR=/tmp/chroma \
    APP_EXPORTS_DIR=/tmp/exports \
    APP_PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
