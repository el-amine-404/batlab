#!/usr/bin/env bash
# M6: exactly-once Paperless projection against a real, disposable Paperless-ngx
# 3.3.2 (own SQLite, media and broker; OCR off). Runs on Lab2 through
# tools/lab2_test.sh (the Paperless image is already there; images are prebuilt).
# The production Paperless is never contacted.
set -Eeuo pipefail
trap 'echo "test_m6_paperless.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
compose="$root/pdf-gateway/tests/docker-compose.m6-paperless.yml"
scratch="$(mktemp -d)"
export TEST_STORAGE="$scratch/storage" TEST_ARCHIVE="$scratch/archive" TEST_ADMIN_TOKEN="$(openssl rand -hex 32)"
export TEST_PAPERLESS_SECRET="$(openssl rand -hex 32)" TEST_PAPERLESS_PASSWORD="$(openssl rand -hex 16)" TEST_PAPERLESS_TOKEN=unset
export ANALYSIS_IMAGE=pdf-gateway-e2e-analysis-worker
dc=(docker compose -f "$compose")
psql=("${dc[@]}" exec -T postgres psql -U pdf_gateway -d pdf_gateway -v ON_ERROR_STOP=1 -q)
base=http://127.0.0.1:18389
paperless=http://127.0.0.1:18300
limits=(--network none --read-only --tmpfs /tmp:size=32m --cap-drop ALL --security-opt no-new-privileges --cpus 1 --memory 512m)
. "$root/pdf-gateway/tests/lib_m3.sh"   # thermal_guard, package_temp

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then "${dc[@]}" logs --no-color --tail=30 paperless-adapter archive-worker >&2 || true; fi
  "${dc[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  docker run --rm --network none --user 10005:10005 -v "$TEST_ARCHIVE:/archive" --entrypoint sh pdf-gateway-e2e-archive-worker \
    -c 'rm -rf /archive/archive /archive/staging /archive/manifests/*' >/dev/null 2>&1 || true
  docker run --rm --network none -v "$TEST_STORAGE/api:/data" --entrypoint sh pdf-gateway-e2e-archive-worker -c 'rm -rf /data/*' >/dev/null 2>&1 || true
  chmod -R u+rwX "$scratch" 2>/dev/null || true
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

eq() { [[ "$1" == "$2" ]] || { echo "expected [$2], got [$1]" >&2; return 1; }; }
q() { "${psql[@]}" -Atc "$1"; }
scan_id() { echo "scan-01J000000000000000000000$1"; }
doc_id() { echo "doc-01J000000000000000000000$1"; }
json() { python3 -c "import json,sys; v=json.load(sys.stdin); print(eval(sys.argv[1], {}, {'v': v}))" "$1"; }
pl() { curl -sS -H "Authorization: Token $TEST_PAPERLESS_TOKEN" "$@"; }
pl_count() { pl "$paperless/api/documents/?page_size=1" | json 'v["count"]'; }
marker_count() {
  pl "$paperless/api/documents/?custom_field_query=$(python3 -c 'import json,sys,urllib.parse; print(urllib.parse.quote(json.dumps(["gateway_document_id","exact",sys.argv[1]])))' "$1")" | json 'v["count"]'
}
import_state() { q "SELECT status||','||COALESCE(paperless_document_id::text,'-') FROM paperless_imports WHERE document_id='$(doc_id "$1")'"; }
wait_imported() {
  for _ in $(seq 1 "${2:-90}"); do [[ "$(import_state "$1")" == IMPORTED,* ]] && return 0; sleep 2; done
  echo "doc $1 import state: $(import_state "$1")" >&2; return 1
}

