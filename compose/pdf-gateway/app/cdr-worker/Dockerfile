# syntax=docker/dockerfile:1.28
FROM debian:13.7-slim@sha256:f6b08b09a05725f9431502d3b5714f5c4576e82c1a84fa1debe0c24a7e0c3ec4
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pika python3-psycopg2 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10004 cdr \
 && groupadd --gid 10010 cdroutput \
 && useradd --uid 10004 --gid 10004 --no-create-home --shell /usr/sbin/nologin cdr
COPY --chown=10004:10004 worker.py /opt/pdf-gateway/worker.py
# Staged output is group-readable only by the validation stage (group 10010).
USER 10004:10010
WORKDIR /work
ENTRYPOINT ["python3", "/opt/pdf-gateway/worker.py"]
