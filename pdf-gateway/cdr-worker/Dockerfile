# syntax=docker/dockerfile:1.7
FROM debian:13.1-slim@sha256:a347fd7510ee31a84387619a492ad6c8eb0af2f2682b916ff3e643eb076f925a
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pika python3-psycopg2 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10004 cdr \
 && useradd --uid 10004 --gid 10004 --no-create-home --shell /usr/sbin/nologin cdr
COPY --chown=10004:10004 worker.py /opt/pdf-gateway/worker.py
USER 10004:10004
WORKDIR /work
ENTRYPOINT ["python3", "/opt/pdf-gateway/worker.py"]
