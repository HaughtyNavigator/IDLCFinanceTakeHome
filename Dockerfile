# syntax=docker/dockerfile:1
FROM python:3.13-slim

WORKDIR /app

# Don't write .pyc files and don't buffer stdout/stderr (cleaner container logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install dependencies first so this layer is cached unless requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only the application code and static assets — never the whole build
# context (see .dockerignore for what's excluded, e.g. .env, NID_Images/)
COPY app/ ./app/
COPY static/ ./static/

# Run as a non-root user
RUN useradd --create-home --shell /usr/sbin/nologin appuser
USER appuser

EXPOSE 8000

# Basic liveness check using stdlib only (no curl in the slim image)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
