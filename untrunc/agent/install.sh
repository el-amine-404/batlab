#!/usr/bin/env bash
# Explicit opt-in host installation. Does not change GPU drivers or Docker.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/batlab-agent-install"
mkdir -p "$cache_dir"
command -v curl >/dev/null || { echo 'Install curl first.' >&2; exit 1; }
if ! command -v ollama >/dev/null; then
    curl --fail --location --retry 3 https://ollama.com/install.sh -o "$cache_dir/ollama-install.sh"
    sha256sum "$cache_dir/ollama-install.sh" >> "$cache_dir/installers.sha256"
    sh "$cache_dir/ollama-install.sh"
fi
if ! command -v hermes >/dev/null; then
    curl --fail --location --retry 3 https://hermes-agent.nousresearch.com/install.sh -o "$cache_dir/hermes-install.sh"
    sha256sum "$cache_dir/hermes-install.sh" >> "$cache_dir/installers.sha256"
    bash "$cache_dir/hermes-install.sh"
fi
printf 'Installers saved in %s. Restart your shell if the new commands are not on PATH.\n' "$cache_dir"
printf 'Next: make untrunc-agent-setup, then make untrunc-agent-model.\n'
