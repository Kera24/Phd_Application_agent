# ScholarReach FastAPI backend — container image for Render.
# The Streamlit dashboard is deployed separately (Streamlit Community Cloud)
# and reaches this service over HTTPS via the SCHOLARREACH_API env var.

FROM python:3.11-slim-bookworm

# System libraries required by WeasyPrint (one-page research-summary PDFs) + base fonts.
# WeasyPrint imports lazily and failures are caught, but these make PDF generation actually work.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libgdk-pixbuf-2.0-0 \
        libcairo2 \
        libffi8 \
        shared-mime-info \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render injects $PORT at runtime; default to 8000 for local `docker run`.
ENV PORT=8000
EXPOSE 8000

# Shell form so ${PORT} is expanded at runtime.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
