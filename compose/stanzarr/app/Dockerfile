# Stanzarr (docs/services.md): the page, built with Node, and the server on a
# slim Python with ffmpeg (the editor's waveforms). The base images are pinned
# in compose/versions.env, where Renovate keeps them current; compose passes
# them in.
ARG BASE_IMAGE
ARG NODE_IMAGE

FROM ${NODE_IMAGE} AS page
# Where the page is in the repo, beside lib/batlab-ui (the context "ui"), so
# its relative path to the shared frame holds here too.
WORKDIR /batlab/compose/stanzarr/app/frontend
COPY --from=ui . /batlab/lib/batlab-ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY frontend/ ./
RUN npm run build

FROM ${BASE_IMAGE} AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/stanzarr
COPY backend/requirements.txt ./
RUN python -m venv .venv \
 && .venv/bin/pip install --require-hashes -r requirements.txt
COPY backend/stanzarr ./stanzarr
# batlab's shared code (lib/batlab-common), handed in by compose as the build
# context "common".
COPY --from=common batlab_common /opt/batlab-common/batlab_common
COPY --from=page /batlab/compose/stanzarr/app/frontend/dist ./static
RUN .venv/bin/python -m compileall -q stanzarr /opt/batlab-common

ENV STANZARR_STATIC=/opt/stanzarr/static \
    STANZARR_CONFIG_DIR=/config \
    PYTHONPATH=/opt/stanzarr:/opt/batlab-common \
    PATH=/opt/stanzarr/.venv/bin:$PATH
# `docker exec stanzarr admin …`: its people and API keys from the shell (stanzarr/admin.py), the same
# command in every app whatever its package is called.
RUN printf '#!/bin/sh\nexec env PYTHONPATH=/opt/stanzarr:/opt/batlab-common /opt/stanzarr/.venv/bin/python -m stanzarr.admin "$@"\n' \
      > /usr/local/bin/admin && chmod 755 /usr/local/bin/admin
USER 1000:1000
EXPOSE 6868
ENTRYPOINT ["/opt/stanzarr/.venv/bin/python", "-m", "stanzarr"]

# The tests, against the image as built: Stanzarr's, and batlab-common's of the
# accounts it runs on. A failing test fails the build, so `make up` never
# deploys it. The image keeps none of them, only the mark.
FROM app AS tested
COPY --chown=1000:1000 backend/tests /tmp/stanzarr-tests/tests
COPY --from=common --chown=1000:1000 tests /tmp/stanzarr-tests/common
RUN cd /tmp/stanzarr-tests \
 && { { HOME=/tmp python -m unittest discover -s tests && HOME=/tmp python -m unittest discover -s common -p "test_[ae]*.py"; } \
      > log 2>&1 || { tail -n 80 log; exit 1; }; } \
 && STANZARR_CONFIG_DIR=/tmp/admin-check admin setup-code > /dev/null \
 && grep -E "^(Ran|OK)" log && touch passed

FROM app
COPY --from=tested /tmp/stanzarr-tests/passed /opt/stanzarr/.tests-passed
