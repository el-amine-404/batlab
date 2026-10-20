# videodrop (docs/services.md): the page, built with Node, and the server on a
# slim Python with ffmpeg (Debian's) and Deno. Base images, Deno and yt-dlp
# are pinned in compose/versions.env, where Renovate keeps them current;
# compose passes them in.
ARG BASE_IMAGE
ARG NODE_IMAGE
ARG DENO_IMAGE

# Deno, the JavaScript runtime yt-dlp answers YouTube's challenge with.
FROM ${DENO_IMAGE} AS deno

FROM ${NODE_IMAGE} AS page
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY frontend/ ./
RUN npm run build

FROM ${BASE_IMAGE} AS app
ARG YTDLP_VERSION
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg joins a video's streams and embeds its cover, subtitles and tags.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
COPY --from=deno /usr/bin/deno /usr/local/bin/deno

WORKDIR /opt/videodrop
COPY backend/requirements.txt ./
# yt-dlp with its default extras: yt-dlp-ejs among them, which answers
# YouTube's challenge with Deno.
RUN python -m venv .venv \
 && .venv/bin/pip install --require-hashes -r requirements.txt \
 && .venv/bin/pip install "yt-dlp[default]==${YTDLP_VERSION}"
COPY backend/videodrop ./videodrop
# batlab's shared code (lib/batlab-common), handed in by compose as the build
# context "common".
COPY --from=common batlab_common /opt/batlab-common/batlab_common
COPY --from=page /src/dist ./static
RUN .venv/bin/python -m compileall -q videodrop /opt/batlab-common

ENV VIDEODROP_STATIC=/opt/videodrop/static \
    PYTHONPATH=/opt/videodrop:/opt/batlab-common \
    PATH=/opt/videodrop/.venv/bin:$PATH
USER 1000:1000
EXPOSE 8840
ENTRYPOINT ["/opt/videodrop/.venv/bin/python", "-m", "videodrop"]

# The tests, against the image as built: videodrop's, and batlab-common's of
# the accounts and the egress proxy it runs on. A failing test fails the
# build, so `make up` never deploys it. The image keeps none of them, only the mark.
FROM app AS tested
COPY --chown=1000:1000 backend/tests /tmp/videodrop-tests/tests
COPY --from=common --chown=1000:1000 tests /tmp/videodrop-tests/common
RUN cd /tmp/videodrop-tests \
 && { { HOME=/tmp python -m unittest discover -s tests && HOME=/tmp python -m unittest discover -s common -p "test_[ae]*.py"; } \
      > log 2>&1 || { tail -n 80 log; exit 1; }; } \
 && grep -E "^(Ran|OK)" log && touch passed

FROM app
COPY --from=tested /tmp/videodrop-tests/passed /opt/videodrop/.tests-passed
