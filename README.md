# PDF Manager

An Adobe Acrobat–inspired **PDF management web application** with a Python (Flask)
backend and a vanilla HTML/CSS/JavaScript frontend. Upload files, run any of 19
PDF operations, and download the results — individually or as a ZIP.

## Features

| # | Tool | What it does |
|---|------|--------------|
| 1 | Word → PDF | Convert `.docx`/`.doc` to PDF (LibreOffice → Word → reportlab fallback chain) |
| 2 | Image → PDF | JPG/PNG/BMP/TIFF/WEBP → PDF, custom page size, combine into one |
| 3 | PDF → Word | Editable `.docx` preserving text, tables, images |
| 4 | Merge | Combine multiple PDFs, drag-to-reorder |
| 5 | Split | By page, range, every-N, or custom selection |
| 6 | Crop | Visually select a crop box, apply to pages/all |
| 7 | Rotate | 90/180/270° on selected pages or whole doc |
| 8 | Rearrange | Drag-and-drop page thumbnails into a new order |
| 9 | Extract | New PDF from selected pages (original preserved) |
| 10 | Delete | Remove pages, produce a new PDF |
| 11 | Compress | Low / Medium / High size reduction |
| 12 | OCR | Make scanned PDFs searchable (Tesseract) |
| 13 | Edit | Add/edit/delete text, insert images |
| 14 | Fill & Sign | Text, checkboxes, dates, drawn/typed/uploaded signatures |
| 15 | Password Protect | AES-256 encryption, user/owner passwords, permissions |
| 16 | Redact | Permanently remove content under black boxes |
| 17 | Watermark | Text or image, position/opacity/rotation/font/color |
| 18 | Page Numbers | Position, start number, font, color, prefix/suffix |
| 19 | Batch | Run one operation across many files, ZIP the results |

## Tech stack

- **Backend:** Python 3.11+, Flask, PyMuPDF, pypdf, pikepdf, Pillow, python-docx, pdf2docx, reportlab, ocrmypdf
- **Frontend:** HTML5, CSS3, vanilla JavaScript (no build step) — responsive, light/dark mode

## Project layout

```
Adobe_Functionality/
├── app.py                  # Flask app factory, CORS, error handling, janitor
├── endpoints.py            # ALL HTTP endpoints: handlers + named ROUTES table + register(app)
├── config.py               # paths, limits, allowed types, presets
├── requirements.txt
├── services/               # pure-Python PDF logic (pdf, conversion, ocr, security, batch)
├── utils/                  # file_utils (validation, jobs, zip, janitor), responses
├── templates/index.html    # single-page UI
├── static/{css,js,images}/ # frontend assets
├── temp/ outputs/          # transient working dirs (auto-cleaned hourly)
```

## Setup & run

```bash
# 1. (recommended) create a virtualenv
python -m venv .venv && source .venv/Scripts/activate   # Git Bash on Windows

# 2. install python deps
pip install -r requirements.txt

# 3. (optional) configure the Azure OCR + LLM keys
#    Edit app_settings.py and paste your OCR_API_KEY / LLM_API_KEY.

# 4. run
python app.py
# open http://127.0.0.1:5000
```

The OCR **Extract text** / **Summarize** features call Azure APIs configured in
`app_settings.py` (endpoint URLs are baked in; only the subscription keys are
needed — paste them directly into `OCR_API_KEY` / `LLM_API_KEY`). Without keys,
those two features return a clear "not configured" error; every other tool works
offline.

### Optional external binaries (graceful degradation if missing)

| Feature | Needs | Notes |
|---------|-------|-------|
| OCR | **Tesseract** + **Ghostscript** | `ocrmypdf` calls them. Without them, OCR returns a clear error. |
| Word → PDF (best fidelity) | **LibreOffice** (`soffice`) **or MS Word** | Falls back to a pure-Python renderer otherwise. |

## API overview

All endpoints accept `multipart/form-data` and return a JSON envelope:

```json
{ "success": true, "job_id": "ab12…", "files": [ { "name": "out.pdf", "url": "/download/ab12…/out.pdf", "size": 12345 } ] }
```

Errors: `{ "success": false, "error": "message" }` with a 4xx/5xx status.
Produced files are downloadable at `GET /download/<job_id>/<filename>` and are
purged automatically after one hour.

Key routes: `/api/convert/*`, `/api/organize/*`, `/api/edit/*`, `/api/security/protect`,
`/api/ocr/*`, `/api/batch`, plus `/api/preview` (page thumbnails). See the `ROUTES` table in `endpoints.py` for the full set.

## Security notes

- Uploads are validated by extension and size (200 MB cap, 50 files/request by default).
- Download paths are namespaced by a random job id and sanitized to block traversal.
- Temporary files are cleaned by a background janitor; redaction removes content permanently.
