"""Application configuration, read from environment variables.

Nothing secret lives in this file. Copy `.env.example` to `.env` for local development.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'philately.db'}")
    # Hosted Postgres providers often hand out "postgres://"; SQLAlchemy needs a driver name.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", str(BASE_DIR / "instance" / "uploads"))
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 MB per request
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


class ProductionConfig(Config):
    pass


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
