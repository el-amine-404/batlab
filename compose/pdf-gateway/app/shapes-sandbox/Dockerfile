# syntax=docker/dockerfile:1.7
# The shapes rebuild's sandbox: runs under gVisor with no network, no capabilities and
# a read-only disk (hosts/lab2/pdf-cdr-provider). Hostile PDF on stdin, plain list on stdout.
FROM debian:13.1-slim@sha256:a347fd7510ee31a84387619a492ad6c8eb0af2f2682b916ff3e643eb076f925a
COPY requirements.txt /tmp/requirements.txt
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip libjpeg-turbo-progs tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng \
 && pip3 install --break-system-packages --no-cache-dir -r /tmp/requirements.txt \
 && apt-get purge -y python3-pip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* /tmp/requirements.txt
COPY extract.py /opt/shapes/extract.py
USER 1000:1000
ENTRYPOINT ["python3", "-I", "/opt/shapes/extract.py"]
