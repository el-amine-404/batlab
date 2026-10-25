# syntax=docker/dockerfile:1.7
FROM debian:13.1-slim@sha256:a347fd7510ee31a84387619a492ad6c8eb0af2f2682b916ff3e643eb076f925a
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 tesseract-ocr tesseract-ocr-eng tesseract-ocr-fra tesseract-ocr-ara poppler-utils ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10008 agent \
 && useradd --uid 10008 --gid 10008 --no-create-home --shell /usr/sbin/nologin agent
COPY --chown=10008:10008 agent.py /opt/pdf-gateway/agent.py
USER 10008:10008
WORKDIR /tmp
# Tesseract uses one thread per process here; the container cap bounds total CPU.
ENV OMP_THREAD_LIMIT=1
ENTRYPOINT ["python3", "/opt/pdf-gateway/agent.py"]
