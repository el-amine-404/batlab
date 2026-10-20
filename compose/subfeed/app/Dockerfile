# subfeed (docs/services.md): the page, built with Node, and the server on a
# slim Python. The base images are pinned in compose/versions.env, where
# Renovate keeps them current; compose passes them in.
ARG BASE_IMAGE
ARG NODE_IMAGE

FROM ${NODE_IMAGE} AS page
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY frontend/ ./
RUN npm run build

FROM ${BASE_IMAGE} AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/subfeed
COPY backend/requirements.txt ./
RUN python -m venv .venv \
 && .venv/bin/pip install --require-hashes -r requirements.txt
COPY backend/subfeed ./subfeed
# batlab's shared code (lib/batlab-common), handed in by compose as the build
# context "common".
COPY --from=common batlab_common /opt/batlab-common/batlab_common
COPY --from=page /src/dist ./static
RUN .venv/bin/python -m compileall -q subfeed /opt/batlab-common

ENV SUBFEED_STATIC=/opt/subfeed/static \
    PYTHONPATH=/opt/subfeed:/opt/batlab-common \
    PATH=/opt/subfeed/.venv/bin:$PATH
# `docker exec subfeed admin …`: its people and API keys from the shell (subfeed/admin.py), the same
# command in every app whatever its package is called.
RUN printf '#!/bin/sh\nexec env PYTHONPATH=/opt/subfeed:/opt/batlab-common /opt/subfeed/.venv/bin/python -m subfeed.admin "$@"\n' \
      > /usr/local/bin/admin && chmod 755 /usr/local/bin/admin
USER 1000:1000
EXPOSE 8830
ENTRYPOINT ["/opt/subfeed/.venv/bin/python", "-m", "subfeed"]

# The tests, against the image as built: subfeed's, and batlab-common's of the
# accounts it runs on. A failing test fails the build, so `make up` never
# deploys it. The image keeps none of them, only the mark.
FROM app AS tested
COPY --chown=1000:1000 backend/tests /tmp/subfeed-tests/tests
COPY --from=common --chown=1000:1000 tests /tmp/subfeed-tests/common
RUN cd /tmp/subfeed-tests \
 && { { HOME=/tmp python -m unittest discover -s tests && HOME=/tmp python -m unittest discover -s common -p "test_[ae]*.py"; } \
      > log 2>&1 || { tail -n 80 log; exit 1; }; } \
 && admin --help > /dev/null
 && grep -E "^(Ran|OK)" log && touch passed

FROM app
COPY --from=tested /tmp/subfeed-tests/passed /opt/subfeed/.tests-passed
