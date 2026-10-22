# Stanzarr: the interface is built with Node, then served by the Python app.
ARG NODE_IMAGE=node:22.20.0-bookworm-slim
ARG PYTHON_IMAGE=python:3.13.15-slim-trixie@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b

FROM ${NODE_IMAGE} AS interface
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STANZARR_CONFIG_DIR=/config STANZARR_LIBRARY=/music STANZARR_PORT=6868
# ffmpeg draws the editor's waveforms.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/ ./
COPY --from=interface /src/backend/stanzarr/static ./stanzarr/static
RUN pip install . && rm -rf build *.egg-info
# Runs as any user given with --user / user:; this one when none is.
RUN useradd --system --uid 1000 --home /config stanzarr && mkdir -p /config && chown 1000:1000 /config
USER 1000:1000
VOLUME /config
EXPOSE 6868
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:6868/health', timeout=4)"
CMD ["stanzarr"]
