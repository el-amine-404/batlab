#!/usr/bin/env bash
# M4 slice 4: allowlisted embedded metadata. The archive worker writes Title,
# Subject and Keywords into a new copy of a validated CDR output; the real
# validation worker (ClamAV, qpdf, YARA, structure) must pass that new version
# before the archive worker publishes it. Local PC; every container is capped.
set -Eeuo pipefail
trap 'echo "test_m4_metadata.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
compose="$root/compose/pdf-gateway/app/tests/docker-compose.m4-metadata.yml"
scratch="$(mktemp -d)"
export TEST_STORAGE="$scratch/storage" TEST_ARCHIVE="$scratch/archive"
dc=(docker compose -f "$compose")
psql=("${dc[@]}" exec -T postgres psql -U pdf_gateway -d pdf_gateway -v ON_ERROR_STOP=1 -q)
USER_ID=00000000-0000-0000-0000-000000000001
limits=(--network none --read-only --tmpfs /tmp:size=32m --cap-drop ALL --security-opt no-new-privileges --cpus 1 --memory 512m)

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then "${dc[@]}" logs --no-color --tail=40 archive-worker validation-worker >&2 || true; fi
  "${dc[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  docker run --rm --network none --user 10005:10005 -v "$TEST_ARCHIVE:/archive" -v "$TEST_STORAGE:/storage" --entrypoint sh \
    pdf-gateway-m4-test/archive-worker:local -c 'rm -rf /archive/archive /archive/staging /archive/manifests/* /storage/metadata-versions/*' >/dev/null 2>&1 || true
  chmod -R u+rwX "$scratch" 2>/dev/null || true
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

eq() { [[ "$1" == "$2" ]] || { echo "expected [$2], got [$1]" >&2; return 1; }; }
q() { "${psql[@]}" -Atc "$1"; }
scan_id() { echo "scan-01J000000000000000000000$1"; }
sv() { q "SELECT $2 FROM scan_jobs WHERE id='$(scan_id "$1")'"; }
relay() { "${dc[@]}" run --rm --no-deps -T relay ARCHIVE_QUEUED VALIDATION_QUEUED >/dev/null 2>&1; }
as_archive() { docker run --rm "${limits[@]}" --user 10005:10005 --group-add 10010 -v "$TEST_ARCHIVE:/archive" -v "$TEST_STORAGE:/storage:ro" -v "$scratch/cli:/scratch" "$@"; }

