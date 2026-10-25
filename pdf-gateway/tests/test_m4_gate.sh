#!/usr/bin/env bash
# M4 gate: archive API (read-only serving, RBAC, ETag/CAS sidecar edits through
# the single writer) and database loss: PostgreSQL is destroyed and the catalog,
# tags, contents and index are rebuilt from the archive alone. Paperless is not
# part of this stack at all. Local PC; every container is capped.
set -Eeuo pipefail
trap 'echo "test_m4_gate.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
compose="$root/pdf-gateway/tests/docker-compose.m4-gate.yml"
scratch="$(mktemp -d)"
export TEST_STORAGE="$scratch/storage" TEST_ARCHIVE="$scratch/archive" TEST_ADMIN_TOKEN="$(openssl rand -hex 32)"
dc=(docker compose -f "$compose")
psql=("${dc[@]}" exec -T postgres psql -U pdf_gateway -d pdf_gateway -v ON_ERROR_STOP=1 -q)
base=http://127.0.0.1:18289
limits=(--network none --read-only --tmpfs /tmp:size=32m --cap-drop ALL --security-opt no-new-privileges --cpus 1 --memory 512m)

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then "${dc[@]}" logs --no-color --tail=40 api archive-worker >&2 || true; fi
  "${dc[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  docker run --rm --network none --user 10005:10005 -v "$TEST_ARCHIVE:/archive" --entrypoint sh \
    pdf-gateway-m4-test/archive-worker:local -c 'rm -rf /archive/archive /archive/staging /archive/manifests/*' >/dev/null 2>&1 || true
  chmod -R u+rwX "$scratch" 2>/dev/null || true
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

