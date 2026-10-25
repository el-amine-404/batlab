#!/usr/bin/env bash
# M3 slice 2: exact-final-byte validation of CDR output and the database
# release guard. Runs on Lab2 (real Dangerzone, ClamAV, qpdf, YARA, poppler).
set -Eeuo pipefail
trap 'echo "test_m3_validation.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
compose="$root/compose/pdf-gateway/app/tests/docker-compose.m3-validation.yml"
scratch="$(mktemp -d)"
export TEST_STORAGE="$scratch/storage"
. "$root/compose/pdf-gateway/app/tests/lib_m3.sh"

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then
    docker compose -f "$compose" logs --no-color --tail=60 cdr-worker validation-worker >&2 || true
    journalctl --user -u "$unit" --no-pager -n 20 >&2 2>/dev/null || true
  fi
  stop_provider
  docker compose -f "$compose" down --volumes --remove-orphans >/dev/null 2>&1 || true
  chmod -R u+rwX "$scratch" 2>/dev/null || true
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

thermal_guard
prepare_storage
# A real Dangerzone output produced by this account, used where a test must
# show that only one property (hash, scanner availability) makes it fail.
dangerzone-cli -o "$TEST_STORAGE/fixtures/dz-unsigned.pdf" "$TEST_STORAGE/fixtures/unsigned.pdf" >/dev/null 2>&1
cp "$TEST_STORAGE/fixtures/dz-unsigned.pdf" "$TEST_STORAGE/fixtures/dz-tampered.pdf"
printf '%% appended after CDR\n' >>"$TEST_STORAGE/fixtures/dz-tampered.pdf"
chmod 0644 "$TEST_STORAGE/fixtures"/*
build_image cdr-worker
build_image analysis-worker

docker compose -f "$compose" up -d --wait postgres rabbitmq clamav
wait_clamav_fresh
docker compose -f "$compose" up -d --wait cdr-worker validation-worker
start_provider 300
q "INSERT INTO users(id,username,role) VALUES ('$USER_ID','validation','ADMIN')"

# Real CDR outputs must pass.
seed_eligible 30 unsigned.pdf
seed_eligible 31 multipage.pdf
seed_eligible 32 handwritten.pdf
# Compromised-converter outputs and integrity failures must not.
seed_output 33 unsigned.pdf eicar-in-stream.pdf
seed_output 34 unsigned.pdf active-js.pdf
seed_output 35 unsigned.pdf multipage.pdf
seed_output 36 unsigned.pdf dz-unsigned.pdf dz-tampered.pdf
seed_output 38 unsigned.pdf blank-image.pdf
seed_output 39 unsigned.pdf dz-unsigned.pdf
pump 30 31 32 33 34 35 36 38 39

fv() { q "SELECT $2 FROM final_validations WHERE scan_id='$(scan_id "$1")' ORDER BY attempt DESC LIMIT 1"; }
for n in 30 31 32 39; do
  eq "$(sv $n "status||','||policy||','||artifact_state||','||detection")" "AWAITING_ARCHIVE,ALLOW_SANITIZED,FINAL_VALIDATED,NO_THREATS_DETECTED"
  eq "$(fv $n "verdict||','||failure_codes::text||','||policy_version")" "PASS,[],final-policy-1"
  [[ "$(fv $n "engines->>'clamav'")" == ClamAV* ]]
  eq "$(q "SELECT count(*) FROM outbox_events WHERE aggregate_id='$(scan_id $n)' AND event_type='ARCHIVE_QUEUED'")" 1
  eq "$(q "SELECT state FROM artifacts WHERE scan_id='$(scan_id $n)' AND kind='CDR_OUTPUT'")" FINAL_VALIDATED
  eq "$(q "SELECT count(*) FROM artifacts WHERE scan_id='$(scan_id $n)' AND kind='VALIDATION_REPORT' AND bytes>100")" 1
done
eq "$(q "SELECT details->>'outputSha256' = (SELECT output_sha256 FROM final_validations WHERE scan_id='$(scan_id 30)') FROM job_steps WHERE scan_id='$(scan_id 30)' AND step_type='CDR'")" t

expect_denied() {
  local n=$1 status=$2 policy=$3 code=$4
  eq "$(sv $n "status||','||policy||','||artifact_state")" "$status,$policy,ORIGINAL_QUARANTINED"
  [[ "$(fv $n verdict)" != PASS ]]
  fv $n "failure_codes::text" | grep -q "\"$code\"" || { echo "scan $n lacks $code: $(fv $n "failure_codes::text")" >&2; return 1; }
  eq "$(q "SELECT state FROM artifacts WHERE scan_id='$(scan_id $n)' AND kind='CDR_OUTPUT'")" REJECTED
  eq "$(q "SELECT count(*) FROM outbox_events WHERE aggregate_id='$(scan_id $n)' AND event_type='ARCHIVE_QUEUED'")" 0
}
expect_denied 33 BLOCKED BLOCK MALWARE_DETECTED
expect_denied 34 REVIEW_REQUIRED REVIEW_REQUIRED FORBIDDEN_STRUCTURE
expect_denied 35 REVIEW_REQUIRED REVIEW_REQUIRED PAGE_COUNT_MISMATCH
expect_denied 36 REVIEW_REQUIRED REVIEW_REQUIRED OUTPUT_HASH_MISMATCH
expect_denied 38 REVIEW_REQUIRED REVIEW_REQUIRED OUTPUT_ALL_PAGES_BLANK
eq "$(sv 33 detection)" MALICIOUS

# Replay of a completed validation is a no-op.
q "INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (gen_random_uuid(),'SCAN','$(scan_id 30)','VALIDATION_QUEUED',jsonb_build_object('scanId','$(scan_id 30)'))"
relay VALIDATION_QUEUED; sleep 3
eq "$(q "SELECT count(*) FROM final_validations WHERE scan_id='$(scan_id 30)'")" 1

# Scanner unavailable: UNKNOWN, a delayed retry, and no release.
docker compose -f "$compose" stop clamav >/dev/null 2>&1
seed_output 37 unsigned.pdf dz-unsigned.pdf
relay VALIDATION_QUEUED
for _ in $(seq 1 60); do [[ "$(q "SELECT count(*) FROM final_validations WHERE scan_id='$(scan_id 37)'")" == 1 ]] && break; sleep 1; done
eq "$(fv 37 verdict)" UNKNOWN
fv 37 "failure_codes::text" | grep -q CLAMAV_UNAVAILABLE
eq "$(sv 37 "status||','||artifact_state")" "VALIDATING_OUTPUT,SANITIZED_STAGED"
eq "$(q "SELECT count(*) FROM outbox_events WHERE aggregate_id='$(scan_id 37)' AND event_type='VALIDATION_QUEUED' AND published_at IS NULL AND available_at > now() + interval '200 seconds'")" 1

# Database guard: no shortcut from an initial CLEAN result to a release state.
q "INSERT INTO scan_jobs(id,owner_id,status,intent,cdr_mode,display_name,source_bytes,source_sha256,quarantine_object,detection,policy)
   VALUES ('$(scan_id 40)','$USER_ID','AWAITING_POLICY','ARCHIVE','RASTER','clean.pdf',1,'$(printf 'a%.0s' {1..64})','$(object_id 40)','CLEAN','CDR_ELIGIBLE')"
denied() { if q "$1" >/dev/null 2>&1; then echo "guard allowed: $1" >&2; return 1; fi; }
denied "UPDATE scan_jobs SET status='READY' WHERE id='$(scan_id 40)'"
denied "UPDATE scan_jobs SET status='AWAITING_ARCHIVE' WHERE id='$(scan_id 40)'"
denied "UPDATE scan_jobs SET policy='ALLOW_SANITIZED' WHERE id='$(scan_id 40)'"
denied "UPDATE scan_jobs SET artifact_state='PUBLISHED' WHERE id='$(scan_id 40)'"
denied "INSERT INTO scan_jobs(id,owner_id,status,intent,cdr_mode,display_name,source_bytes,source_sha256,quarantine_object,policy) VALUES ('$(scan_id 41)','$USER_ID','READY','ARCHIVE','RASTER','x',1,'$(printf 'b%.0s' {1..64})','$(object_id 41)','ALLOW_SANITIZED')"
# A rejected output cannot be promoted, and a PASS cannot be forged for it.
denied "UPDATE artifacts SET state='FINAL_VALIDATED' WHERE scan_id='$(scan_id 34)' AND kind='CDR_OUTPUT'"
denied "INSERT INTO final_validations(id,scan_id,attempt,artifact_id,source_sha256,output_sha256,verdict,detection,policy_decision,failure_codes,policy_version,engines,report_sha256)
        SELECT gen_random_uuid(),scan_id,9,id,'$(printf 'c%.0s' {1..64})',sha256,'PASS','NO_THREATS_DETECTED','ALLOW_SANITIZED','[]','final-policy-1','{}',sha256 FROM artifacts WHERE scan_id='$(scan_id 34)' AND kind='CDR_OUTPUT'"
denied "UPDATE final_validations SET verdict='PASS' WHERE scan_id='$(scan_id 35)'"
denied "UPDATE artifacts SET sha256='$(printf 'd%.0s' {1..64})' WHERE scan_id='$(scan_id 30)' AND kind='CDR_OUTPUT'"
# Validated bytes cannot be pointed at a different file either.
denied "UPDATE artifacts SET storage_object='cdr-output/other.pdf' WHERE scan_id='$(scan_id 30)' AND kind='CDR_OUTPUT'"

released="$(q "SELECT string_agg(right(id,2),',' ORDER BY id) FROM scan_jobs WHERE status IN ('AWAITING_ARCHIVE','ARCHIVING','READY') OR policy='ALLOW_SANITIZED' OR artifact_state IN ('FINAL_VALIDATED','PUBLISHED')")"
eq "$released" "30,31,32,39"
q "SELECT right(s.id,2), s.status, f.verdict, f.failure_codes FROM scan_jobs s LEFT JOIN final_validations f ON f.scan_id=s.id ORDER BY s.id"
echo "M3 final-byte validation, release guard, typed denial, scanner-unavailable retry, and replay tests passed"
