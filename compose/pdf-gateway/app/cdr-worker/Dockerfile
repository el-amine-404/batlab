# syntax=docker/dockerfile:1.29
FROM debian:13.8-slim@sha256:8aa5a7bf5a1955bddbe7d74fcdd0b91ab30e74362a8b73af672b0481b35b44a0
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
