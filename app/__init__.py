"""Application factory."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from .config import CONFIGS
from .extensions import csrf, db, login_manager, migrate


def create_app(config_name: str | None = None) -> Flask:
    load_dotenv()
    config_name = config_name or os.environ.get("FLASK_ENV_NAME", "development")
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(CONFIGS[config_name])

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY must be set (see .env.example).")

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)

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
        if request.path.startswith("/api/"):
            code = (exc.name or "error").lower().replace(" ", "_")
            return jsonify({"error": {"code": code, "message": exc.description}}), exc.code
        return render_template("error.html", error=exc), exc.code
