# Base image pinned in compose/versions.env (IMAGE_/TAG_TV_REMOTE_BASE), where
# Renovate keeps it current; compose passes it in.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# Wheels only, every file checked against requirements.txt's hashes.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes --only-binary=:all: -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY app/ /app/
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://{os.environ['LISTEN_ADDR']}:{os.environ['PORT']}/healthz\", timeout=3)"]
CMD ["python", "/app/server.py"]
