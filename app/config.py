"""Application configuration, read from environment variables.

Nothing secret lives in this file. Copy `.env.example` to `.env` for local development.
"""

import os
from pathlib import Path

from sqlalchemy.pool import NullPool

BASE_DIR = Path(__file__).resolve().parent.parent


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'philately.db'}")
    # Hosted Postgres providers often hand out "postgres://"; SQLAlchemy needs a driver name.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


# True when running inside AWS Lambda: the filesystem is read-only except /tmp, each instance
# should hold one short-lived database connection, and request bodies are capped at ~6 MB.
IS_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _database_url()
    # Lambda: no connection pool (instances are frozen between requests, so pooled connections
    # would go stale). Neon's pooled endpoint does the real pooling.
    SQLALCHEMY_ENGINE_OPTIONS = {"poolclass": NullPool} if IS_LAMBDA else {"pool_pre_ping": True}

    UPLOAD_FOLDER = os.environ.get(
        "UPLOAD_FOLDER", "/tmp/uploads" if IS_LAMBDA else str(BASE_DIR / "instance" / "uploads")
    )
    # Lambda rejects request bodies over 6 MB (after base64), so stay under that.
    MAX_CONTENT_LENGTH = (4 if IS_LAMBDA else 5) * 1024 * 1024
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

    API_TOKEN_MAX_AGE = 7 * 24 * 3600  # seconds
    API_PAGE_SIZE_MAX = 50

    # Listing verification. Thresholds are Hamming distances between 64-bit dHashes.
    DUPLICATE_HASH_DISTANCE = 6
    REFERENCE_MATCH_DISTANCE = 10
    REFERENCE_WARN_DISTANCE = 18

    # Optional AI vision check. Leave GEMINI_API_KEY unset to skip it.
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    GEMINI_TIMEOUT = float(os.environ.get("GEMINI_TIMEOUT", "20"))

    # Single sign-on. Each provider is enabled only when its client id and secret are set.
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
    YAHOO_CLIENT_ID = os.environ.get("YAHOO_CLIENT_ID")
    YAHOO_CLIENT_SECRET = os.environ.get("YAHOO_CLIENT_SECRET")

    # Imported philately headlines (`flask import-feeds`). Extra RSS/Atom feeds can be added as
    # "kind|label|url" entries separated by commas, kind being release, news or event.
    FEED_TIMEOUT = float(os.environ.get("FEED_TIMEOUT", "20"))
    FEED_EXTRA_RSS = os.environ.get("FEED_EXTRA_RSS", "")
    # India Post's guidelines require Department of Posts permission for any use of stamp
    # images, so their images are only shown once that permission is in hand.
    FEED_SHOW_SOURCE_IMAGES = _flag("FEED_SHOW_SOURCE_IMAGES", True)
    # Shown in the disclaimer so rights holders can reach you (leave unset to omit).
    CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL")
    FEED_DETAIL_FETCH_LIMIT = int(os.environ.get("FEED_DETAIL_FETCH_LIMIT", "10"))

    # Background jobs: run them immediately in the web process (no worker needed), or leave them
    # for `flask worker` / `flask run-jobs`. Production turns inline off.
    JOBS_INLINE = _flag("JOBS_INLINE", True)
    FEED_REFRESH_HOURS = float(os.environ.get("FEED_REFRESH_HOURS", "6"))
    # Badges shown next to names (forums, directory) are cached for this many seconds.
    BADGE_CACHE_SECONDS = int(os.environ.get("BADGE_CACHE_SECONDS", "300"))

    # Pre-bookings hold stock for this long while the buyer pays; unpaid ones then expire.
    PREBOOK_HOLD_MINUTES = int(os.environ.get("PREBOOK_HOLD_MINUTES", "30"))

    # Rate limiting (Flask-Limiter). Use a shared store such as redis:// with several workers.
    RATELIMIT_ENABLED = _flag("RATELIMIT_ENABLED", True)
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True

    # Where uploaded images live: "local" (UPLOAD_FOLDER) or "s3" (any S3-compatible store:
    # AWS S3, Cloudflare R2, Backblaze B2, MinIO).
    STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")
    S3_BUCKET = os.environ.get("S3_BUCKET")
    S3_PREFIX = os.environ.get("S3_PREFIX", "uploads/")
    S3_REGION = os.environ.get("S3_REGION")
    S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")  # needed for R2 / B2 / MinIO
    S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID")
    S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY")
    # Public base URL (e.g. a CDN or public bucket). If unset, short-lived signed URLs are used.
    S3_PUBLIC_URL = os.environ.get("S3_PUBLIC_URL")

    # Behind a reverse proxy (Render, Heroku, nginx), trust one hop of X-Forwarded-* headers so
    # the app sees the real scheme, host and client IP.
    TRUST_PROXY = _flag("TRUST_PROXY", False)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"


class DevelopmentConfig(Config):
    DEBUG = True
    SECRET_KEY = Config.SECRET_KEY or "dev-only-not-secret"


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    GEMINI_API_KEY = None
    # scrypt is deliberately slow; tests don't need that.
    PASSWORD_HASH_METHOD = "pbkdf2:sha256:1000"
    # Fake credentials so the SSO providers are registered; tests mock the OAuth exchange.
    GOOGLE_CLIENT_ID = "test-google-id"
    GOOGLE_CLIENT_SECRET = "test-google-secret"
    YAHOO_CLIENT_ID = "test-yahoo-id"
    YAHOO_CLIENT_SECRET = "test-yahoo-secret"
    FEED_EXTRA_RSS = ""
    RATELIMIT_ENABLED = False
    STORAGE_BACKEND = "local"
    FEED_SHOW_SOURCE_IMAGES = False
    BADGE_CACHE_SECONDS = 0
    FEED_REFRESH_HOURS = 0  # never fetch real feeds from tests


class ProductionConfig(Config):
    # HTTPS-only cookies and https:// external URLs (needed for SSO redirect URIs).
    SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE", True)
    REMEMBER_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE", True)
    REMEMBER_COOKIE_HTTPONLY = True
    PREFERRED_URL_SCHEME = "https"
    TRUST_PROXY = _flag("TRUST_PROXY", True)
    JOBS_INLINE = _flag("JOBS_INLINE", False)


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
