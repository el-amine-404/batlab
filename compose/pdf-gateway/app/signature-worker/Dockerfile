# syntax=docker/dockerfile:1.28
FROM python:3.15-slim@sha256:63bb298d2b7b88f176f51b5189f54ce6535c997682ea70885d2132f5d473a8a5
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
