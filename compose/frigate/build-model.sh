#!/usr/bin/env bash
# Builds a YOLOv9 ONNX model for Frigate's OpenVINO detector into
# $VOLUMES_ROOT/frigate/config/models (/config/models in Frigate), following
# Frigate 0.18's docs (docs/data/object_detectors_models.yaml, "yolov9").
# Run on the server:
#   compose/frigate/build-model.sh [SIZE] [IMG]     # default: s 320
# SIZE is t, s, m, c or e (bigger: more accurate, slower); IMG is 320 or 640.
# The build downloads Python, PyTorch and the weights (a few GB, cached by
# Docker); the result is one small .onnx file. Point config.yml's model.path
# at it and restart Frigate.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
set -a; . "$here/../.env"; set +a
size="${1:-s}"
img="${2:-320}"
out="$VOLUMES_ROOT/frigate/config/models"
mkdir -p "$out"

# Host network: the host resolves through AdGuard on 127.0.0.1, which build
# containers on Docker's default bridge cannot reach.
docker build --network host --build-arg MODEL_SIZE="$size" --build-arg IMG_SIZE="$img" \
  --output "$out" -f- "$here" << 'EOF'
FROM python:3.11 AS build
RUN apt-get update && apt-get install --no-install-recommends -y cmake libgl1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /bin/
WORKDIR /yolov9
ADD https://github.com/WongKinYiu/yolov9.git .
RUN uv pip install --system -r requirements.txt
RUN uv pip install --system onnx==1.18.0 onnxruntime onnx-simplifier==0.4.* onnxscript
ARG MODEL_SIZE
ARG IMG_SIZE
ADD https://github.com/WongKinYiu/yolov9/releases/download/v0.1/yolov9-${MODEL_SIZE}-converted.pt yolov9-${MODEL_SIZE}.pt
RUN sed -i "s/ckpt = torch.load(attempt_download(w), map_location='cpu')/ckpt = torch.load(attempt_download(w), map_location='cpu', weights_only=False)/g" models/experimental.py
RUN python3 export.py --weights ./yolov9-${MODEL_SIZE}.pt --imgsz ${IMG_SIZE} --simplify --include onnx
FROM scratch
ARG MODEL_SIZE
ARG IMG_SIZE
COPY --from=build /yolov9/yolov9-${MODEL_SIZE}.onnx /yolov9-${MODEL_SIZE}-${IMG_SIZE}.onnx
EOF
ls -l "$out/yolov9-$size-$img.onnx"
