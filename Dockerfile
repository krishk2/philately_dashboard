FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.9.13 /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first so they are cached between code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

RUN useradd --create-home appuser \
    && mkdir -p /app/instance/uploads \
    && chown -R appuser /app/instance \
    && chmod +x /app/docker-entrypoint.sh
USER appuser

ENV PATH="/app/.venv/bin:$PATH" \
    FLASK_APP=wsgi.py \
    FLASK_ENV_NAME=production

EXPOSE 8000

# Used by Docker/ECS to restart an unhealthy container (the ALB uses /healthz too).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["web"]
