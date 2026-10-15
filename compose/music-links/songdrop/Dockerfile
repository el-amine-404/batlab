# songdrop (docs/music.md): the page and the docs site, built with Node, and
# the server on spotDL's image, which brings spotDL, ffmpeg and Deno. Both spotDL's and
# songdrop's yt-dlp are the one pinned in compose/versions.env, for the same
# reason as compose/music-links/spotdl/Dockerfile.
ARG BASE_IMAGE
ARG NODE_IMAGE

FROM ${NODE_IMAGE} AS page
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY frontend/ ./
RUN npm run build

# The docs (/docs): VitePress, and Scalar for the API reference.
FROM ${NODE_IMAGE} AS docs
WORKDIR /src
COPY docs/package.json docs/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY docs/ ./
RUN npm run build

FROM ${BASE_IMAGE}
ARG YTDLP_VERSION
USER root
RUN uv pip install --python /app/.venv/bin/python --no-cache "yt-dlp[default]==${YTDLP_VERSION}"

WORKDIR /opt/songdrop
COPY backend/requirements.txt ./
# The server's own virtualenv: spotDL pins old FastAPI and friends in its own.
RUN uv venv --python /usr/local/bin/python3 .venv \
 && uv pip install --python .venv/bin/python --no-cache --require-hashes -r requirements.txt \
 && uv pip install --python .venv/bin/python --no-cache "yt-dlp[default]==${YTDLP_VERSION}"
COPY backend/app ./app
# batlab's shared code (lib/batlab-common), handed in by compose as the build
# context "common"; on PYTHONPATH for both interpreters (spotDL's runs
# runners/verify.py).
COPY --from=common batlab_common /opt/batlab-common/batlab_common
COPY --from=page /src/dist ./static
COPY --from=docs /src/.vitepress/dist ./static/docs
RUN .venv/bin/python -m compileall -q app /opt/batlab-common

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SONGDROP_STATIC=/opt/songdrop/static \
    PYTHONPATH=/opt/batlab-common
USER spotdl
EXPOSE 8810
ENTRYPOINT ["/opt/songdrop/.venv/bin/python", "-m", "app"]
