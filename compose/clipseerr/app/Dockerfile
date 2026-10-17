# clipseerr (docs/music.md): the page, built with Node, and the server on a
# slim Python. Base images and ytmusicapi are pinned in compose/versions.env,
# where Renovate keeps them current; compose passes them in.
ARG BASE_IMAGE
ARG NODE_IMAGE

FROM ${NODE_IMAGE} AS page
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY frontend/ ./
RUN npm run build

FROM ${BASE_IMAGE}
ARG YTMUSICAPI_VERSION
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/clipseerr
COPY backend/requirements.txt ./
RUN python -m venv .venv \
 && .venv/bin/pip install --require-hashes -r requirements.txt \
 && .venv/bin/pip install --no-deps "ytmusicapi==${YTMUSICAPI_VERSION}"
COPY backend/app ./app
# batlab's shared code (lib/batlab-common), handed in by compose as the build
# context "common".
COPY --from=common batlab_common /opt/batlab-common/batlab_common
COPY --from=page /src/dist ./static
RUN .venv/bin/python -m compileall -q app /opt/batlab-common

ENV CLIPSEERR_STATIC=/opt/clipseerr/static \
    PYTHONPATH=/opt/batlab-common
USER 1000:1000
EXPOSE 8820
ENTRYPOINT ["/opt/clipseerr/.venv/bin/python", "-m", "app"]
