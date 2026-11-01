#!/usr/bin/env bash
# Keeps the agent's model loaded in LM Studio, on the CPU (see model_loaded in agent.py).
set -u
export PATH="$HOME/.lmstudio/bin:$PATH"
model="qwen/qwen3-vl-30b"
lms ps 2>/dev/null | grep -qF "$model" && exit 0
# A second vision model runs the T1000 out of GPU memory.
lms unload --all >/dev/null 2>&1
exec lms load "$model" --gpu off --context-length 16384 -y
