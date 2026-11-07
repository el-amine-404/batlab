# syntax=docker/dockerfile:1.28
# The shapes rebuild's sandbox: runs under gVisor with no network, no capabilities and
# a read-only disk (hosts/lab2/pdf-cdr-provider). Hostile PDF on stdin, plain list on stdout.
FROM debian:13.7-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
COPY requirements.txt /tmp/requirements.txt
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip libjpeg-turbo-progs tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng \
 && pip3 install --break-system-packages --no-cache-dir -r /tmp/requirements.txt \
 && apt-get purge -y python3-pip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* /tmp/requirements.txt
COPY extract.py /opt/shapes/extract.py
USER 1000:1000
ENTRYPOINT ["python3", "-I", "/opt/shapes/extract.py"]
