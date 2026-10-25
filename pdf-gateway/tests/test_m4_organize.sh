#!/usr/bin/env bash
# M4 slice 2: real native-text extraction (analysis image) and deterministic
# organisation + publish (archive image). Local PC; networkless, capped containers.
set -Eeuo pipefail
trap 'echo "test_m4_organize.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
scratch="$(mktemp -d)"
cleanup() { status=$?; rm -rf -- "$scratch"; exit "$status"; }
trap cleanup EXIT
limits=(--network none --read-only --tmpfs /tmp:size=32m --cap-drop ALL --security-opt no-new-privileges
        --cpus 1 --memory 512m --pids-limit 64 --user "$(id -u):$(id -g)")

docker build -q -t pdf-gateway-m4-test/analysis-worker:local "$root/pdf-gateway/analysis-worker" >/dev/null
docker build -q -f "$root/pdf-gateway/archive-worker/Dockerfile" -t pdf-gateway-m4-test/archive-worker:local "$root/pdf-gateway" >/dev/null
mkdir -p "$scratch/fixtures" "$scratch/texts"
python3 -I "$root/pdf-gateway/tests/make_fixtures.py" "$scratch/fixtures"
for name in invoice-en statement-fr unfamiliar conflicting undated-invoice blank-image malformed large-text; do
  docker run --rm "${limits[@]}" -e PDF_TEXT_MAX_BYTES=16384 -v "$scratch:/data" --entrypoint python3 \
    pdf-gateway-m4-test/analysis-worker:local /opt/pdf-gateway/extract_text.py \
    "/data/fixtures/$name.pdf" "/data/texts/$name.txt" >"$scratch/texts/$name.json"
done
docker run --rm "${limits[@]}" -v "$scratch:/data" -v "$root/pdf-gateway/tests/organize_test.py:/tests/organize_test.py:ro" \
  --entrypoint python pdf-gateway-m4-test/archive-worker:local /tests/organize_test.py /data
