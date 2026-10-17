# clipfeed (docs/music.md): communities' music video links, checked, as an
# API and Atom feeds. The base image and ytmusicapi are pinned in
# compose/versions.env, where Renovate keeps them current; compose passes them in.
ARG BASE_IMAGE

FROM ${BASE_IMAGE}
ARG YTMUSICAPI_VERSION
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/clipfeed
COPY requirements.txt ./
RUN python -m venv .venv \
 && .venv/bin/pip install --require-hashes -r requirements.txt \
 && .venv/bin/pip install --no-deps "ytmusicapi==${YTMUSICAPI_VERSION}"
COPY clipfeed ./clipfeed
# batlab's shared code (lib/batlab-common), handed in by compose as the build
# context "common".
COPY --from=common batlab_common /opt/batlab-common/batlab_common
RUN .venv/bin/python -m compileall -q clipfeed /opt/batlab-common

ENV PYTHONPATH=/opt/batlab-common
USER 1000:1000
EXPOSE 8830
ENTRYPOINT ["/opt/clipfeed/.venv/bin/python", "-m", "clipfeed"]
