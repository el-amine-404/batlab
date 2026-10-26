#!/usr/bin/env bash
# M5 slice 3: the real web UI (web container + API + archive worker) in headless
# Firefox at desktop and iPhone sizes. Local PC only; containers are capped.
#   VENV=/path/to/venv-with-selenium compose/pdf-gateway/app/tests/test_m5_ui.sh [SCREENSHOT_DIR]
set -Eeuo pipefail
trap 'echo "test_m5_ui.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
scratch="$(mktemp -d)"
shots="${1:-$scratch/screenshots}"
export TEST_STORAGE="$scratch/storage" TEST_ARCHIVE="$scratch/archive" TEST_ADMIN_TOKEN="$(openssl rand -hex 32)" SE_AVOID_STATS=true
dc=(docker compose -f "$root/compose/pdf-gateway/app/tests/docker-compose.m4-gate.yml" -f "$root/compose/pdf-gateway/app/tests/docker-compose.m5-web.override.yml")
psql=("${dc[@]}" exec -T postgres psql -U pdf_gateway -d pdf_gateway -v ON_ERROR_STOP=1 -q)
limits=(--network none --read-only --tmpfs /tmp:size=32m --cap-drop ALL --security-opt no-new-privileges --cpus 1 --memory 512m)
python="${VENV:?set VENV to a virtualenv with selenium and playwright (firefox installed)}/bin/python"

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then "${dc[@]}" logs --no-color --tail=30 api web >&2 || true; fi
  "${dc[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  docker run --rm --network none --user 10005:10005 -v "$TEST_ARCHIVE:/archive" --entrypoint sh \
    pdf-gateway-m4-test/archive-worker:local -c 'rm -rf /archive/archive /archive/previews /archive/staging /archive/manifests/*' >/dev/null 2>&1 || true
  docker run --rm --network none -v "$TEST_STORAGE/api:/data" --entrypoint sh pdf-gateway-m4-test/archive-worker:local -c 'rm -rf /data/*' >/dev/null 2>&1 || true
  chmod -R u+rwX "$scratch" 2>/dev/null || true
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

q() { "${psql[@]}" -Atc "$1"; }
scan_id() { echo "scan-01J000000000000000000000$1"; }
mkdir -p "$TEST_STORAGE"/{fixtures,cdr-output,text-extracts,api/quarantine,api/multipart} "$TEST_ARCHIVE/manifests" "$shots"
chmod 0777 "$TEST_ARCHIVE" "$TEST_ARCHIVE/manifests" "$TEST_STORAGE/api" "$TEST_STORAGE/api/quarantine" "$TEST_STORAGE/api/multipart"
for image in api analysis-worker; do docker build -q -t "pdf-gateway-m4-test/$image:local" "$root/compose/pdf-gateway/app/$image" >/dev/null; done
docker build -q -f "$root/compose/pdf-gateway/app/archive-worker/Dockerfile" -t pdf-gateway-m4-test/archive-worker:local "$root/compose/pdf-gateway/app" >/dev/null
docker build -q -t pdf-gateway-m5-test/web:local "$root/compose/pdf-gateway/app/web" >/dev/null
python3 -I "$root/compose/pdf-gateway/app/tests/make_fixtures.py" "$TEST_STORAGE/fixtures"
chmod 0755 "$TEST_STORAGE/fixtures" "$TEST_STORAGE/cdr-output" "$TEST_STORAGE/text-extracts"; chmod 0644 "$TEST_STORAGE/fixtures"/*
cat >"$TEST_ARCHIVE/manifests/rules.json" <<'JSON'
{"schemaVersion": "1.0.0", "revision": 1, "updatedAt": "2026-10-25T12:00:00Z", "rules": [
 {"id": "example-telecom-invoice", "issuer": "Example Telecom", "match": {"all": ["example telecom"], "any": ["invoice"]},
  "category": "Finance", "folder": "Invoices", "documentType": "invoice", "title": "Internet Invoice", "tags": ["finance", "internet", "invoice"]}]}
JSON
chmod 0644 "$TEST_ARCHIVE/manifests/rules.json"

"${dc[@]}" up -d --wait postgres rabbitmq api
"${dc[@]}" up -d --wait archive-worker web
admin="$(q "SELECT id FROM users WHERE username='admin'")"
. "$root/compose/pdf-gateway/app/tests/lib_m4.sh"
seed 60 invoice-en.pdf; seed 61 unfamiliar.pdf
printf '%%PDF-1.4 held\n' >"$TEST_STORAGE/api/quarantine/quar-held62.bin"; chmod 0666 "$TEST_STORAGE/api/quarantine/quar-held62.bin"
q "INSERT INTO scan_jobs(id,owner_id,status,intent,cdr_mode,display_name,source_bytes,source_sha256,quarantine_object,detection,policy)
   VALUES ('$(scan_id 62)','$admin','REVIEW_REQUIRED','ARCHIVE','RASTER','<img src=x onerror=alert(1)>.pdf',30,'$(printf 'a%.0s' {1..64})','quar-held62','UNKNOWN','REVIEW_REQUIRED')"
for _ in $(seq 1 60); do [[ "$(q "SELECT count(*) FROM scan_jobs WHERE status='READY'")" == 2 ]] && break; sleep 1; done

"$python" "$root/compose/pdf-gateway/app/tests/ui_test.py" http://localhost:18290 "$TEST_ADMIN_TOKEN" "$TEST_STORAGE/fixtures/invoice-en.pdf" "$shots"
[[ ! -e $TEST_STORAGE/api/quarantine/quar-held62.bin ]]
# Three edits through the UI: the form, an inline change, and its Undo.
[[ "$(q "SELECT metadata_revision FROM archive_documents WHERE scan_id='$(scan_id 60)'")" == 4 ]]
[[ "$(q "SELECT sidecar->'reference'->>'value' FROM archive_documents WHERE scan_id='$(scan_id 60)'")" == 2026-10-4471 ]]
echo "M5 UI end-to-end in Firefox passed (screenshots: $shots)"
