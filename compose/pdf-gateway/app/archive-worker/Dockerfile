# syntax=docker/dockerfile:1.7
FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# Build context is compose/pdf-gateway/app/ (see Dockerfile.dockerignore) so the contract schema is shared.
COPY archive-worker/requirements.txt /tmp/requirements.txt
# poppler-utils: pdftotext reads the clean copy's OCR text layer (image-only originals).
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/* \
 && python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt \
 && groupadd --gid 10005 archive \
 && useradd --uid 10005 --gid 10005 --no-create-home --shell /usr/sbin/nologin archive
COPY --chown=10005:10005 archive-worker/archive.py archive-worker/organize.py archive-worker/pdf_archive.py archive-worker/worker.py archive-worker/metadata.py archive-worker/reindex_db.py archive-worker/enrichment.py archive-worker/revisions.py /opt/pdf-gateway/
COPY --chown=10005:10005 contracts/archive-metadata.schema.json contracts/taxonomy.schema.json contracts/rules.schema.json /opt/pdf-gateway/
USER 10005:10005
WORKDIR /work
ENTRYPOINT ["python", "/opt/pdf-gateway/pdf_archive.py"]
