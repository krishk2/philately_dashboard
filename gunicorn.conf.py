"""Gunicorn settings, tunable through environment variables."""

import multiprocessing
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
# Default: 2 workers per CPU + 1, capped at 8. Each worker handles requests with a few threads,
# which suits this I/O-bound app (database, S3, Gemini calls).
workers = int(os.environ.get("WEB_CONCURRENCY", min(multiprocessing.cpu_count() * 2 + 1, 8)))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
worker_class = "gthread"
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
graceful_timeout = 30
keepalive = 5
# Restart workers now and then to contain slow memory growth.
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
# Behind a load balancer: trust its X-Forwarded-* headers (the app also applies ProxyFix).
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "*")
