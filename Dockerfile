# Slim rather than alpine: reportlab ships manylinux wheels that alpine's musl
# cannot use, so alpine would mean compiling it from source.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so edits to the source do not invalidate the layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app/ includes app/services/geocode_cache.json, pre-warmed by
# scripts/warm_geocode_cache.py. Baking it in is what makes the map usable on
# hosting with no persistent disk: without it every cold start would re-resolve
# every place at one lookup per second.
COPY app/ ./app/
COPY static/ ./static/

# Default to the baked-in cache. Point GEOCODE_CACHE_PATH at a mounted volume
# instead if the host gives you one, and newly resolved places will persist.
ENV GEOCODE_CACHE_PATH=/app/app/services/geocode_cache.json

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

ENV PORT=8000 \
    COOKIE_SECURE=true
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz').read()"

# One worker on purpose. Sessions live in this process's memory, so a second
# worker would answer requests for trees it has never seen. Scaling out means
# moving app/sessions.py behind Redis first.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