eq() { [[ "$1" == "$2" ]] || { echo "expected [$2], got [$1]" >&2; return 1; }; }
q() { "${psql[@]}" -Atc "$1"; }
scan_id() { echo "scan-01J000000000000000000000$1"; }
api() { curl -sS -H "Authorization: Bearer ${TOKEN:-$TEST_ADMIN_TOKEN}" "$@"; }
code() { curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${TOKEN:-$TEST_ADMIN_TOKEN}" "$@"; }
json() { python3 -c "import json,sys; v=json.load(sys.stdin); print(eval(sys.argv[1], {}, {'v': v}))" "$1"; }
cli() { docker run --rm "${limits[@]}" --user 10005:10005 -v "$TEST_ARCHIVE:/archive" -v "$scratch/cli:/scratch" pdf-gateway-m4-test/archive-worker:local --root /archive "$@"; }

mkdir -p "$TEST_STORAGE"/{fixtures,cdr-output,text-extracts,api} "$TEST_ARCHIVE/manifests" "$scratch/cli"
chmod 0777 "$TEST_ARCHIVE" "$TEST_ARCHIVE/manifests" "$scratch/cli" "$TEST_STORAGE/api"
docker build -q -t pdf-gateway-m4-test/api:local "$root/pdf-gateway/api" >/dev/null
docker build -q -t pdf-gateway-m4-test/analysis-worker:local "$root/pdf-gateway/analysis-worker" >/dev/null
docker build -q -f "$root/pdf-gateway/archive-worker/Dockerfile" -t pdf-gateway-m4-test/archive-worker:local "$root/pdf-gateway" >/dev/null
python3 -I "$root/pdf-gateway/tests/make_fixtures.py" "$TEST_STORAGE/fixtures"
chmod 0755 "$TEST_STORAGE/fixtures" "$TEST_STORAGE/cdr-output" "$TEST_STORAGE/text-extracts"; chmod 0644 "$TEST_STORAGE/fixtures"/*
cat >"$TEST_ARCHIVE/manifests/rules.json" <<'JSON'
{"schemaVersion": "1.0.0", "revision": 1, "updatedAt": "2026-10-25T12:00:00Z", "rules": [
 {"id": "example-telecom-invoice", "issuer": "Example Telecom", "match": {"all": ["example telecom"], "any": ["invoice"]},
  "category": "Finance", "folder": "Invoices", "documentType": "invoice", "title": "Internet Invoice", "tags": ["finance", "internet", "invoice"]}]}
JSON
chmod 0644 "$TEST_ARCHIVE/manifests/rules.json"

"${dc[@]}" up -d --wait postgres rabbitmq api
"${dc[@]}" up -d --wait archive-worker
admin="$(q "SELECT id FROM users WHERE username='admin'")"
# A second USER and a READ_ONLY account, for authorisation checks.
user_token="$(openssl rand -hex 32)"; ro_token="$(openssl rand -hex 32)"
q "INSERT INTO users(id,username,role) VALUES ('00000000-0000-0000-0000-0000000000a2','other','USER'),('00000000-0000-0000-0000-0000000000a3','reader','READ_ONLY')"
q "INSERT INTO api_tokens(id,user_id,token_hash,label) VALUES (gen_random_uuid(),'00000000-0000-0000-0000-0000000000a2','$(printf %s "$user_token" | sha256sum | cut -d' ' -f1)','t'),(gen_random_uuid(),'00000000-0000-0000-0000-0000000000a3','$(printf %s "$ro_token" | sha256sum | cut -d' ' -f1)','t')"

# Stage-isolated seeding through the real guards (see test_m4_worker.sh); the
# API's own outbox publisher delivers ARCHIVE_QUEUED to the archive worker.
seed() {
  local n=$1 fixture=$2 scan out out_hash src_hash bytes summary
  scan="$(scan_id "$n")"; out="cdr-output/$scan-1.pdf"
  cp "$TEST_STORAGE/fixtures/$fixture" "$TEST_STORAGE/$out"; chmod 0644 "$TEST_STORAGE/$out"
  out_hash="$(sha256sum "$TEST_STORAGE/$out" | cut -d' ' -f1)"; bytes="$(stat -c %s "$TEST_STORAGE/$out")"
  src_hash="$(printf 'original-%s' "$out_hash" | sha256sum | cut -d' ' -f1)"
  summary="$(docker run --rm "${limits[@]}" --user "$(id -u):$(id -g)" -v "$TEST_STORAGE:/data" --entrypoint python3 \
    pdf-gateway-m4-test/analysis-worker:local /opt/pdf-gateway/extract_text.py "/data/fixtures/$fixture" "/data/text-extracts/$scan.txt")"
  [[ -f $TEST_STORAGE/text-extracts/$scan.txt ]] && chmod 0644 "$TEST_STORAGE/text-extracts/$scan.txt"
  "${psql[@]}" <<SQL
INSERT INTO scan_jobs(id,owner_id,status,intent,cdr_mode,display_name,source_bytes,source_sha256,quarantine_object,detection,policy,artifact_state)
VALUES ('$scan','$admin','VALIDATING_OUTPUT','ARCHIVE','RASTER','$fixture',$bytes,'$src_hash','quar-${scan:5}','CLEAN','CDR_ELIGIBLE','SANITIZED_STAGED');
INSERT INTO job_steps(id,scan_id,step_type,status,attempt,started_at,finished_at,details)
VALUES (gen_random_uuid(),'$scan','ANALYSIS','SUCCEEDED',1,now(),now(),jsonb_build_object('policyDecision','CDR_ELIGIBLE','detection','CLEAN','sourceSha256','$src_hash','textExtraction','$summary'::jsonb));
INSERT INTO signature_verifications(id,scan_id,attempt,source_sha256,state,policy_decision,preserve_original,signature_count,report)
VALUES (gen_random_uuid(),'$scan',1,'$src_hash','UNSIGNED','CDR_REQUIRED',false,0,'{}');
INSERT INTO artifacts(id,scan_id,kind,storage_object,sha256,bytes,state) VALUES ('cdr-${scan:5}-1','$scan','CDR_OUTPUT','$out','$out_hash',$bytes,'SANITIZED_STAGED');
INSERT INTO cdr_results(id,scan_id,attempt,status,source_sha256,output_artifact_id,output_sha256,output_bytes,image_digest)
VALUES (gen_random_uuid(),'$scan',1,'SUCCEEDED','$src_hash','cdr-${scan:5}-1','$out_hash',$bytes,'sha256:seeded');
INSERT INTO final_validations(id,scan_id,attempt,artifact_id,source_sha256,output_sha256,verdict,detection,policy_decision,failure_codes,policy_version,engines,report_sha256)
VALUES (gen_random_uuid(),'$scan',1,'cdr-${scan:5}-1','$src_hash','$out_hash','PASS','NO_THREATS_DETECTED','ALLOW_SANITIZED','[]','final-policy-1','{}','$out_hash');
UPDATE artifacts SET state='FINAL_VALIDATED' WHERE id='cdr-${scan:5}-1';
UPDATE scan_jobs SET status='AWAITING_ARCHIVE',detection='NO_THREATS_DETECTED',policy='ALLOW_SANITIZED',artifact_state='FINAL_VALIDATED' WHERE id='$scan';
INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (gen_random_uuid(),'SCAN','$scan','ARCHIVE_QUEUED',jsonb_build_object('scanId','$scan'));
SQL
}
seed 90 invoice-en.pdf; seed 91 statement-fr.pdf; seed 92 unfamiliar.pdf
for _ in $(seq 1 60); do [[ "$(q "SELECT count(*) FROM scan_jobs WHERE status='READY'")" == 3 ]] && break; sleep 1; done
eq "$(q "SELECT count(*) FROM scan_jobs WHERE status='READY'")" 3
invoice="doc-01J00000000000000000000090"

# --- Read API, authorisation, safe download ----------------------------------
eq "$(api "$base/api/v1/archive" | json 'len(v["items"])')" 3
eq "$(api "$base/api/v1/archive?limit=2" | json 'v["nextCursor"]')" "doc-01J00000000000000000000091"
eq "$(api "$base/api/v1/archive/$invoice" | json 'v["category"]+"|"+v["archivePath"]')" \
   "Finance|Finance/2026/Invoices/2026-10-15 - Example Telecom - Internet Invoice - October 2026 [$invoice]"
eq "$(curl -sS -o /dev/null -w '%{http_code}' "$base/api/v1/archive")" 401
eq "$(TOKEN=$user_token api "$base/api/v1/archive" | json 'len(v["items"])')" 0
eq "$(TOKEN=$user_token code "$base/api/v1/archive/$invoice/file")" 404
# Encoded traversal is refused before routing (firewall 400) or as unknown (404).
[[ "$(code "$base/api/v1/archive/doc-..%2F..%2Fetc/file")" =~ ^(400|404)$ ]]
[[ "$(code "$base/api/v1/archive/..%2F..%2F..%2Fetc%2Fpasswd/file")" =~ ^(400|404)$ ]]
api -D "$scratch/file.headers" -o "$scratch/invoice.pdf" "$base/api/v1/archive/$invoice/file"
eq "$(sha256sum "$scratch/invoice.pdf" | cut -d' ' -f1)" "$(q "SELECT sha256 FROM archive_documents WHERE document_id='$invoice'")"
grep -qi '^content-disposition: attachment; filename="2026-10-15 - Example Telecom - Internet Invoice - October 2026.pdf"' "$scratch/file.headers"
grep -qi '^x-content-type-options: nosniff' "$scratch/file.headers"
grep -qi "^content-security-policy: sandbox; default-src 'none'" "$scratch/file.headers"
grep -qi '^cache-control: private, no-store' "$scratch/file.headers"
api -D "$scratch/meta.headers" -o "$scratch/meta.json" "$base/api/v1/archive/$invoice/metadata"
grep -qi '^etag: "rev-1"' "$scratch/meta.headers"
eq "$(json 'v["documentId"]+"|"+",".join(v["tags"])' <"$scratch/meta.json")" "$invoice|finance,internet,invoice"
grep -qi '^content-disposition: attachment; filename="metadata.json"' <(api -D - -o /dev/null "$base/api/v1/archive/$invoice/sidecar")

# --- Sidecar edits: If-Match CAS, single writer, PDF untouched -----------------
patch() { curl -sS -o "$scratch/patch.json" -w '%{http_code}' -X PATCH -H "Authorization: Bearer ${TOKEN:-$TEST_ADMIN_TOKEN}" \
  -H 'Content-Type: application/json' "${@:2}" -d "$1" "$base/api/v1/archive/$invoice/metadata"; }
eq "$(patch '{"tags":["finance","paid"]}')" 428
eq "$(patch '{"tags":["finance","paid"]}' -H 'If-Match: "rev-7"')" 409
eq "$(patch '{"integrity":{"sha256":"0"}}' -H 'If-Match: "rev-1"')" 400
eq "$(patch '{"tags":["Not Valid"]}' -H 'If-Match: "rev-1"')" 400
eq "$(TOKEN=$ro_token patch '{"tags":["paid"]}' -H 'If-Match: "rev-1"')" 403
eq "$(TOKEN=$user_token patch '{"tags":["paid"]}' -H 'If-Match: "rev-1"')" 404
eq "$(patch '{"tags":["finance","internet","invoice","paid"],"title":"Internet Invoice - October 2026 (paid)"}' -H 'If-Match: "rev-1"')" 202
edit_url="$(json 'v["statusUrl"]' <"$scratch/patch.json")"
for _ in $(seq 1 30); do [[ "$(api "$base$edit_url" | json 'v["status"]')" == APPLIED ]] && break; sleep 1; done
eq "$(api "$base$edit_url" | json 'v["status"]+"|"+str(v["resultRevision"])')" "APPLIED|2"
grep -qi '^etag: "rev-2"' <(api -D - -o "$scratch/meta2.json" "$base/api/v1/archive/$invoice/metadata")
eq "$(json '",".join(v["tags"])+"|"+v["title"]' <"$scratch/meta2.json")" "finance,internet,invoice,paid|Internet Invoice - October 2026 (paid)"
api -o "$scratch/invoice2.pdf" "$base/api/v1/archive/$invoice/file"
eq "$(sha256sum "$scratch/invoice2.pdf" | cut -d' ' -f1)" "$(sha256sum "$scratch/invoice.pdf" | cut -d' ' -f1)"
eq "$(patch '{"tags":["stale"]}' -H 'If-Match: "rev-1"')" 409
cli verify | json 'v["ok"]' | grep -qx True

# --- Database loss: destroy PostgreSQL, rebuild everything from the archive ---
before="$(cli reindex --catalog /scratch/before.sqlite | json 'v["indexed"]')"
"${dc[@]}" stop api archive-worker >/dev/null 2>&1
"${dc[@]}" rm -sfv postgres >/dev/null 2>&1
"${dc[@]}" up -d --wait postgres api
eq "$(q "SELECT count(*) FROM archive_documents")" 0
eq "$(q "SELECT count(*) FROM scan_jobs")" 0
# Catalog, tags and contents from files alone (no database, no network).
cli reindex --catalog /scratch/after.sqlite | json 'str(v["indexed"])+"|"+str(v["rejected"])' | grep -qx "$before|0"
python3 - "$scratch/cli/after.sqlite" "$invoice" <<'PY'
import sqlite3,sys
db=sqlite3.connect(sys.argv[1]); doc=sys.argv[2]
assert db.execute("SELECT count(*) FROM documents").fetchone()[0]==3
assert {r[0] for r in db.execute("SELECT tag FROM tags WHERE document_id=?",(doc,))}=={"finance","internet","invoice","paid"}
assert "29.99" in db.execute("SELECT text FROM content WHERE document_id=?",(doc,)).fetchone()[0]
assert db.execute("SELECT metadata_revision,title FROM documents WHERE document_id=?",(doc,)).fetchone()==(2,"Internet Invoice - October 2026 (paid)")
print("catalog rebuilt from archive files alone")
PY
# The operational index from sidecars, into the fresh database.
docker run --rm --network pdf-gateway-m4-gate-test_backend --read-only --tmpfs /tmp:size=16m --cap-drop ALL --user 10005:10005 \
  -e PDF_GATEWAY_DATABASE_PASSWORD=test-database-password -v "$TEST_ARCHIVE:/archive:ro" \
  pdf-gateway-m4-test/archive-worker:local --root /archive reindex-db | json 'str(v["indexed"])+"|"+str(v["ok"])' | grep -qx "3|True"
eq "$(api "$base/api/v1/archive" | json 'len(v["items"])')" 3
eq "$(api "$base/api/v1/archive/$invoice" | json 'str(v["metadataRevision"])+"|"+v["title"]')" "2|Internet Invoice - October 2026 (paid)"
api -o "$scratch/invoice3.pdf" "$base/api/v1/archive/$invoice/file"
eq "$(sha256sum "$scratch/invoice3.pdf" | cut -d' ' -f1)" "$(sha256sum "$scratch/invoice.pdf" | cut -d' ' -f1)"
# Without scan rows, ownership is unknown: only administrators see rebuilt documents.
# (Accounts were lost with the database; recreate the ordinary user first.)
q "INSERT INTO users(id,username,role) VALUES ('00000000-0000-0000-0000-0000000000a2','other','USER')"
q "INSERT INTO api_tokens(id,user_id,token_hash,label) VALUES (gen_random_uuid(),'00000000-0000-0000-0000-0000000000a2','$(printf %s "$user_token" | sha256sum | cut -d' ' -f1)','t')"
eq "$(TOKEN=$user_token api "$base/api/v1/archive" | json 'len(v["items"])')" 0
eq "$(TOKEN=$user_token code "$base/api/v1/archive/$invoice/file")" 404

# --- Tampered archive bytes are never served ----------------------------------
docker run --rm --network none --user 10005:10005 -v "$TEST_ARCHIVE:/archive" --entrypoint sh pdf-gateway-m4-test/archive-worker:local \
  -c 'f=$(find /archive/archive -path "*doc-01J00000000000000000000091]*" -name "*.pdf"); printf "X" >> "$f"'
eq "$(code "$base/api/v1/archive/doc-01J00000000000000000000091/file")" 409
echo "M4 gate: archive API, RBAC, safe downloads, CAS edits, and rebuild after PostgreSQL loss passed"
