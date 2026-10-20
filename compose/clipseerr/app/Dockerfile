# clipseerr (docs/music.md): the page, built with Node, and the server on a
# slim Python. Base images and ytmusicapi are pinned in compose/versions.env,
# where Renovate keeps them current; compose passes them in.
ARG BASE_IMAGE
ARG NODE_IMAGE
ARG DENO_IMAGE

# Deno, the JavaScript runtime yt-dlp answers YouTube's challenge with (the previews).
FROM ${DENO_IMAGE} AS deno

FROM ${NODE_IMAGE} AS page
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY frontend/ ./
RUN npm run build

FROM ${BASE_IMAGE} AS app
ARG YTMUSICAPI_VERSION
ARG YTDLP_VERSION
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg cuts a video's preview clip (catalog/preview.py); Debian's, which a
# new base image brings up to date.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
COPY --from=deno /usr/bin/deno /usr/local/bin/deno

WORKDIR /opt/clipseerr
COPY backend/requirements.txt ./
# yt-dlp with its default extras: yt-dlp-ejs among them, which answers
# YouTube's challenge with Deno.
RUN python -m venv .venv \
 && .venv/bin/pip install --require-hashes -r requirements.txt \
 && .venv/bin/pip install --no-deps "ytmusicapi==${YTMUSICAPI_VERSION}" \
 && .venv/bin/pip install "yt-dlp[default]==${YTDLP_VERSION}"
COPY backend/app ./app
# batlab's shared code (lib/batlab-common), handed in by compose as the build
# context "common".
COPY --from=common batlab_common /opt/batlab-common/batlab_common
COPY --from=page /src/dist ./static
RUN .venv/bin/python -m compileall -q app /opt/batlab-common

ENV CLIPSEERR_STATIC=/opt/clipseerr/static \
    PYTHONPATH=/opt/batlab-common \
    DENO_DIR=/tmp/deno \
    XDG_CACHE_HOME=/state/cache
# `docker exec clipseerr admin …`: its people and API keys from the shell (app/admin.py), the same
# command in every app whatever its package is called.
RUN printf '#!/bin/sh\nexec env PYTHONPATH=/opt/clipseerr:/opt/batlab-common /opt/clipseerr/.venv/bin/python -m app.admin "$@"\n' \
      > /usr/local/bin/admin && chmod 755 /usr/local/bin/admin
USER 1000:1000
EXPOSE 8820
ENTRYPOINT ["/opt/clipseerr/.venv/bin/python", "-m", "app"]

# The tests, against the image as built: clipseerr's, and batlab-common's of
# the accounts it runs on. A failing test fails the build, so `make up` never
# deploys it. The image keeps none of them, only the mark.
FROM app AS tested
USER root
# Beside the app, where they find it (tests/ imports app from its parent).
COPY --chown=1000:1000 backend/tests /opt/clipseerr/tests
COPY --from=common --chown=1000:1000 tests /opt/clipseerr/common-tests
USER 1000:1000
RUN cd /opt/clipseerr \
 && { { HOME=/tmp .venv/bin/python -m unittest discover -s tests \
        && HOME=/tmp .venv/bin/python -m unittest discover -s common-tests -p "test_[ae]*.py"; } \
      > /tmp/log 2>&1 || { tail -n 80 /tmp/log; exit 1; }; } \
 && admin --help > /dev/null \
 && grep -E "^(Ran|OK)" /tmp/log && touch /tmp/passed

FROM app
COPY --from=tested /tmp/passed /opt/clipseerr/.tests-passed
