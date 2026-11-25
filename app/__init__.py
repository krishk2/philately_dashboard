"""Application factory."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import CONFIGS
from .extensions import csrf, db, limiter, login_manager, migrate


def create_app(config_name: str | None = None, overrides: dict | None = None) -> Flask:
    load_dotenv()
    config_name = config_name or os.environ.get("FLASK_ENV_NAME", "development")
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(CONFIGS[config_name])
    app.config.update(overrides or {})

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY must be set (see .env.example).")

    if app.config.get("TRUST_PROXY"):
        # Render/Heroku/nginx terminate TLS and forward the request. Without this the app
        # thinks it is served over http://, which breaks SSO redirect URIs and secure cookies,
        # and the rate limiter would see the proxy's IP instead of the client's.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    if app.config["STORAGE_BACKEND"] == "local":
        Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)

    from .services.storage import init_storage

    init_storage(app)

    from . import models

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(models.User, int(user_id))

    from .api import bp as api_bp
    from .auth import bp as auth_bp
    from .circle import bp as circle_bp
    from .community import bp as community_bp
    from .member import bp as member_bp
    from .web import bp as web_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(member_bp)
    app.register_blueprint(circle_bp)
    app.register_blueprint(community_bp)
    app.register_blueprint(api_bp)

    from .services.sso import enabled_providers, register_providers

    register_providers(app)
    sso_buttons = enabled_providers(app)

    @app.context_processor
    def inject_sso_providers():
        return {"sso_providers": sso_buttons}

    @app.context_processor
    def inject_unread_count():
        """Unread notification count for the profile icon in the site header."""
        from flask_login import current_user
        from sqlalchemy import func, select

        if not current_user.is_authenticated:
            return {"unread_count": 0}
        count = db.session.scalar(
            select(func.count(models.Notification.id)).where(
                models.Notification.user_id == current_user.id,
                models.Notification.is_read.is_(False),
            )
        )
        return {"unread_count": count or 0}

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    _register_error_pages(app)

    from .cli import register_cli

    register_cli(app)

    if not app.debug and not app.testing:
        logging.basicConfig(level=logging.INFO)

    return app


def _register_error_pages(app: Flask) -> None:
    @app.errorhandler(HTTPException)
    def handle_http(exc: HTTPException):
        # Requests that never reached the API blueprint (e.g. unknown /api/ URLs) still get JSON.
        if request.path.startswith("/api/") or request.path == "/ask":
            code = (exc.name or "error").lower().replace(" ", "_")
            body = {"error": {"code": code, "message": exc.description}}
            if request.path == "/ask" and exc.code == 429:  # the chatbot shows "response" text
                body["response"] = "You're sending messages too quickly. Please wait a minute."
            return jsonify(body), exc.code
        if exc.code == 429:
            exc.description = "Too many attempts. Please wait a minute and try again."
        return render_template("error.html", error=exc), exc.code
