from __future__ import annotations

from flask import Flask

from ..config import Settings
from .routes import make_blueprint


def create_app(*, settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()

    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
        static_url_path="/static",
    )

    # NOTE: no shared LedBleService here. Each request opens a short-lived
    # connection ("connect -> act -> disconnect") so the OS BLE stack never
    # gets left holding a stale link (which wedges the adapter for everyone).
    app.register_blueprint(make_blueprint(settings=settings))
    return app