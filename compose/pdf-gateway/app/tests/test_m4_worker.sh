#!/usr/bin/env bash
# M4 slice 3: the durable archive worker publishes validated CDR output into the
# portable archive and records the index. Local PC; every container is capped.
# Stage-isolated: committed M2/M3 evidence is seeded through the real database
# guards; the CDR chain into the archive is exercised on Lab2 by test_m4_e2e.sh.
set -Eeuo pipefail
trap 'echo "test_m4_worker.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
compose="$root/compose/pdf-gateway/app/tests/docker-compose.m4-worker.yml"
scratch="$(mktemp -d)"
export TEST_STORAGE="$scratch/storage" TEST_ARCHIVE="$scratch/archive"
dc=(docker compose -f "$compose")
psql=("${dc[@]}" exec -T postgres psql -U pdf_gateway -d pdf_gateway -v ON_ERROR_STOP=1 -q)
USER_ID=00000000-0000-0000-0000-000000000001
limits=(--network none --read-only --tmpfs /tmp:size=32m --cap-drop ALL --security-opt no-new-privileges --cpus 1 --memory 512m)

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then "${dc[@]}" logs --no-color --tail=40 archive-worker >&2 || true; fi
  "${dc[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  # Archive files belong to the archive UID; remove them as that UID.
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
sv() { q "SELECT $2 FROM scan_jobs WHERE id='$(scan_id "$1")'"; }
relay() { "${dc[@]}" run --rm --no-deps -T relay ARCHIVE_QUEUED >/dev/null 2>&1; }
# The archive has one writer identity (the worker's UID); the CLI runs as it too.
cli() { docker run --rm "${limits[@]}" --user 10005:10005 -v "$TEST_ARCHIVE:/archive" -v "$scratch/cli:/scratch" pdf-gateway-m4-test/archive-worker:local --root /archive "$@"; }

