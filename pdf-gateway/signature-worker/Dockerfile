# syntax=docker/dockerfile:1.7
FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt \
 && groupadd --gid 10003 signature \
 && useradd --uid 10003 --gid 10003 --no-create-home --shell /usr/sbin/nologin signature
COPY --chown=10003:10003 inspect_signatures.py /opt/pdf-gateway/inspect_signatures.py
COPY --chown=10003:10003 worker.py /opt/pdf-gateway/worker.py
USER 10003:10003
WORKDIR /work
ENTRYPOINT ["python", "/opt/pdf-gateway/inspect_signatures.py"]
