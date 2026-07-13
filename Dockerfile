FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LIBREOFFICE_BIN=/usr/bin/soffice \
    HOME=/tmp \
    # glibc gives every thread its own malloc arena, so a threaded worker holds far
    # more RSS than it uses. Capping arenas is the cheapest cut to the memory
    # footprint and costs nothing here (the heavy work is in subprocesses anyway).
    MALLOC_ARENA_MAX=2

# System engines the app shells out to:
#   libreoffice-writer  -> Word<->PDF conversion (the primary engine)
#   tesseract + gs      -> ocrmypdf, for the local /api/ocr/run path
#   fonts-*             -> without these LibreOffice substitutes fonts and the
#                          converted layout drifts (wrong line breaks, wrong widths)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libreoffice-core \
        default-jre-headless \
        tesseract-ocr \
        ghostscript \
        fonts-dejavu \
        fonts-liberation \
        fonts-crosextra-carlito \
        fonts-crosextra-caladea \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# LibreOffice needs a writable HOME for its per-run profile; the app needs its
# own job dirs. Run unprivileged.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/temp /app/outputs \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

# --timeout 300 matches SOFFICE_TIMEOUT: a shorter gunicorn timeout would kill a
# conversion LibreOffice is still legitimately working on.
# --max-requests recycles a worker periodically. PyMuPDF/pdfplumber hold on to
# memory across large documents, so a long-lived worker's RSS only ever climbs;
# recycling caps it. The jitter stops both workers restarting at the same moment.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", \
     "--workers", "2", "--threads", "4", "--timeout", "300", \
     "--max-requests", "100", "--max-requests-jitter", "20", \
     "app:app"]
