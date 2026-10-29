ARG PYTHON_IMAGE=python:3.13-slim-trixie
ARG NODE_IMAGE=node:24-alpine
ARG CADDY_IMAGE=caddy:2-alpine

FROM ${PYTHON_IMAGE} AS boundary-model
ARG DOCALIGNER_URL="https://drive.usercontent.google.com/download?id=14vUH77v6yGg7zFctUgcT6BzV5Iisg4Dl&export=download&confirm=t"
ARG DOCALIGNER_SHA256=7f9f5a8935b2eb22b3ee0245d34996063f54562df390d34714af2d76928695bc
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && mkdir -p /model \
 && curl --fail --location --silent --show-error "$DOCALIGNER_URL" -o /model/docaligner.onnx \
 && echo "$DOCALIGNER_SHA256  /model/docaligner.onnx" | sha256sum -c - \
 && cp /usr/share/common-licenses/Apache-2.0 /model/LICENSE.DocAligner

FROM ${NODE_IMAGE} AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIPS_BLOCK_UNTRUSTED=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends libvips42 libglib2.0-0 tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/scanloom
COPY --from=boundary-model /model/docaligner.onnx ./models/docaligner.onnx
COPY --from=boundary-model /model/LICENSE.DocAligner ./models/LICENSE.DocAligner
COPY models/NOTICE ./models/NOTICE
COPY backend/requirements.txt ./
RUN python -m venv .venv && .venv/bin/pip install --require-hashes -r requirements.txt
COPY backend/scanloom ./scanloom
COPY backend/migrations ./migrations
COPY --from=frontend /src/dist ./static
RUN .venv/bin/python -m compileall -q scanloom
ENV PYTHONPATH=/opt/scanloom PATH=/opt/scanloom/.venv/bin:$PATH SCANLOOM_STATIC=/opt/scanloom/static
USER 11000:11000
EXPOSE 8850
ENTRYPOINT ["python", "-m", "scanloom"]

FROM app AS tested
USER root
COPY backend/tests /tmp/tests
RUN python -m unittest discover -s /tmp/tests -v && touch /tmp/tests-passed
USER 11000:11000

FROM app AS server
COPY --from=tested /tmp/tests-passed /opt/scanloom/.tests-passed

FROM ${CADDY_IMAGE} AS web
USER root
# The upstream binary carries a privileged-port file capability. Scanloom binds
# 8080, so copy it without that xattr; this remains executable with every Linux
# capability dropped and no-new-privileges enabled.
RUN cp /usr/bin/caddy /usr/local/bin/caddy
COPY frontend/Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend /src/dist /srv
USER 11001:11001
EXPOSE 8080
