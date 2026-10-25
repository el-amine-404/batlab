# syntax=docker/dockerfile:1.7
FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt \
 && groupadd --gid 10007 paperless-adapter \
 && useradd --uid 10007 --gid 10007 --no-create-home --shell /usr/sbin/nologin paperless-adapter
COPY --chown=10007:10007 adapter.py /opt/pdf-gateway/adapter.py
USER 10007:10007
WORKDIR /work
ENTRYPOINT ["python", "/opt/pdf-gateway/adapter.py"]
