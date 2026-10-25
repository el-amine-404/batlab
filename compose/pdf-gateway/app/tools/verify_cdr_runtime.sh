#!/usr/bin/env bash

# Fail-closed capability probe. A successful conversion is necessary but not
# sufficient: debug evidence must show the expected networkless Podman/gVisor path.
set -Eeuo pipefail

readonly CONTAINERS_CONF="${PDF_CDR_CONTAINERS_CONF:-/etc/batlab/pdf-cdr/containers.conf}"

if [[ ! -r $CONTAINERS_CONF ]]; then
  echo "CDR_UNAVAILABLE: missing dedicated Podman config $CONTAINERS_CONF" >&2
  exit 1
fi
export CONTAINERS_CONF

for command in podman dangerzone-cli dangerzone-image; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "CDR_UNAVAILABLE: missing $command" >&2
    exit 1
  }
done

command -v crun >/dev/null 2>&1 || {
  echo "CDR_UNAVAILABLE: missing outer crun runtime" >&2
  exit 1
}

if [[ ${EUID} -eq 0 ]]; then
  echo "CDR_UNAVAILABLE: Dangerzone must run rootless" >&2
  exit 1
fi

dangerzone-image verify-local >/dev/null

configured_runtime="$(podman info --format '{{.Host.OCIRuntime.Name}}')"
if [[ $configured_runtime != crun ]]; then
  echo "CDR_UNAVAILABLE: outer Podman selected '$configured_runtime', expected 'crun'" >&2
  exit 1
fi

scratch="$(mktemp -d)"
cleanup() { rm -rf -- "$scratch"; }
trap cleanup EXIT
input="$scratch/input.pdf"
output="$scratch/output.pdf"
debug_log="$scratch/debug.log"

# A tiny deterministic one-page PDF; no source corpus bytes are modified or moved.
python3 - "$input" <<'PY'
import pathlib
import sys

objects = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    b"<< /Length 44 >>\nstream\nBT /F1 18 Tf 30 50 Td (CDR isolation test) Tj ET\nendstream",
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
]
data = bytearray(b"%PDF-1.4\n")
offsets = [0]
for number, body in enumerate(objects, 1):
    offsets.append(len(data))
    data.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
xref = len(data)
data.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
for offset in offsets[1:]:
    data.extend(f"{offset:010d} 00000 n \n".encode())
data.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
pathlib.Path(sys.argv[1]).write_bytes(data)
PY

if ! timeout 180 dangerzone-cli --debug -o "$output" "$input" 2>"$debug_log"; then
  sed -E 's/[[:cntrl:]]//g' "$debug_log" | tail -n 40 >&2
  echo "CDR_UNAVAILABLE: test conversion failed" >&2
  exit 1
fi

[[ -s $output ]] || { echo "CDR_UNAVAILABLE: empty output" >&2; exit 1; }
head -c 5 "$output" | grep -qx '%PDF-' || {
  echo "CDR_UNAVAILABLE: output is not a PDF" >&2
  exit 1
}

# Dangerzone's debug output is untrusted text but is adequate capability evidence.
grep -Eiq 'runsc|gvisor' "$debug_log" || {
  echo "CDR_UNAVAILABLE: conversion did not provide gVisor evidence" >&2
  exit 1
}
grep -Eiq '(network[= :]+none|--network=none)' "$debug_log" || {
  echo "CDR_UNAVAILABLE: conversion did not provide network isolation evidence" >&2
  exit 1
}
# The input is streamed over stdin. Any host bind/volume mount would violate the
# quarantine boundary; Dangerzone's private tmpfs cgroup mount is acceptable.
if grep -Eiq -- '(^|[[:space:]])--volume([=[:space:]]|$)|--mount=type=bind' "$debug_log"; then
  echo "CDR_UNAVAILABLE: conversion command exposed a host bind/volume mount" >&2
  exit 1
fi

printf 'CDR_READY dangerzone=%s output_sha256=%s\n' \
  "$(dangerzone-cli --version | tr -d '\n')" \
  "$(sha256sum "$output" | cut -d' ' -f1)"
