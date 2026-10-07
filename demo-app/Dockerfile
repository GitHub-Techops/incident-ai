FROM python:3.12-slim

# Don't write .pyc files; send logs straight to stdout (no buffering),
# so `kubectl logs` shows lines immediately.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The version is baked into the image at build time:
# image incident-demo:v1 always reports "v1".
ARG APP_VERSION=v1
ENV APP_VERSION=${APP_VERSION}

WORKDIR /app

# Dependencies first: this layer is cached and only rebuilt when requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Don't run as root.
RUN useradd --create-home --uid 10001 appuser
USER 10001

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]