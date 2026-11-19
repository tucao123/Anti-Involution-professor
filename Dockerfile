# 职引者 ZHIYIN · FastAPI BFF 镜像
# 构建：docker compose build api
# 说明：默认安装「轻量服务依赖」（celery/redis/psycopg/neo4j/langgraph/qdrant-client），
#       不安装 sentence-transformers / torch（体积大）；BGE 未装时自动降级 hash 投影。
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

ARG INSTALL_SERVICE_DEPS=1
RUN if [ "$INSTALL_SERVICE_DEPS" = "1" ]; then \
      pip install celery redis "psycopg[binary]" neo4j langgraph qdrant-client ; \
    fi

COPY *.py ./
# 前端页面全部由 server.py 从 ROOT 读取：主入口 workbench、登录页、旧单页与演示配置缺一即 404/500
COPY workbench.html login.html ./
COPY stores ./stores
COPY deploy ./deploy

EXPOSE 8848

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8848/api/health', timeout=3)" || exit 1

CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8848"]
