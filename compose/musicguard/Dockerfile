# Base image pinned in compose/versions.env (IMAGE_/TAG_MUSICGUARD_BASE), where
# Renovate keeps it current; compose passes it in.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg probes, decodes and rebuilds every file; Debian's build is the one
# the server's mediascan already trusts.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Wheels only, every file checked against requirements.txt's hashes.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes --only-binary=:all: -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY app/ /app/
COPY tests/ /tests/
USER 10001:10001
# The guard touches /state/heartbeat between files and between passes.
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import os, sys, time; sys.exit(time.time() - os.path.getmtime('/state/heartbeat') > 900)"]
ENTRYPOINT ["python", "/app/guard.py"]
CMD ["run"]
