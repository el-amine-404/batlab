# syntax=docker/dockerfile:1.28
FROM python:3.15-slim@sha256:8c13a3f37a6a5b71498228e902c4b9f51c1f7900d49fabf71f9a650c6120e0d0
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt \
 && groupadd --gid 10007 paperless-adapter \
 && useradd --uid 10007 --gid 10007 --no-create-home --shell /usr/sbin/nologin paperless-adapter
COPY --chown=10007:10007 adapter.py notifier.py /opt/pdf-gateway/
# batlab's shared Apprise client (standard library only), for notifier.py.
COPY --from=common --chown=10007:10007 batlab_common/notify.py /opt/pdf-gateway/notify.py
USER 10007:10007
WORKDIR /work
ENTRYPOINT ["python", "/opt/pdf-gateway/adapter.py"]
