# syntax=docker/dockerfile:1
# tbo-tracker — образ для сервера (amd64 и arm64/Raspberry Pi). Запуск: docker compose up -d (см. docs/DEPLOY.md)

FROM python:3.12-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.lock /tmp/requirements.lock
# Корпоративный прокси с подменой TLS: docker build --secret id=pip_ca,src=/путь/к/ca.pem . (в образ не попадает)
RUN --mount=type=secret,id=pip_ca,required=false \
    if [ -s /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; \
    /opt/venv/bin/pip install --no-deps -r /tmp/requirements.lock \
 && /opt/venv/bin/pip check

FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.title="tbo-tracker" \
      org.opencontainers.image.description="Обслуживание БПЛА по фактической наработке" \
      org.opencontainers.image.licenses="MIT"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TBO_DATA_DIR=/data \
    TBO_HOST=0.0.0.0 \
    TBO_PORT=8000
# Непривилегированный пользователь; каталог данных — отдельный том
RUN groupadd --system --gid 10001 tbo \
 && useradd --system --uid 10001 --gid tbo --home-dir /data --no-create-home --shell /usr/sbin/nologin tbo \
 && mkdir -p /data && chown tbo:tbo /data && chmod 0750 /data
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY app ./app
# бортовой агент — для скачивания с сервера (/api/downloads/tbo-agent.tar.gz) и установки на борт
COPY agent ./agent
RUN python -m compileall -q /app/app
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('TBO_PORT', '8000'), timeout=4)"]
STOPSIGNAL SIGTERM
CMD ["python", "-m", "app.serve"]