thermal_guard
mkdir -p "$TEST_STORAGE"/{fixtures,cdr-output,text-extracts,api/quarantine,api/multipart} "$TEST_ARCHIVE/manifests"
chmod 0777 "$TEST_ARCHIVE" "$TEST_ARCHIVE/manifests" "$TEST_STORAGE/api" "$TEST_STORAGE/api/quarantine" "$TEST_STORAGE/api/multipart"
for image in api archive-worker analysis-worker paperless-adapter; do docker image inspect "pdf-gateway-e2e-$image" >/dev/null; done
python3 -I "$root/pdf-gateway/tests/make_fixtures.py" "$TEST_STORAGE/fixtures"
chmod 0755 "$TEST_STORAGE/fixtures" "$TEST_STORAGE/cdr-output" "$TEST_STORAGE/text-extracts"; chmod 0644 "$TEST_STORAGE/fixtures"/*
cat >"$TEST_ARCHIVE/manifests/rules.json" <<'JSON'
{"schemaVersion": "1.0.0", "revision": 1, "updatedAt": "2026-10-25T12:00:00Z", "rules": [
 {"id": "example-telecom-invoice", "issuer": "Example Telecom", "match": {"all": ["example telecom"], "any": ["invoice"]},
  "category": "Finance", "folder": "Invoices", "documentType": "invoice", "title": "Internet Invoice", "tags": ["finance", "internet", "invoice"]}]}
JSON
chmod 0644 "$TEST_ARCHIVE/manifests/rules.json"

"${dc[@]}" up -d postgres rabbitmq valkey paperless
"${dc[@]}" up -d --wait api
"${dc[@]}" up -d --wait archive-worker
for _ in $(seq 1 120); do curl -fsS -o /dev/null "$paperless/api/" 2>/dev/null || [[ "$(curl -sS -o /dev/null -w '%{http_code}' "$paperless/api/" 2>/dev/null)" == 302 ]] && break; sleep 2; done
export TEST_PAPERLESS_TOKEN="$(curl -sS -X POST -d "username=gateway-test&password=$TEST_PAPERLESS_PASSWORD" "$paperless/api/token/" | json 'v["token"]')"
"${dc[@]}" up -d paperless-adapter
admin="$(q "SELECT id FROM users WHERE username='admin'")"
. "$root/pdf-gateway/tests/lib_m4.sh"

# --- Import exactly once --------------------------------------------------------
seed 70 invoice-en.pdf ARCHIVE_AND_IMPORT; seed 71 statement-fr.pdf ARCHIVE_AND_IMPORT; seed 72 unfamiliar.pdf ARCHIVE_AND_IMPORT
for n in 70 71 72; do wait_imported "$n"; done
eq "$(pl_count)" 3
for n in 70 71 72; do eq "$(marker_count "$(doc_id $n)")" 1; done
eq "$(q "SELECT string_agg(import_status,',' ORDER BY id) FROM scan_jobs WHERE id IN ('$(scan_id 70)','$(scan_id 71)','$(scan_id 72)')")" IMPORTED,IMPORTED,IMPORTED
p70="$(q "SELECT paperless_document_id FROM paperless_imports WHERE document_id='$(doc_id 70)'")"
eq "$(pl "$paperless/api/documents/$p70/" | json 'v["title"]+"|"+str(v["created_date"])')" "Internet Invoice - October 2026|2026-10-15"
eq "$(pl "$paperless/api/tags/?page_size=100" | json '",".join(sorted(t["name"] for t in v["results"]))')" "finance,internet,invoice"
# The mapping is written back into the authoritative sidecar by the archive's single writer.
for _ in $(seq 1 30); do [[ "$(q "SELECT sidecar->'integrations'->>'paperlessDocumentId' FROM archive_documents WHERE document_id='$(doc_id 70)'")" == "$p70" ]] && break; sleep 1; done
eq "$(q "SELECT sidecar->'integrations'->>'paperlessDocumentId' FROM archive_documents WHERE document_id='$(doc_id 70)'")" "$p70"

# --- Replay: re-delivering every request creates nothing new ----------------------
for n in 70 71 72; do q "INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (gen_random_uuid(),'DOCUMENT','$(doc_id $n)','PAPERLESS_QUEUED',jsonb_build_object('documentId','$(doc_id $n)'))"; done
sleep 15
eq "$(pl_count)" 3

# --- Crash after upload, before the task was recorded ----------------------------
seed 73 multipage.pdf ARCHIVE
for _ in $(seq 1 60); do [[ "$(q "SELECT count(*) FROM archive_documents WHERE document_id='$(doc_id 73)'")" == 1 ]] && break; sleep 1; done
path73="$TEST_ARCHIVE/archive/$(q "SELECT archive_path||'/'||pdf_file FROM archive_documents WHERE document_id='$(doc_id 73)'")"
field="$(pl "$paperless/api/custom_fields/?name__iexact=gateway_document_id" | json 'v["results"][0]["id"]')"
# Exactly what the adapter sends, sent "by a previous attempt that died".
cp "$path73" "$scratch/$(doc_id 73).pdf"
pl -F "document=@$scratch/$(doc_id 73).pdf;type=application/pdf" -F "title=Unclassified document" \
   -F "custom_fields={\"$field\": \"$(doc_id 73)\"}" "$paperless/api/documents/post_document/" >/dev/null
q "INSERT INTO paperless_imports(document_id,artifact_sha256,status,upload_started_at) SELECT document_id,sha256,'PENDING',now() FROM archive_documents WHERE document_id='$(doc_id 73)'"
q "INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (gen_random_uuid(),'DOCUMENT','$(doc_id 73)','PAPERLESS_QUEUED',jsonb_build_object('documentId','$(doc_id 73)'))"
wait_imported 73
eq "$(marker_count "$(doc_id 73)")" 1
eq "$(pl_count)" 4

# --- Paperless outage: the archive keeps growing; import resumes, no duplicates ------
"${dc[@]}" stop paperless >/dev/null 2>&1
seed 74 invoice-en.pdf ARCHIVE_AND_IMPORT
for _ in $(seq 1 60); do [[ "$(q "SELECT status FROM scan_jobs WHERE id='$(scan_id 74)'")" == READY ]] && break; sleep 1; done
eq "$(q "SELECT status FROM scan_jobs WHERE id='$(scan_id 74)'")" READY          # archive independent of Paperless
for _ in $(seq 1 60); do [[ "$(import_state 74)" == FAILED_RETRYABLE,- ]] && break; sleep 1; done
eq "$(import_state 74)" "FAILED_RETRYABLE,-"
eq "$(q "SELECT last_error FROM paperless_imports WHERE document_id='$(doc_id 74)'")" PAPERLESS_UNAVAILABLE
[[ "$(q "SELECT count(*) FROM outbox_events WHERE aggregate_id='$(doc_id 74)' AND event_type='PAPERLESS_QUEUED' AND published_at IS NULL AND available_at > now()")" -ge 1 ]]
"${dc[@]}" start paperless >/dev/null 2>&1
for _ in $(seq 1 120); do [[ "$(curl -sS -o /dev/null -w '%{http_code}' "$paperless/api/documents/" -H "Authorization: Token $TEST_PAPERLESS_TOKEN" 2>/dev/null)" == 200 ]] && break; sleep 2; done
# Reconcile now rather than wait for the backoff (the scheduled retry would also do it).
"${dc[@]}" run --rm --no-deps -T paperless-adapter resync | json 'str(v["checked"])' >/dev/null
wait_imported 74 120
eq "$(marker_count "$(doc_id 74)")" 1
eq "$(pl_count)" 5

# --- Sidecar edits propagate (title and tags), the PDF is untouched -------------------
curl -sS -o /dev/null -X PATCH -H "Authorization: Bearer $TEST_ADMIN_TOKEN" -H 'Content-Type: application/json' -H 'If-Match: "rev-2"' \
  -d '{"title":"Internet Invoice - October 2026 (paid)","tags":["finance","internet","invoice","paid"]}' "$base/api/v1/archive/$(doc_id 70)/metadata"
for _ in $(seq 1 60); do [[ "$(pl "$paperless/api/documents/$p70/" | json 'v["title"]')" == "Internet Invoice - October 2026 (paid)" ]] && break; sleep 2; done
eq "$(pl "$paperless/api/documents/$p70/" | json 'v["title"]')" "Internet Invoice - October 2026 (paid)"
paid="$(pl "$paperless/api/tags/?name__iexact=paid" | json 'v["results"][0]["id"]')"
pl "$paperless/api/documents/$p70/" | json 'v["tags"]' | grep -q "\b$paid\b"
eq "$(marker_count "$(doc_id 70)")" 1

# --- Deleted in Paperless: resync restores it; nothing else changes -------------------
p72="$(q "SELECT paperless_document_id FROM paperless_imports WHERE document_id='$(doc_id 72)'")"
eq "$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE -H "Authorization: Token $TEST_PAPERLESS_TOKEN" "$paperless/api/documents/$p72/")" 204
eq "$("${dc[@]}" run --rm --no-deps -T paperless-adapter resync | json '",".join(v["missing"])')" "$(doc_id 72)"
for _ in $(seq 1 90); do [[ "$(import_state 72)" == IMPORTED,* && "$(import_state 72)" != "IMPORTED,$p72" ]] && break; sleep 2; done
eq "$(marker_count "$(doc_id 72)")" 1
eq "$(pl_count)" 5

# --- API request for a document archived without import ------------------------------
eq "$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $TEST_ADMIN_TOKEN" "$base/api/v1/scans/$(scan_id 71)/paperless-import")" 202
seed 75 statement-fr.pdf ARCHIVE
for _ in $(seq 1 60); do [[ "$(q "SELECT status FROM scan_jobs WHERE id='$(scan_id 75)'")" == READY ]] && break; sleep 1; done
eq "$(curl -sS -H "Authorization: Bearer $TEST_ADMIN_TOKEN" "$base/api/v1/scans/$(scan_id 75)/paperless-import" | json 'v["status"]')" NOT_REQUESTED
eq "$(curl -sS -X POST -H "Authorization: Bearer $TEST_ADMIN_TOKEN" "$base/api/v1/scans/$(scan_id 75)/paperless-import" | json 'v["status"]')" PENDING
wait_imported 75
eq "$(pl_count)" 6
eq "$(curl -sS -H "Authorization: Bearer $TEST_ADMIN_TOKEN" "$base/api/v1/scans/$(scan_id 75)/paperless-import" | json 'v["status"]')" IMPORTED

# --- The Paperless token never leaves the adapter's environment ------------------------
! curl -sS -H "Authorization: Bearer $TEST_ADMIN_TOKEN" "$base/api/v1/scans/$(scan_id 70)/paperless-import" | grep -q "$TEST_PAPERLESS_TOKEN"
! "${dc[@]}" logs --no-color 2>/dev/null | grep -q "$TEST_PAPERLESS_TOKEN"
! "${dc[@]}" exec -T postgres pg_dump -U pdf_gateway pdf_gateway 2>/dev/null | grep -q "$TEST_PAPERLESS_TOKEN"
! grep -rq "$TEST_PAPERLESS_TOKEN" "$TEST_ARCHIVE" 2>/dev/null
q "SELECT right(document_id,2), status, paperless_document_id, synced_revision, attempts, COALESCE(last_error,'-') FROM paperless_imports ORDER BY document_id"
echo "M6 Paperless projection: exactly-once import, replay, crash recovery, outage/resume, metadata sync, resync and secret handling passed"
