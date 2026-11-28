# syntax=docker/dockerfile:1.29
FROM debian:13.8-slim@sha256:8aa5a7bf5a1955bddbe7d74fcdd0b91ab30e74362a8b73af672b0481b35b44a0
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pil tesseract-ocr tesseract-ocr-eng tesseract-ocr-fra tesseract-ocr-ara poppler-utils ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10008 agent \
 && useradd --uid 10008 --gid 10008 --no-create-home --shell /usr/sbin/nologin agent
COPY --chown=10008:10008 agent.py /opt/pdf-gateway/agent.py
USER 10008:10008
WORKDIR /tmp
# Tesseract uses one thread per process here; the container cap bounds total CPU.
ENV OMP_THREAD_LIMIT=1
ENTRYPOINT ["python3", "/opt/pdf-gateway/agent.py"]
