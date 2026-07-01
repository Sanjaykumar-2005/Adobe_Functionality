"""
PDF Manager — Flask application entry point.

Wires together configuration, logging, CORS, the feature blueprints, a single
download endpoint, JSON error handling and the background file janitor.

Run:  python app.py        (dev server on http://127.0.0.1:5000)
"""
from __future__ import annotations

import logging
import os

from flask import Flask
from flask_cors import CORS
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from config import Config
from utils.file_utils import FileValidationError, start_janitor
from utils.responses import fail

# All HTTP endpoints live in one place — see endpoints.py (ROUTES table).
import endpoints


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG if Config.DEBUG else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def create_app() -> Flask:
    _configure_logging()
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)
    CORS(app)  # allow the frontend (and external API consumers) to call the API

    # ---- All routes (UI, download, health, and every /api/... endpoint) ----
    # Defined and named in endpoints.py; registered here in one call.
    endpoints.register(app)

    # ---- Centralised error handling -> always JSON for /api, friendly otherwise ----
    @app.errorhandler(FileValidationError)
    def _bad_file(exc):
        return fail(str(exc), 400)

    @app.errorhandler(RequestEntityTooLarge)
    def _too_large(exc):
        mb = Config.MAX_CONTENT_LENGTH // (1024 * 1024)
        return fail(f"Upload exceeds the {mb} MB limit.", 413)

    @app.errorhandler(HTTPException)
    def _http_err(exc):
        return fail(exc.description or exc.name, exc.code or 500)

    @app.errorhandler(Exception)
    def _unhandled(exc):
        app.logger.exception("Unhandled error")
        return fail(f"Internal error: {exc}", 500)

    # ---- Background cleanup of stale files ----
    start_janitor(app.logger)
    app.logger.info("PDF Manager ready. Temp dir=%s", Config.TEMP_DIR)
    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=Config.DEBUG)
