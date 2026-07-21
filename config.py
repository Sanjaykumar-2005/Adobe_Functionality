"""
Central configuration for the PDF Manager application.

All tunables (paths, limits, allowed types, retention) live here so services and
routes never hard-code magic values. Values can be overridden with environment
variables, which keeps the module 12-factor friendly for production deployment.
"""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _path(name: str) -> str:
    p = os.path.join(BASE_DIR, name)
    os.makedirs(p, exist_ok=True)
    return p


class Config:
    # ---- Flask ----
    SECRET_KEY = os.environ.get("SECRET_KEY", "pdf-manager-dev-secret-change-me")
    DEBUG = os.environ.get("FLASK_DEBUG", "1") == "1"

    # ---- Storage ----
    # Uploads are persisted per-job under TEMP_DIR (see utils.file_utils);
    # OUTPUT_DIR holds processed results served by /download.
    OUTPUT_DIR = _path("outputs")   # processed files served for download
    TEMP_DIR = _path("temp")        # raw uploads + scratch space, per job_id

    # ---- Upload limits ----
    # Hard cap enforced by Flask (request rejected before reaching a route).
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_MB", "200")) * 1024 * 1024
    MAX_FILES_PER_REQUEST = int(os.environ.get("MAX_FILES", "50"))

    # ---- Retention ----
    # Files older than this (seconds) are purged by the background janitor.
    FILE_RETENTION_SECONDS = int(os.environ.get("RETENTION_SECONDS", str(60 * 60)))  # 1 hour
    CLEANUP_INTERVAL_SECONDS = int(os.environ.get("CLEANUP_INTERVAL", str(15 * 60)))  # 15 min

    # ---- Allowed input types per feature family ----
    ALLOWED_PDF = {".pdf"}
    ALLOWED_WORD = {".docx", ".doc"}
    ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

    # ---- Named page sizes (points, 1pt = 1/72") for image->pdf ----
    PAGE_SIZES = {
        "A4": (595, 842),
        "A3": (842, 1191),
        "A5": (420, 595),
        "Letter": (612, 792),
        "Legal": (612, 1008),
        "Fit": None,  # page matches each image's pixel size
    }

    # ---- OCR ----
    OCR_DEFAULT_LANG = os.environ.get("OCR_LANG", "eng")
    # Selectable OCR engine. "tesseract" is local/default; cloud engines
    # (google_vision / azure_read / aws_textract) activate only when their SDK
    # and credentials are present (see services/ocr_engines.py).
    OCR_DEFAULT_ENGINE = os.environ.get("OCR_ENGINE", "tesseract")
    OCR_CLOUD_DPI = int(os.environ.get("OCR_CLOUD_DPI", "200"))  # render DPI for cloud OCR

    # ---- Compression presets: target image DPI + JPEG quality ----
    COMPRESSION_LEVELS = {
        "low":    {"dpi": 150, "quality": 85},  # light squeeze, best quality
        "medium": {"dpi": 110, "quality": 65},
        "high":   {"dpi": 72,  "quality": 45},  # smallest file
    }

    # Ladder used when compressing to a TARGET SIZE, ordered best-quality first.
    # pdf_service.compress binary-searches it for the gentlest step that still
    # fits, so keep it sorted by DECREASING output size.
    #
    # Keep the steps CLOSE together: the search returns the first step that fits,
    # so a coarse ladder overshoots and throws away quality the user asked to keep
    # (a 96dpi->72dpi jump once turned a 300 KB target into a 93 KB file). The
    # search is binary, so doubling the rungs costs ~1 extra pass, not double.
    COMPRESSION_TARGET_STEPS = [
        {"dpi": 220, "quality": 92},
        {"dpi": 200, "quality": 90},
        {"dpi": 180, "quality": 87},
        {"dpi": 165, "quality": 85},
        {"dpi": 150, "quality": 82},
        {"dpi": 140, "quality": 78},
        {"dpi": 130, "quality": 75},
        {"dpi": 120, "quality": 72},
        {"dpi": 110, "quality": 68},
        {"dpi": 100, "quality": 65},
        {"dpi": 96,  "quality": 60},
        {"dpi": 88,  "quality": 55},
        {"dpi": 80,  "quality": 50},
        {"dpi": 72,  "quality": 45},
        {"dpi": 66,  "quality": 40},
        {"dpi": 60,  "quality": 36},
        {"dpi": 54,  "quality": 32},
        {"dpi": 48,  "quality": 28},
        {"dpi": 42,  "quality": 24},
        {"dpi": 36,  "quality": 20},
        {"dpi": 30,  "quality": 16},
        {"dpi": 24,  "quality": 12},  # last resort, visibly degraded
    ]