mkdir -p "$TEST_STORAGE"/{fixtures,quarantine,cdr-output,text-extracts,metadata-versions,validation-reports} "$TEST_ARCHIVE/manifests" "$scratch/cli"
chmod 0777 "$TEST_ARCHIVE" "$TEST_ARCHIVE/manifests" "$scratch/cli" "$TEST_STORAGE/metadata-versions" "$TEST_STORAGE/validation-reports"
docker build -q -t pdf-gateway-m4-test/analysis-worker:local "$root/compose/pdf-gateway/app/analysis-worker" >/dev/null
docker build -q -f "$root/compose/pdf-gateway/app/archive-worker/Dockerfile" -t pdf-gateway-m4-test/archive-worker:local "$root/compose/pdf-gateway/app" >/dev/null
python3 -I "$root/compose/pdf-gateway/app/tests/make_fixtures.py" "$TEST_STORAGE/fixtures"
chmod 0755 "$TEST_STORAGE/fixtures" "$TEST_STORAGE/quarantine" "$TEST_STORAGE/cdr-output" "$TEST_STORAGE/text-extracts"
chmod 0644 "$TEST_STORAGE/fixtures"/*
cat >"$TEST_ARCHIVE/manifests/rules.json" <<'JSON'
{"schemaVersion": "1.0.0", "revision": 1, "updatedAt": "2026-10-25T12:00:00Z", "rules": [
 {"id": "example-telecom-invoice", "issuer": "Example Telecom", "match": {"all": ["example telecom"], "any": ["invoice"]},
  "category": "Finance", "folder": "Invoices", "documentType": "invoice", "title": "Internet Invoice", "tags": ["finance", "internet", "invoice"]}]}
JSON
chmod 0644 "$TEST_ARCHIVE/manifests/rules.json"

"${dc[@]}" up -d --wait postgres rabbitmq clamav
for _ in $(seq 1 120); do
  "${dc[@]}" exec -T clamav clamdscan --reload >/dev/null 2>&1 || true
  version="$("${dc[@]}" exec -T clamav clamdscan --version 2>/dev/null || true)"
  python3 - "$version" <<'PY' && break
import datetime as dt,sys
p=sys.argv[1].strip().split("/")
raise SystemExit(0 if len(p) > 2 and (dt.datetime.now(dt.timezone.utc)-dt.datetime.strptime(p[2], "%a %b %d %H:%M:%S %Y").replace(tzinfo=dt.timezone.utc)).total_seconds() <= 172800 else 1)
PY
  sleep 2
done
"${dc[@]}" up -d --wait archive-worker validation-worker
q "INSERT INTO users(id,username,role) VALUES ('$USER_ID','metadata','ADMIN')"

# seed N ORIGINAL RETENTION: an original in quarantine, its real text extract,
# and an already-validated image-only "CDR output", all through the real guards.
seed() {
  local n=$1 original=$2 retention=$3 scan out out_hash src_hash bytes summary
  scan="$(scan_id "$n")"; out="cdr-output/$scan-1.pdf"
  cp "$TEST_STORAGE/fixtures/$original" "$TEST_STORAGE/quarantine/quar-${scan:5}.bin"
  cp "$TEST_STORAGE/fixtures/raster-only.pdf" "$TEST_STORAGE/$out"
  chmod 0644 "$TEST_STORAGE/quarantine/quar-${scan:5}.bin" "$TEST_STORAGE/$out"
  src_hash="$(sha256sum "$TEST_STORAGE/quarantine/quar-${scan:5}.bin" | cut -d' ' -f1)"
  out_hash="$(sha256sum "$TEST_STORAGE/$out" | cut -d' ' -f1)"; bytes="$(stat -c %s "$TEST_STORAGE/$out")"
  summary="$(docker run --rm "${limits[@]}" --user "$(id -u):$(id -g)" -v "$TEST_STORAGE:/data" --entrypoint python3 \
    pdf-gateway-m4-test/analysis-worker:local /opt/pdf-gateway/extract_text.py "/data/fixtures/$original" "/data/text-extracts/$scan.txt")"
  chmod 0644 "$TEST_STORAGE/text-extracts/$scan.txt"
  "${psql[@]}" <<SQL
INSERT INTO scan_jobs(id,owner_id,status,intent,cdr_mode,display_name,source_bytes,source_sha256,quarantine_object,detection,policy,artifact_state,original_retention)
VALUES ('$scan','$USER_ID','VALIDATING_OUTPUT','ARCHIVE','RASTER','$original',$(stat -c %s "$TEST_STORAGE/fixtures/$original"),'$src_hash','quar-${scan:5}','CLEAN','CDR_ELIGIBLE','SANITIZED_STAGED','$retention');
INSERT INTO job_steps(id,scan_id,step_type,status,attempt,started_at,finished_at,details)
VALUES (gen_random_uuid(),'$scan','ANALYSIS','SUCCEEDED',1,now(),now(),jsonb_build_object('policyDecision','CDR_ELIGIBLE','detection','CLEAN','sourceSha256','$src_hash','textExtraction','$summary'::jsonb));
INSERT INTO signature_verifications(id,scan_id,attempt,source_sha256,state,policy_decision,preserve_original,signature_count,report)
VALUES (gen_random_uuid(),'$scan',1,'$src_hash','UNSIGNED','CDR_REQUIRED',false,0,'{}');
INSERT INTO artifacts(id,scan_id,kind,storage_object,sha256,bytes,state) VALUES ('cdr-${scan:5}-1','$scan','CDR_OUTPUT','$out','$out_hash',$bytes,'SANITIZED_STAGED');
INSERT INTO cdr_results(id,scan_id,attempt,status,source_sha256,output_artifact_id,output_sha256,output_bytes,image_digest)
VALUES (gen_random_uuid(),'$scan',1,'SUCCEEDED','$src_hash','cdr-${scan:5}-1','$out_hash',$bytes,'sha256:seeded');
INSERT INTO job_steps(id,scan_id,step_type,status,attempt,started_at,finished_at) VALUES (gen_random_uuid(),'$scan','VALIDATION','SUCCEEDED',1,now(),now());
INSERT INTO final_validations(id,scan_id,attempt,artifact_id,source_sha256,output_sha256,verdict,detection,policy_decision,failure_codes,policy_version,engines,report_sha256)
VALUES (gen_random_uuid(),'$scan',1,'cdr-${scan:5}-1','$src_hash','$out_hash','PASS','NO_THREATS_DETECTED','ALLOW_SANITIZED','[]','final-policy-1','{}','$out_hash');
UPDATE artifacts SET state='FINAL_VALIDATED' WHERE id='cdr-${scan:5}-1';
UPDATE scan_jobs SET status='AWAITING_ARCHIVE',detection='NO_THREATS_DETECTED',policy='ALLOW_SANITIZED',artifact_state='FINAL_VALIDATED' WHERE id='$scan';
INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (gen_random_uuid(),'SCAN','$scan','ARCHIVE_QUEUED',jsonb_build_object('scanId','$scan'));
SQL
}
seed 80 invoice-en.pdf STANDARD
seed 81 unfamiliar.pdf SIGNED_RESTRICTED
original_81="$(sha256sum "$TEST_STORAGE/quarantine/quar-01J00000000000000000000081.bin" | cut -d' ' -f1)"
for _ in $(seq 1 120); do
  relay
  [[ "$(q "SELECT count(*) FROM scan_jobs WHERE status NOT IN ('READY','REVIEW_REQUIRED','BLOCKED','FAILED')")" == 0 ]] && break
  sleep 2
done
q "SELECT right(id,2), status, policy, artifact_state FROM scan_jobs ORDER BY id"

for n in 80 81; do
  meta="meta-$(scan_id $n | cut -c6-)-1"; cdr="cdr-$(scan_id $n | cut -c6-)-1"
  eq "$(sv $n "status||','||artifact_state||','||policy")" "READY,PUBLISHED,ALLOW_SANITIZED"
  # Published bytes are the re-validated metadata version, not the CDR output.
  eq "$(q "SELECT artifact_id FROM archive_versions v JOIN archive_documents d USING (document_id) WHERE d.scan_id='$(scan_id $n)'")" "$meta"
  eq "$(q "SELECT kind||','||state||','||parent_artifact_id FROM artifacts WHERE id='$meta'")" "EMBEDDED_METADATA,PUBLISHED,$cdr"
  eq "$(q "SELECT state FROM artifacts WHERE id='$cdr'")" FINAL_VALIDATED
  eq "$(q "SELECT count(*) FROM final_validations WHERE scan_id='$(scan_id $n)' AND verdict='PASS'")" 2
  [[ "$(q "SELECT sha256 FROM artifacts WHERE id='$meta'")" != "$(q "SELECT sha256 FROM artifacts WHERE id='$cdr'")" ]]
done
eq "$(sha256sum "$TEST_STORAGE/quarantine/quar-01J00000000000000000000081.bin" | cut -d' ' -f1)" "$original_81"

# The embedded values are exactly the allowlisted, sidecar-consistent fields.
for n in 80 81; do
  path="/archive/archive/$(q "SELECT archive_path FROM archive_documents WHERE scan_id='$(scan_id $n)'")"
  as_archive --entrypoint python pdf-gateway-m4-test/archive-worker:local -c '
import json,sys,pikepdf
d=sys.argv[1]; s=json.load(open(d+"/metadata.json"))
with pikepdf.open(d+"/"+s["relativePdfPath"]) as pdf:
    info={str(k):str(v) for k,v in pdf.docinfo.items()}
    assert "/Metadata" not in pdf.Root and "/Info" not in pdf.Root, "XMP or catalog Info survived"
    assert pdf.is_linearized, "published PDF is not linearized (fast web view)"
expected={"/Title":s["title"],"/Producer":"PDF Gateway"}
if s.get("issuer"): expected["/Author"]=s["issuer"]["value"]
subject=[{"invoice":"Invoice"}.get(s.get("documentType"), s.get("documentType")), (s.get("issuer") or {}).get("value"),
         (s.get("documentDate") or {}).get("value"), ("no. "+s["reference"]["value"]) if s.get("reference") else None]
if any(subject): expected["/Subject"]=" · ".join(x for x in subject if x)
if s.get("tags"): expected["/Keywords"]=", ".join(s["tags"])
assert info==expected, (info, expected)
assert s["embeddedMetadata"]["fields"]==sorted(k[1:] for k in expected), s["embeddedMetadata"]
assert s["signature"]["viewingCopySigned"] is False
print(json.dumps(info))' "$path"
done

# The writer strips untrusted metadata carriers from its input. The dirty input
# is a well-formed PDF (built with pikepdf) carrying XMP, a spoofed Author/Title,
# a custom info key and MuPDF's catalog /Info.
cp "$TEST_STORAGE/fixtures/raster-only.pdf" "$scratch/cli/raster-only.pdf"
chmod 0644 "$scratch/cli/raster-only.pdf"
as_archive --entrypoint python pdf-gateway-m4-test/archive-worker:local -c '
import pikepdf
with pikepdf.open("/scratch/raster-only.pdf") as pdf:
    pdf.docinfo["/Author"] = "Spoofed Author"; pdf.docinfo["/Title"] = "Spoofed"; pdf.docinfo["/EvilKey"] = "1"
    pdf.Root["/Metadata"] = pdf.make_stream(b"<x:xmpmeta xmlns:x=\"adobe:ns:meta/\">Spoofed</x:xmpmeta>")
    pdf.save("/scratch/dirty.pdf")'
as_archive --entrypoint python pdf-gateway-m4-test/archive-worker:local -c '
import sys; sys.path.insert(0,"/opt/pdf-gateway")
import metadata, pikepdf
from pathlib import Path
metadata.OUTPUT_GROUP=10005
metadata.write(Path("/scratch/dirty.pdf"), Path("/scratch/clean.pdf"), {"/Title":"T","/Producer":metadata.PRODUCER})
info=metadata.read(Path("/scratch/clean.pdf"))
assert info=={"/Title":"T","/Producer":metadata.PRODUCER}, info
data=open("/scratch/clean.pdf","rb").read()
assert b"Spoofed" not in data and b"EvilKey" not in data and b"MuPDF" not in data, "old metadata bytes survived"
print("writer stripped spoofed Author/Title, custom key and catalog Info")'

# The final validator independently refuses non-allowlisted metadata.
validate() {  # validate FILE SOURCE -> report on stdout; never trips the ERR trap
  local status=0
  "${dc[@]}" run --rm --no-deps -T -v "$scratch/cli:/scratch:ro" --entrypoint python3 validation-worker /opt/pdf-gateway/final_validate.py \
    "/scratch/$1" --expected-sha256 "$(sha256sum "$scratch/cli/$1" | cut -d' ' -f1)" \
    --source "/var/lib/pdf-gateway/quarantine/$2" 2>"$scratch/validate.err" || status=$?
  [[ -s $scratch/validate.err ]] && grep -q Traceback "$scratch/validate.err" && { cat "$scratch/validate.err" >&2; return 1; }
  return 0
}
# Positive: MuPDF-style inline catalog /Info with a string Producer passes.
cp "$TEST_STORAGE/fixtures/unsigned.pdf" "$TEST_STORAGE/quarantine/one-page.bin"; chmod 0644 "$TEST_STORAGE/quarantine/one-page.bin"
validate raster-only.pdf one-page.bin >"$scratch/clean-report.json"
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["verdict"]=="PASS", r["failureCodes"]; print("inline catalog Info passes")' "$scratch/clean-report.json"
# Negative: XMP, spoofed Author and a custom key are refused.
validate dirty.pdf one-page.bin >"$scratch/dirty-report.json"
python3 - "$scratch/dirty-report.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
assert r["verdict"]=="FAIL" and "INFO_NOT_ALLOWLISTED" in r["failureCodes"] and "FORBIDDEN_STRUCTURE" in r["failureCodes"], r["failureCodes"]
print("validator refused:", r["failureCodes"])
PY

# Guard: a metadata version cannot be passed without a validated parent.
q "INSERT INTO scan_jobs(id,owner_id,status,intent,cdr_mode,display_name,source_bytes,source_sha256,quarantine_object)
   VALUES ('$(scan_id 82)','$USER_ID','VALIDATING_OUTPUT','ARCHIVE','RASTER','x',1,'$(printf 'e%.0s' {1..64})','quar-x82')"
q "INSERT INTO artifacts(id,scan_id,kind,storage_object,sha256,bytes,state) VALUES ('cdr-x82','$(scan_id 82)','CDR_OUTPUT','cdr-output/x82','$(printf 'f%.0s' {1..64})',1,'SANITIZED_STAGED')"
q "INSERT INTO artifacts(id,scan_id,kind,storage_object,sha256,bytes,state,parent_artifact_id) VALUES ('meta-x82','$(scan_id 82)','EMBEDDED_METADATA','metadata-versions/x82','$(printf '1%.0s' {1..64})',1,'SANITIZED_STAGED','cdr-x82')"
if q "INSERT INTO final_validations(id,scan_id,attempt,artifact_id,source_sha256,output_sha256,verdict,detection,policy_decision,failure_codes,policy_version,engines,report_sha256)
      VALUES (gen_random_uuid(),'$(scan_id 82)',1,'meta-x82','$(printf 'e%.0s' {1..64})','$(printf '1%.0s' {1..64})','PASS','NO_THREATS_DETECTED','ALLOW_SANITIZED','[]','final-policy-1','{}','$(printf '1%.0s' {1..64})')" 2>/dev/null; then
  echo "PASS accepted for a metadata version without a validated parent" >&2; exit 1
fi
if q "INSERT INTO artifacts(id,scan_id,kind,storage_object,sha256,bytes,state) VALUES ('meta-orphan','$(scan_id 82)','EMBEDDED_METADATA','metadata-versions/o','$(printf '2%.0s' {1..64})',1,'SANITIZED_STAGED')" 2>/dev/null; then
  echo "metadata version without parent accepted" >&2; exit 1
fi

docker run --rm "${limits[@]}" --user 10005:10005 -v "$TEST_ARCHIVE:/archive" pdf-gateway-m4-test/archive-worker:local --root /archive verify \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); assert r["ok"] and r["documents"]==2, r'
echo "M4 embedded-metadata write, revalidation, publish, stripping, validator refusal, and guard tests passed"