mkdir -p "$TEST_STORAGE"/{fixtures,cdr-output,text-extracts} "$TEST_ARCHIVE/manifests" "$scratch/cli"
chmod 0777 "$scratch/cli"
chmod 0777 "$TEST_ARCHIVE" "$TEST_ARCHIVE/manifests"
docker build -q -t pdf-gateway-m4-test/analysis-worker:local "$root/compose/pdf-gateway/app/analysis-worker" >/dev/null
docker build -q -f "$root/compose/pdf-gateway/app/archive-worker/Dockerfile" -t pdf-gateway-m4-test/archive-worker:local "$root/compose/pdf-gateway/app" >/dev/null
python3 -I "$root/compose/pdf-gateway/app/tests/make_fixtures.py" "$TEST_STORAGE/fixtures"
chmod 0755 "$TEST_STORAGE"/* && chmod 0644 "$TEST_STORAGE/fixtures"/*
cat >"$TEST_ARCHIVE/manifests/rules.json" <<'JSON'
{"schemaVersion": "1.0.0", "revision": 1, "updatedAt": "2026-10-25T12:00:00Z", "rules": [
 {"id": "example-telecom-invoice", "issuer": "Example Telecom", "match": {"all": ["example telecom"], "any": ["invoice"]},
  "category": "Finance", "folder": "Invoices", "documentType": "invoice", "title": "Internet Invoice", "tags": ["finance", "internet", "invoice"]}]}
JSON
chmod 0644 "$TEST_ARCHIVE/manifests/rules.json"

"${dc[@]}" up -d --wait postgres rabbitmq archive-worker
q "INSERT INTO users(id,username,role) VALUES ('$USER_ID','archive','ADMIN')"

# seed N FIXTURE RETENTION SIGNATURE_STATE SIGNATURE_COUNT
seed() {
  local n=$1 fixture=$2 retention=$3 sig_state=$4 sig_count=$5 scan out out_hash src_hash bytes summary
  scan="$(scan_id "$n")"
  out="cdr-output/$scan-1.pdf"
  cp "$TEST_STORAGE/fixtures/$fixture" "$TEST_STORAGE/$out"; chmod 0644 "$TEST_STORAGE/$out"
  out_hash="$(sha256sum "$TEST_STORAGE/$out" | cut -d' ' -f1)"; bytes="$(stat -c %s "$TEST_STORAGE/$out")"
  src_hash="$(printf 'original-%s' "$out_hash" | sha256sum | cut -d' ' -f1)"
  # Real bounded extraction from the fixture, as the analysis worker does.
  summary="$(docker run --rm "${limits[@]}" --user "$(id -u):$(id -g)" -v "$TEST_STORAGE:/data" --entrypoint python3 \
    pdf-gateway-m4-test/analysis-worker:local /opt/pdf-gateway/extract_text.py "/data/fixtures/$fixture" "/data/text-extracts/$scan.txt")"
  if [[ -f $TEST_STORAGE/text-extracts/$scan.txt ]]; then
    chmod 0644 "$TEST_STORAGE/text-extracts/$scan.txt"
    summary="$(python3 -c 'import json,sys,hashlib; s=json.loads(sys.argv[1]); s["sha256"]=hashlib.sha256(open(sys.argv[2],"rb").read()).hexdigest(); print(json.dumps(s))' "$summary" "$TEST_STORAGE/text-extracts/$scan.txt")"
  fi
  "${psql[@]}" <<SQL
INSERT INTO scan_jobs(id,owner_id,status,intent,cdr_mode,display_name,source_bytes,source_sha256,quarantine_object,detection,policy,artifact_state,original_retention)
VALUES ('$scan','$USER_ID','VALIDATING_OUTPUT','ARCHIVE','RASTER','$fixture',$bytes,'$src_hash','quar-${scan:5}','CLEAN','CDR_ELIGIBLE','SANITIZED_STAGED','$retention');
INSERT INTO job_steps(id,scan_id,step_type,status,attempt,started_at,finished_at,details)
VALUES (gen_random_uuid(),'$scan','ANALYSIS','SUCCEEDED',1,now(),now(),
        jsonb_build_object('policyDecision','CDR_ELIGIBLE','detection','CLEAN','sourceSha256','$src_hash','textExtraction','$summary'::jsonb));
INSERT INTO signature_verifications(id,scan_id,attempt,source_sha256,state,policy_decision,preserve_original,signature_count,report)
VALUES (gen_random_uuid(),'$scan',1,'$src_hash','$sig_state','CDR_REQUIRED',$([[ $sig_count -gt 0 ]] && echo true || echo false),$sig_count,'{}');
INSERT INTO artifacts(id,scan_id,kind,storage_object,sha256,bytes,state) VALUES ('cdr-${scan:5}-1','$scan','CDR_OUTPUT','$out','$out_hash',$bytes,'SANITIZED_STAGED');
INSERT INTO cdr_results(id,scan_id,attempt,status,source_sha256,output_artifact_id,output_sha256,output_bytes,image_digest)
VALUES (gen_random_uuid(),'$scan',1,'SUCCEEDED','$src_hash','cdr-${scan:5}-1','$out_hash',$bytes,'sha256:seeded');
INSERT INTO final_validations(id,scan_id,attempt,artifact_id,source_sha256,output_sha256,verdict,detection,policy_decision,failure_codes,policy_version,engines,report_sha256)
VALUES (gen_random_uuid(),'$scan',1,'cdr-${scan:5}-1','$src_hash','$out_hash','PASS','NO_THREATS_DETECTED','ALLOW_SANITIZED','[]','final-policy-1','{}','$out_hash');
UPDATE artifacts SET state='FINAL_VALIDATED' WHERE id='cdr-${scan:5}-1';
UPDATE scan_jobs SET status='AWAITING_ARCHIVE',detection='NO_THREATS_DETECTED',policy='ALLOW_SANITIZED',artifact_state='FINAL_VALIDATED' WHERE id='$scan';
INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (gen_random_uuid(),'SCAN','$scan','ARCHIVE_QUEUED',jsonb_build_object('scanId','$scan','artifactId','cdr-${scan:5}-1'));
SQL
}
pump() {
  for _ in $(seq 1 60); do
    relay
    [[ "$(q "SELECT count(*) FROM scan_jobs WHERE status IN ('AWAITING_ARCHIVE','ARCHIVING') AND NOT EXISTS (SELECT 1 FROM job_steps s WHERE s.scan_id=scan_jobs.id AND s.step_type='ARCHIVE' AND s.status='FAILED')")" == 0 ]] && return 0
    sleep 1
  done
  q "SELECT id,status FROM scan_jobs" >&2; return 1
}

seed 70 invoice-en.pdf STANDARD UNSIGNED 0
seed 71 unfamiliar.pdf STANDARD UNSIGNED 0
seed 72 visible-only.pdf SIGNED_RESTRICTED SIGNED_VALID_TRUSTED 1
seed 73 statement-fr.pdf STANDARD UNSIGNED 0
printf 'changed after validation' >>"$TEST_STORAGE/cdr-output/$(scan_id 73)-1.pdf"
# 74: a previous attempt published, then died before committing the index.
seed 74 multipage.pdf STANDARD UNSIGNED 0
q "DELETE FROM outbox_events WHERE aggregate_id='$(scan_id 74)'"
python3 - "$scratch/cli/prepublished.json" "$(scan_id 74)" <<'PY'
import json,sys
scan=sys.argv[2]
json.dump({"documentId":"doc-"+scan[5:],"artifactVersion":1,"metadataRevision":1,"title":"Unclassified document","titleSource":"fallback",
 "organization":{"category":"Unsorted","reviewState":"FALLBACK","reason":"NO_RULE_MATCHED"},"tags":[],
 "derivedFrom":{"scanId":scan,"kind":"CDR_RECONSTRUCTION"},"approval":{"policyVersion":"final-policy-1","decision":"ALLOW_SANITIZED","finalReportId":"validation-x-1"},
 "signature":{"originalSigned":False,"viewingCopySigned":False},"integrations":{},"createdAt":"2026-10-25T12:00:00Z","updatedAt":"2026-10-25T12:00:00Z"},open(sys.argv[1],"w"))
PY
cp "$TEST_STORAGE/cdr-output/$(scan_id 74)-1.pdf" "$scratch/cli/prepublished.pdf"
chmod 0644 "$scratch/cli"/prepublished.*
cli publish --pdf /scratch/prepublished.pdf --sha256 "$(sha256sum "$scratch/cli/prepublished.pdf" | cut -d' ' -f1)" --sidecar /scratch/prepublished.json >/dev/null
q "INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (gen_random_uuid(),'SCAN','$(scan_id 74)','ARCHIVE_QUEUED',jsonb_build_object('scanId','$(scan_id 74)'))"
pump

published() {
  local n=$1 category=$2 review=$3
  eq "$(sv "$n" "status||','||artifact_state||','||policy")" "READY,PUBLISHED,ALLOW_SANITIZED"
  eq "$(q "SELECT category||','||review_state FROM archive_documents WHERE scan_id='$(scan_id "$n")'")" "$category,$review"
  eq "$(q "SELECT state FROM artifacts WHERE id='cdr-$(scan_id "$n" | cut -c6-)-1'")" PUBLISHED
  path="$TEST_ARCHIVE/archive/$(q "SELECT archive_path FROM archive_documents WHERE scan_id='$(scan_id "$n")'")"
  file=$(q "SELECT pdf_file FROM archive_documents WHERE scan_id='$(scan_id "$n")'")
  [[ -f "$path/.$file.json" ]]
  eq "$(sha256sum "$path/$file" | cut -d' ' -f1)" \
     "$(sha256sum "$TEST_STORAGE/cdr-output/$(scan_id "$n")-1.pdf" | cut -d' ' -f1)"
}
published 70 Finance RULE
published 71 Unsorted FALLBACK
published 72 Unsorted FALLBACK
published 74 Unsorted FALLBACK
eq "$(q "SELECT archive_path||'/'||pdf_file FROM archive_documents WHERE scan_id='$(scan_id 70)'")" \
   "Finance/Example Telecom/2026-10-15 Internet Invoice - October 2026.pdf"
side70="$TEST_ARCHIVE/archive/Finance/Example Telecom/.2026-10-15 Internet Invoice - October 2026.pdf"
python3 - "$side70.json" <<'PY'
import json,sys
s=json.load(open(sys.argv[1]))
assert s["textExtraction"]["method"]=="native_text" and s["textExtraction"]["source"]=="original", s.get("textExtraction")
assert s["relativeTextPath"]==".2026-10-15 Internet Invoice - October 2026.pdf.txt" and s["tags"]==["finance","internet","invoice"], s
assert s["approval"]=={"policyVersion":"final-policy-1","decision":"ALLOW_SANITIZED","finalReportId":"validation-01J00000000000000000000070-1"}, s["approval"]
assert s["signature"]["originalSigned"] is False and s["signature"]["viewingCopySigned"] is False
PY
grep -q "Total due: 29.99" "$side70.txt"
python3 - "$TEST_ARCHIVE/archive/$(q "SELECT archive_path||'/.'||pdf_file||'.json' FROM archive_documents WHERE scan_id='$(scan_id 72)'")" <<'PY'
import json,sys
s=json.load(open(sys.argv[1]))["signature"]
assert s["originalSigned"] is True and s["viewingCopySigned"] is False and "not signed" in s["summary"] and "SIGNED_VALID_TRUSTED" in s["summary"], s
PY
# Recovery after a crash between publishing and indexing: one document, now indexed.
eq "$(grep -rl --include='.*.pdf.json' '"documentId": "doc-01J00000000000000000000074"' "$TEST_ARCHIVE/archive" | wc -l)" 1
eq "$(q "SELECT details->'organisation'->>'recoveredExistingPublication' FROM job_steps WHERE scan_id='$(scan_id 74)' AND step_type='ARCHIVE' AND status='SUCCEEDED'")" true

# Bytes changed after validation: refused, rejected, nothing published.
eq "$(sv 73 "status||','||policy||','||artifact_state")" "REVIEW_REQUIRED,REVIEW_REQUIRED,ORIGINAL_QUARANTINED"
eq "$(q "SELECT error_code FROM job_steps WHERE scan_id='$(scan_id 73)' AND step_type='ARCHIVE'")" VALIDATED_BYTES_CHANGED
eq "$(q "SELECT count(*) FROM archive_documents WHERE scan_id='$(scan_id 73)'")" 0
eq "$(grep -rl --include='.*.pdf.json' '01J00000000000000000000073' "$TEST_ARCHIVE/archive" | wc -l)" 0

# Replay is a no-op.
q "INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (gen_random_uuid(),'SCAN','$(scan_id 70)','ARCHIVE_QUEUED',jsonb_build_object('scanId','$(scan_id 70)'))"
relay; sleep 2
eq "$(q "SELECT count(*) FROM archive_documents WHERE scan_id='$(scan_id 70)'")" 1
eq "$(q "SELECT count(*) FROM job_steps WHERE scan_id='$(scan_id 70)' AND step_type='ARCHIVE'")" 1

# Invalid rules: not filed with defaults; retried later.
printf '{"schemaVersion": "1.0.0", "revision": 2, "rules": "broken"}' >"$TEST_ARCHIVE/manifests/rules.json"
seed 75 invoice-en.pdf STANDARD UNSIGNED 0
pump
eq "$(sv 75 status)" AWAITING_ARCHIVE
eq "$(q "SELECT error_code FROM job_steps WHERE scan_id='$(scan_id 75)' AND step_type='ARCHIVE'")" ARCHIVE_CONFIG_INVALID
eq "$(q "SELECT count(*) FROM outbox_events WHERE aggregate_id='$(scan_id 75)' AND published_at IS NULL AND available_at > now() + interval '200 seconds'")" 1
eq "$(q "SELECT count(*) FROM archive_documents WHERE scan_id='$(scan_id 75)'")" 0

# Database guard: READY without an archive record is refused.
if q "UPDATE scan_jobs SET status='READY' WHERE id='$(scan_id 75)'" 2>/dev/null; then echo "READY without archive accepted" >&2; exit 1; fi
if q "UPDATE artifacts SET state='PUBLISHED' WHERE id='cdr-$(scan_id 75 | cut -c6-)-1'" 2>/dev/null; then echo "PUBLISHED without version accepted" >&2; exit 1; fi

# The archive is complete and rebuildable on its own.
cli verify | python3 -c 'import json,sys; r=json.load(sys.stdin); assert r["ok"] and r["documents"]==4, r'
cli reindex --catalog /scratch/catalog.sqlite | python3 -c 'import json,sys; r=json.load(sys.stdin); assert r["indexed"]==4 and r["rejected"]==0, r'
q "SELECT right(scan_id,2), category, review_state, archive_path FROM archive_documents ORDER BY scan_id"
echo "M4 archive worker publish, index, guard, recovery, replay, and fail-closed tests passed"
