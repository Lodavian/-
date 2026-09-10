FROM python:3.11.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 10001 fastorder && useradd --uid 10001 --gid 10001 --home-dir /var/lib/fastorder --create-home fastorder
WORKDIR /app

COPY packaging/server-requirements.lock /app/server-requirements.lock
RUN python -m pip install -r /app/server-requirements.lock

COPY fast_order /app/fast_order
COPY fast_order_server /app/fast_order_server
COPY server_admin /app/server_admin

RUN mkdir -p /var/lib/fastorder/webull_tokens && chown -R fastorder:fastorder /var/lib/fastorder /app
USER 10001:10001

EXPOSE 8080
CMD ["python", "-m", "fast_order_server.main"]
