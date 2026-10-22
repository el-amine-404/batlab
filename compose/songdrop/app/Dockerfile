# songdrop (docs/music.md): the page, built with Node, and
# the server on spotDL's image, which brings spotDL, ffmpeg and Deno. Both spotDL's and
# songdrop's yt-dlp are the one pinned in compose/versions.env.
ARG BASE_IMAGE
ARG NODE_IMAGE

FROM ${NODE_IMAGE} AS page
# Where the page is in the repo, beside lib/batlab-ui (the context "ui"), so
# its relative path to the shared frame holds here too.
WORKDIR /batlab/compose/songdrop/app/frontend
COPY --from=ui . /batlab/lib/batlab-ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY frontend/ ./
RUN npm run build

FROM ${BASE_IMAGE} AS app
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
COPY --from=page /batlab/compose/songdrop/app/frontend/dist ./static
RUN .venv/bin/python -m compileall -q app /opt/batlab-common

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SONGDROP_STATIC=/opt/songdrop/static \
    PYTHONPATH=/opt/batlab-common
# `docker exec songdrop admin …`: its people and API keys from the shell (app/admin.py), the same
# command in every app whatever its package is called.
RUN printf '#!/bin/sh\nexec env PYTHONPATH=/opt/songdrop:/opt/batlab-common /opt/songdrop/.venv/bin/python -m app.admin "$@"\n' \
      > /usr/local/bin/admin && chmod 755 /usr/local/bin/admin
USER spotdl
EXPOSE 8810
ENTRYPOINT ["/opt/songdrop/.venv/bin/python", "-m", "app"]

# The tests, against the image as built: songdrop's, and batlab-common's of
# the accounts and the egress proxy it runs on. httpx (FastAPI's test client,
# for the API's tests) only here: the image itself keeps none of it, only the
# mark. A failing test fails the build, so `make up` never deploys it.
FROM app AS tested
USER root
RUN uv pip install --python /opt/songdrop/.venv/bin/python --no-cache "httpx==0.28.1"
# Beside the app, where they find it (tests/ imports app from its parent).
COPY --chown=spotdl backend/tests /opt/songdrop/tests
COPY --from=common --chown=spotdl tests /opt/songdrop/common-tests
USER spotdl
RUN cd /opt/songdrop \
 && { { HOME=/tmp .venv/bin/python -m unittest discover -s tests \
        && HOME=/tmp .venv/bin/python -m unittest discover -s common-tests -p "test_[ae]*.py"; } \
      > /tmp/log 2>&1 || { tail -n 80 /tmp/log; exit 1; }; } \
 && SONGDROP_STATE=/tmp/admin-check admin setup-code > /dev/null \
 && grep -E "^(Ran|OK)" /tmp/log && touch /tmp/passed

FROM app
COPY --from=tested /tmp/passed /opt/songdrop/.tests-passed
