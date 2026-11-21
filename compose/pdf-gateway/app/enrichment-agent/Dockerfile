# syntax=docker/dockerfile:1.29
FROM debian:13.7-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
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
