"""
Pluggable OCR engine registry.

The OCR feature can run against several backends, selectable per request:

  * ``tesseract``     — local, via ocrmypdf (the default; the original engine).
  * ``google_vision`` — Google Cloud Vision document text detection.
  * ``azure_read``    — Azure AI Vision (Image Analysis "Read").
  * ``aws_textract``  — Amazon Textract ``detect_document_text``.

Design
------
Every engine exposes the same shape:

    key, label, kind, requires,
    available() -> (bool, reason),
    ocr(path, job_id, lang, force) -> [abs_output_path]

Only the local Tesseract engine is guaranteed to work out of the box (when the
Tesseract binary is installed). Each cloud engine is **optional**: its SDK and
credentials are read lazily from the environment, and ``available()`` reports a
clear reason when something is missing — nothing here is imported at module load,
so the app boots fine without any cloud SDK. Selecting an unavailable engine
raises a friendly :class:`RuntimeError` only at request time.

Cloud engines build a *searchable* PDF the same way Tesseract does: the original
page image is kept and an **invisible** text layer (PyMuPDF ``render_mode=3``) is
overlaid from the words + bounding boxes the cloud API returns.

Credentials (all via environment variables; never hard-coded):
  * Google : ``GOOGLE_APPLICATION_CREDENTIALS`` (path to a service-account JSON)
  * Azure  : ``AZURE_VISION_ENDPOINT`` + ``AZURE_VISION_KEY``
  * AWS    : standard boto3 chain (``AWS_ACCESS_KEY_ID``/``AWS_SECRET_ACCESS_KEY``
             or a profile/role) plus ``AWS_DEFAULT_REGION``
"""
from __future__ import annotations

import importlib.util
import os

import fitz  # PyMuPDF

from config import Config
from utils.file_utils import output_path, with_suffix
from services import ocr_service


