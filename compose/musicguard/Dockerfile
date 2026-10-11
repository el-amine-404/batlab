# Base images pinned in compose/versions.env (IMAGE_/TAG_MUSICGUARD_BASE and
# _RUST), where Renovate keeps them current; compose passes them in.
ARG BASE_IMAGE
ARG RUST_IMAGE
ARG SONGREC_VERSION

# SongRec, the open-source Shazam client, built without its GUI. It is not
# packaged for Debian. Its Linux build links GLib, libsoup, PipeWire and ALSA
# whether or not anything records audio; only the fingerprint request is used.
FROM ${RUST_IMAGE} AS songrec
ARG SONGREC_VERSION
RUN apt-get update \
    && apt-get install -y --no-install-recommends pkg-config clang libclang-dev \
       libglib2.0-dev libsoup-3.0-dev libpipewire-0.3-dev libasound2-dev \
    && rm -rf /var/lib/apt/lists/*
RUN cargo install songrec --version "${SONGREC_VERSION}" --locked --no-default-features -F ffmpeg

FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg probes, decodes and rebuilds every file; Debian's build is the one
# the server's mediascan already trusts.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
       libglib2.0-0t64 libsoup-3.0-0 libpipewire-0.3-0t64 libasound2t64 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=songrec /usr/local/cargo/bin/songrec /usr/local/bin/songrec

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
