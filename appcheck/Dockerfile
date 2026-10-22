# The checks' tools, in an image of their own: they run on the apps' sealed
# test network (run.sh), never beside the apps themselves.
ARG BASE_IMAGE=python:3.13.15-slim-trixie
FROM ${BASE_IMAGE}
RUN pip install --no-cache-dir --root-user-action=ignore \
      "schemathesis==4.32.3" "httpx==0.28.1" "bandit==1.9.4" "pip-audit==2.10.1"
WORKDIR /appcheck
COPY load.py ./
ENTRYPOINT []