# --------------------------------------------------------------------------- #
# Shared helpers (page rendering + invisible text overlay)
# --------------------------------------------------------------------------- #
def _importable(module: str) -> bool:
    """True if *module* can be imported without actually importing it."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _render_pages(path: str, dpi: int) -> list[tuple[int, int, bytes]]:
    """Render every page to a PNG. Returns ``[(width_px, height_px, png_bytes)]``."""
    out: list[tuple[int, int, bytes]] = []
    with fitz.open(path) as doc:
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        for page in doc:
            pix = page.get_pixmap(matrix=mat, alpha=False)
            out.append((pix.width, pix.height, pix.tobytes("png")))
    return out


def _overlay_searchable(src_path: str, dest: str,
                        page_words: list[list[tuple]], dpi: int) -> str:
    """Write a searchable PDF: original pages + an invisible text layer.

    *page_words* is one list per page of ``(text, x0, y0, x1, y1)`` tuples in
    **image pixel** coordinates (top-left origin). They are scaled back to PDF
    points and drawn with ``render_mode=3`` (invisible but selectable/searchable).
    """
    scale = 72.0 / dpi
    with fitz.open(src_path) as doc:
        for pno in range(doc.page_count):
            words = page_words[pno] if pno < len(page_words) else []
            page = doc[pno]
            for (text, x0, y0, x1, y1) in words:
                if not text or not str(text).strip():
                    continue
                # Font size ≈ box height (points); baseline at the box bottom.
                fs = max(4.0, (y1 - y0) * scale)
                point = fitz.Point(x0 * scale, y1 * scale)
                try:
                    page.insert_text(point, str(text), fontsize=fs, render_mode=3)
                except Exception:
                    continue  # never let one stray word abort the whole page
        doc.save(dest, deflate=True, garbage=4)
    return dest


def _cloud_ocr(words_fn, path: str, job_id: str) -> list[str]:
    """Common cloud flow: render pages → extract words via *words_fn* → overlay."""
    dpi = Config.OCR_CLOUD_DPI
    images = _render_pages(path, dpi)
    page_words = words_fn(images)
    dest = output_path(job_id, with_suffix(path, "_ocr"))
    return [_overlay_searchable(path, dest, page_words, dpi)]


# --------------------------------------------------------------------------- #
# Engine: local Tesseract (via ocrmypdf) — delegates to the original service
# --------------------------------------------------------------------------- #
def _tesseract_available() -> tuple[bool, str]:
    if not ocr_service._OCRMYPDF_OK:
        return False, "ocrmypdf is not installed (pip install ocrmypdf)."
    import shutil
    if not shutil.which("tesseract"):
        return False, "The Tesseract binary was not found on PATH."
    return True, ""


def _tesseract_ocr(path: str, job_id: str, lang: str, force: bool) -> list[str]:
    return ocr_service.ocr_pdf(path, job_id, lang=lang, force=force)


# --------------------------------------------------------------------------- #
# Engine: Google Cloud Vision
# --------------------------------------------------------------------------- #
def _google_available() -> tuple[bool, str]:
    if not _importable("google.cloud.vision"):
        return False, "google-cloud-vision is not installed."
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return False, "Set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON."
    return True, ""


def _google_words(images: list[tuple[int, int, bytes]]) -> list[list[tuple]]:
    from google.cloud import vision  # lazy

    client = vision.ImageAnnotatorClient()
    pages: list[list[tuple]] = []
    for (_w, _h, png) in images:
        resp = client.document_text_detection(image=vision.Image(content=png))
        if resp.error.message:
            raise RuntimeError("Google Vision error: " + resp.error.message)
        words: list[tuple] = []
        # text_annotations[0] is the whole-page text; [1:] are individual words.
        for ann in resp.text_annotations[1:]:
            verts = ann.bounding_poly.vertices
            xs = [v.x for v in verts] or [0]
            ys = [v.y for v in verts] or [0]
            words.append((ann.description, min(xs), min(ys), max(xs), max(ys)))
        pages.append(words)
    return pages


def _google_ocr(path: str, job_id: str, lang: str, force: bool) -> list[str]:
    return _cloud_ocr(_google_words, path, job_id)


# --------------------------------------------------------------------------- #
# Engine: Azure AI Vision (Image Analysis — Read)
# --------------------------------------------------------------------------- #
def _azure_available() -> tuple[bool, str]:
    if not _importable("azure.ai.vision.imageanalysis"):
        return False, "azure-ai-vision-imageanalysis is not installed."
    if not (os.environ.get("AZURE_VISION_ENDPOINT") and os.environ.get("AZURE_VISION_KEY")):
        return False, "Set AZURE_VISION_ENDPOINT and AZURE_VISION_KEY."
    return True, ""


def _azure_words(images: list[tuple[int, int, bytes]]) -> list[list[tuple]]:
    from azure.ai.vision.imageanalysis import ImageAnalysisClient  # lazy
    from azure.ai.vision.imageanalysis.models import VisualFeatures
    from azure.core.credentials import AzureKeyCredential

    client = ImageAnalysisClient(
        endpoint=os.environ["AZURE_VISION_ENDPOINT"],
        credential=AzureKeyCredential(os.environ["AZURE_VISION_KEY"]),
    )
    pages: list[list[tuple]] = []
    for (_w, _h, png) in images:
        result = client.analyze(image_data=png, visual_features=[VisualFeatures.READ])
        words: list[tuple] = []
        if result.read is not None:
            for block in result.read.blocks:
                for line in block.lines:
                    for word in line.words:
                        poly = word.bounding_polygon
                        xs = [p.x for p in poly] or [0]
                        ys = [p.y for p in poly] or [0]
                        words.append((word.text, min(xs), min(ys), max(xs), max(ys)))
        pages.append(words)
    return pages


def _azure_ocr(path: str, job_id: str, lang: str, force: bool) -> list[str]:
    return _cloud_ocr(_azure_words, path, job_id)


# --------------------------------------------------------------------------- #
# Engine: Amazon Textract
# --------------------------------------------------------------------------- #
def _aws_available() -> tuple[bool, str]:
    if not _importable("boto3"):
        return False, "boto3 is not installed."
    if not os.environ.get("AWS_DEFAULT_REGION"):
        return False, "Set AWS_DEFAULT_REGION (and AWS credentials / profile / role)."
    return True, ""


def _aws_words(images: list[tuple[int, int, bytes]]) -> list[list[tuple]]:
    import boto3  # lazy

    client = boto3.client("textract")
    pages: list[list[tuple]] = []
    for (w, h, png) in images:
        resp = client.detect_document_text(Document={"Bytes": png})
        words: list[tuple] = []
        for block in resp.get("Blocks", []):
            if block.get("BlockType") != "WORD":
                continue
            bb = block["Geometry"]["BoundingBox"]  # normalized 0..1
            x0 = bb["Left"] * w
            y0 = bb["Top"] * h
            words.append((block.get("Text", ""), x0, y0,
                          x0 + bb["Width"] * w, y0 + bb["Height"] * h))
        pages.append(words)
    return pages


def _aws_ocr(path: str, job_id: str, lang: str, force: bool) -> list[str]:
    return _cloud_ocr(_aws_words, path, job_id)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
_ENGINES = {
    "tesseract": {
        "key": "tesseract", "label": "Tesseract (local)", "kind": "local",
        "requires": "Tesseract + Ghostscript installed on the server.",
        "available": _tesseract_available, "ocr": _tesseract_ocr,
    },
    "google_vision": {
        "key": "google_vision", "label": "Google Cloud Vision", "kind": "cloud",
        "requires": "pip install google-cloud-vision + GOOGLE_APPLICATION_CREDENTIALS.",
        "available": _google_available, "ocr": _google_ocr,
    },
    "azure_read": {
        "key": "azure_read", "label": "Azure AI Vision (Read)", "kind": "cloud",
        "requires": "pip install azure-ai-vision-imageanalysis + AZURE_VISION_ENDPOINT/KEY.",
        "available": _azure_available, "ocr": _azure_ocr,
    },
    "aws_textract": {
        "key": "aws_textract", "label": "Amazon Textract", "kind": "cloud",
        "requires": "pip install boto3 + AWS credentials + AWS_DEFAULT_REGION.",
        "available": _aws_available, "ocr": _aws_ocr,
    },
}


def list_engines() -> list[dict]:
    """Return every engine with its current availability (for the UI dropdown)."""
    out = []
    for eng in _ENGINES.values():
        ok, reason = eng["available"]()
        out.append({
            "key": eng["key"], "label": eng["label"], "kind": eng["kind"],
            "available": ok, "reason": reason, "requires": eng["requires"],
        })
    return out


def run(engine_key: str, path: str, job_id: str, lang: str, force: bool) -> list[str]:
    """Dispatch OCR to the chosen engine.

    Raises :class:`ValueError` for an unknown engine and :class:`RuntimeError`
    (with the availability reason) when the chosen engine isn't configured.
    """
    eng = _ENGINES.get(engine_key)
    if eng is None:
        raise ValueError(
            f"Unknown OCR engine '{engine_key}'. "
            f"Choose from: {', '.join(_ENGINES)}."
        )
    ok, reason = eng["available"]()
    if not ok:
        raise RuntimeError(f"OCR engine '{eng['label']}' is unavailable: {reason}")
    return eng["ocr"](path, job_id, lang, force)
