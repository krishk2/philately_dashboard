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

RUN useradd --create-home appuser && mkdir -p /app/instance/uploads && chown -R appuser /app/instance
USER appuser

ENV PATH="/app/.venv/bin:$PATH" \
    FLASK_APP=wsgi.py \
    FLASK_ENV_NAME=production

EXPOSE 8000

# Apply migrations, then serve with gunicorn.
CMD ["sh", "-c", "flask db upgrade && gunicorn --bind 0.0.0.0:8000 --workers 2 --access-logfile - wsgi:app"]
