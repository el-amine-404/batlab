#!/usr/bin/env bash
# Full pipeline on Lab2 with the production compose definition: fixtures are
# uploaded through the authenticated API and pass through the real analysis,
# signature, CDR (host Dangerzone provider), final validation, embedded-metadata
# revalidation and archive publication stages. One run at a time, thermally
# guarded: Lab2 runs live services.
set -Eeuo pipefail
trap 'echo "test_pipeline_e2e.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
compose="$root/compose/pdf-gateway/app/compose.yml"
project=pdf-gateway-e2e
dc=(docker compose -p "$project" -f "$compose" -f "$root/compose/pdf-gateway/app/tests/docker-compose.pipeline-e2e.override.yml" --profile scanning --profile cdr --profile archive)
scratch="$(mktemp -d)"
export TEST_STORAGE="$scratch/storage"
. "$root/compose/pdf-gateway/app/tests/lib_m3.sh"

export PDF_GATEWAY_STORAGE_ROOT="$TEST_STORAGE"
export PDF_GATEWAY_CDR_SOCKET_DIR="$TEST_STORAGE/cdr-socket"
export PDF_GATEWAY_ARCHIVE_ROOT="$scratch/archive"
export PDF_GATEWAY_DATABASE_PASSWORD="$(openssl rand -hex 24)"
export PDF_GATEWAY_RABBITMQ_PASSWORD="$(openssl rand -hex 24)"
export PDF_GATEWAY_BOOTSTRAP_ADMIN_TOKEN="$(openssl rand -hex 32)"
export PDF_GATEWAY_API_PORT=18189
# Below the production defaults: this test bursts 11 documents through every stage at once.
export PDF_GATEWAY_CPUS_WORKER=0.25 PDF_GATEWAY_CPUS_CLAMAV=0.5 PDF_GATEWAY_CPUS_API=0.5 PDF_GATEWAY_CPUS_CORE=0.25
base="http://127.0.0.1:$PDF_GATEWAY_API_PORT"

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then
    "${dc[@]}" logs --no-color --tail=40 analysis-worker signature-worker cdr-worker validation-worker api >&2 || true
    journalctl --user -u "$unit" --no-pager -n 20 >&2 2>/dev/null || true
  fi
  stop_provider
  "${dc[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  # Archive and metadata-version files belong to the archive UID.
  docker run --rm --network none --user 10005:10005 -v "$PDF_GATEWAY_ARCHIVE_ROOT:/archive" -v "$TEST_STORAGE:/storage" --entrypoint sh \
    "$project-archive-worker" -c 'rm -rf /archive/archive /archive/previews /archive/staging /archive/manifests/* /storage/metadata-versions/*' >/dev/null 2>&1 || true
  chmod -R u+rwX "$scratch" 2>/dev/null || true
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

thermal_guard
prepare_storage
mkdir -p "$TEST_STORAGE/trust"
# The API container (UID 10001) writes quarantine objects here.
chmod 0777 "$TEST_STORAGE/quarantine"
mkdir -p "$TEST_STORAGE/text-extracts" "$TEST_STORAGE/metadata-versions" "$PDF_GATEWAY_ARCHIVE_ROOT/manifests"
chmod 0777 "$TEST_STORAGE/text-extracts" "$TEST_STORAGE/metadata-versions" "$PDF_GATEWAY_ARCHIVE_ROOT" "$PDF_GATEWAY_ARCHIVE_ROOT/manifests"
cat >"$PDF_GATEWAY_ARCHIVE_ROOT/manifests/rules.json" <<'JSON'
{"schemaVersion": "1.0.0", "revision": 1, "updatedAt": "2026-10-25T12:00:00Z", "rules": [
 {"id": "example-telecom-invoice", "issuer": "Example Telecom", "match": {"all": ["example telecom"], "any": ["invoice"]},
  "category": "Finance", "folder": "Invoices", "documentType": "invoice", "title": "Internet Invoice", "tags": ["finance", "internet", "invoice"]}]}
JSON
chmod 0644 "$PDF_GATEWAY_ARCHIVE_ROOT/manifests/rules.json"
fx="$TEST_STORAGE/fixtures"

# Images are tagged as the production compose expects. Prefer PDF_GATEWAY_PREBUILT=1
# (tools/lab2_test.sh builds off-Lab2); otherwise build here with host networking.
images=(api analysis-worker signature-worker cdr-worker archive-worker)
if [[ ${PDF_GATEWAY_PREBUILT:-} == 1 ]]; then
  for service in "${images[@]}"; do docker image inspect "$project-$service" >/dev/null; done
else
  for service in api analysis-worker signature-worker cdr-worker; do
    docker build -q --network host -t "$project-$service" "$root/compose/pdf-gateway/app/$service" >/dev/null
  done
  docker build -q --network host -f "$root/compose/pdf-gateway/app/archive-worker/Dockerfile" -t "$project-archive-worker" "$root/compose/pdf-gateway/app" >/dev/null
fi
docker tag "$project-analysis-worker" "$project-validation-worker"

# Disposable CA, a real visible signature, a post-signature incremental update,
# a byte-level tamper inside the signed range, and an encrypted file.
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -sha256 -subj '/CN=M3 E2E Root' \
  -keyout "$TEST_STORAGE/builder/root.key" -out "$TEST_STORAGE/builder/root.crt" \
  -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign' >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes -sha256 -subj '/CN=M3 E2E Signer' \
  -keyout "$TEST_STORAGE/builder/signer.key" -out "$TEST_STORAGE/builder/signer.csr" >/dev/null 2>&1
printf '%s\n' 'basicConstraints=critical,CA:FALSE' 'keyUsage=critical,digitalSignature,nonRepudiation' >"$TEST_STORAGE/builder/signer.ext"
openssl x509 -req -in "$TEST_STORAGE/builder/signer.csr" -CA "$TEST_STORAGE/builder/root.crt" -CAkey "$TEST_STORAGE/builder/root.key" \
  -CAcreateserial -days 2 -sha256 -extfile "$TEST_STORAGE/builder/signer.ext" -out "$TEST_STORAGE/builder/signer.crt" >/dev/null 2>&1
cp "$fx/unsigned.pdf" "$TEST_STORAGE/builder/unsigned.pdf"
chmod 0644 "$TEST_STORAGE/builder"/*
docker run --rm --network none -v "$TEST_STORAGE/builder:/fixtures" \
  -v "$root/compose/pdf-gateway/app/tests/make_signed_fixture.py:/t.py:ro" --entrypoint python "$project-signature-worker" /t.py 2>/dev/null
cp "$TEST_STORAGE/builder/signed.pdf" "$TEST_STORAGE/builder/signed-then-modified.pdf" "$fx/"
cp "$TEST_STORAGE/builder/root.crt" "$TEST_STORAGE/trust/root.crt"
python3 - "$fx/signed.pdf" "$fx/tampered.pdf" <<'PY'
import pathlib,sys
d=pathlib.Path(sys.argv[1]).read_bytes(); old=b"M3 unsigned CDR fixture"; new=b"M3 unsigned CDR fixturE"
assert d.count(old)==1; pathlib.Path(sys.argv[2]).write_bytes(d.replace(old,new))
PY
docker run --rm --network none -v "$fx:/f" -w /f --user "$(id -u):$(id -g)" --entrypoint qpdf "$project-analysis-worker" \
  unsigned.pdf --encrypt user owner 256 -- encrypted.pdf
chmod 0644 "$fx"/* "$TEST_STORAGE/trust/root.crt"

"${dc[@]}" up -d --no-build --wait postgres rabbitmq clamav
wait_clamav_fresh
start_provider 300
"${dc[@]}" up -d --no-build --wait
for _ in $(seq 1 60); do curl -fsS "$base/internal/actuator/health/readiness" >/dev/null 2>&1 && break; sleep 1; done

declare -A SCAN HASH
upload() {
  local name=$1 file=$2 body
  HASH[$name]="$(sha256sum "$fx/$file" | cut -d' ' -f1)"
  body="$(curl -fsS -H "Authorization: Bearer $PDF_GATEWAY_BOOTSTRAP_ADMIN_TOKEN" -H "Idempotency-Key: e2e-upload-$name-00000001" \
    -F intent=ARCHIVE -F cdrMode=RASTER -F "file=@$fx/$file;type=application/pdf" "$base/api/v1/scans")"
  SCAN[$name]="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["scanId"])' <<<"$body")"
}
upload unsigned unsigned.pdf
upload invoice invoice-en.pdf
upload signed signed.pdf
upload tampered tampered.pdf
upload modified signed-then-modified.pdf
upload visible visible-only.pdf
upload handwritten handwritten.pdf
upload malformed malformed.pdf
upload encrypted encrypted.pdf
upload active active-js.pdf
upload eicar eicar-in-stream.pdf
fixture_names=("${!SCAN[@]}")
fixture_ids="$(printf "'%s'," "${SCAN[@]}" | sed 's/,$//')"
# Optional real documents (REAL_SAMPLE_DIR, private): checked for invariants
# only and reported anonymously; their content never reaches logs or Git.
real_names=()
if [[ -n ${REAL_SAMPLE_DIR:-} ]]; then
  for file in "$REAL_SAMPLE_DIR"/*.pdf; do
    name="real$(basename "$file" .pdf)"
    cp "$file" "$fx/$name.pdf"; chmod 0644 "$fx/$name.pdf"
    upload "$name" "$name.pdf"; real_names+=("$name")
  done
fi

js() { q "SELECT $2 FROM scan_jobs WHERE id='${SCAN[$1]}'"; }
for _ in $(seq 1 300); do
  busy="$(q "SELECT count(*) FROM scan_jobs WHERE status NOT IN ('READY','REVIEW_REQUIRED','BLOCKED','FAILED')")"
  [[ $busy == 0 ]] && break
  sleep 2
done
q "SELECT s.display_name, s.status, s.detection, s.policy, s.artifact_state, s.original_retention, v.state, c.status, f.verdict, f.failure_codes
     FROM scan_jobs s LEFT JOIN signature_verifications v ON v.scan_id=s.id LEFT JOIN cdr_results c ON c.scan_id=s.id
     LEFT JOIN final_validations f ON f.scan_id=s.id ORDER BY s.display_name"
eq "$busy" 0

released() {
  local name=$1 signature=$2 retention=$3
  eq "$(js "$name" "status||','||policy||','||artifact_state||','||original_retention")" "READY,ALLOW_SANITIZED,PUBLISHED,$retention"
  eq "$(q "SELECT state FROM signature_verifications WHERE scan_id='${SCAN[$name]}'")" "$signature"
  # Two validations: the CDR output, then the embedded-metadata version that is published.
  eq "$(q "SELECT string_agg(verdict,',' ORDER BY attempt) FROM final_validations WHERE scan_id='${SCAN[$name]}'")" PASS,PASS
  eq "$(q "SELECT a.kind||','||a.state FROM archive_documents d JOIN archive_versions v USING (document_id) JOIN artifacts a ON a.id=v.artifact_id WHERE d.scan_id='${SCAN[$name]}'")" EMBEDDED_METADATA,PUBLISHED
}
released unsigned UNSIGNED STANDARD
released invoice UNSIGNED STANDARD
released signed SIGNED_VALID_TRUSTED SIGNED_RESTRICTED
released visible UNSIGNED STANDARD
released handwritten UNSIGNED STANDARD
released active UNSIGNED STANDARD
eq "$(js active detection)" NO_THREATS_DETECTED
eq "$(q "SELECT details->>'detection' FROM job_steps WHERE scan_id='${SCAN[active]}' AND step_type='ANALYSIS' AND status='SUCCEEDED'")" SUSPICIOUS

held() {
  local name=$1 status=$2 stage=$3
  eq "$(js "$name" status)" "$status"
  [[ "$(js "$name" policy)" != ALLOW_SANITIZED ]]
  eq "$(q "SELECT count(*) FROM cdr_results WHERE scan_id='${SCAN[$name]}'")" 0
  eq "$(q "SELECT count(*) FROM job_steps WHERE scan_id='${SCAN[$name]}' AND step_type='$stage' AND status='SUCCEEDED'")" 1
}
held tampered REVIEW_REQUIRED SIGNATURE
held modified REVIEW_REQUIRED SIGNATURE
held malformed REVIEW_REQUIRED ANALYSIS
held encrypted REVIEW_REQUIRED ANALYSIS
held eicar BLOCKED ANALYSIS
eq "$(q "SELECT state FROM signature_verifications WHERE scan_id='${SCAN[modified]}'")" SIGNED_MODIFIED
eq "$(js modified original_retention)" SIGNED_RESTRICTED
[[ "$(q "SELECT state FROM signature_verifications WHERE scan_id='${SCAN[tampered]}'")" =~ ^(SIGNED_INVALID|UNKNOWN)$ ]]
eq "$(js eicar "detection||','||policy")" "MALICIOUS,BLOCK"
q "SELECT details::text FROM job_steps WHERE scan_id='${SCAN[encrypted]}' AND step_type='ANALYSIS'" | grep -q PDF_ENCRYPTED
eq "$(q "SELECT count(*) FROM signature_verifications WHERE scan_id IN ('${SCAN[malformed]}','${SCAN[encrypted]}','${SCAN[eicar]}')")" 0

# Exactly the five passing documents are in a release-eligible state.
eq "$(q "SELECT count(*) FROM scan_jobs WHERE status IN ('AWAITING_ARCHIVE','ARCHIVING','READY') OR policy='ALLOW_SANITIZED' OR artifact_state IN ('FINAL_VALIDATED','PUBLISHED')) AND id IN ($fixture_ids)")" 6

# Originals are byte-identical after the whole pipeline.
for name in "${!SCAN[@]}"; do
  object="$(js "$name" quarantine_object)"
  eq "$(sha256sum "$TEST_STORAGE/quarantine/$object.bin" | cut -d' ' -f1)" "${HASH[$name]}"
  eq "$(js "$name" source_sha256)" "${HASH[$name]}"
done

output() { echo "/s/$(q "SELECT storage_object FROM artifacts WHERE scan_id='${SCAN[$1]}' AND kind='CDR_OUTPUT' AND state='FINAL_VALIDATED'")"; }
# The viewing copy of a signed document carries no signature: it is a new, unsigned PDF.
docker run --rm --network none --group-add 10010 -v "$TEST_STORAGE:/s:ro" "$project-signature-worker" "$(output signed)" \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); assert r["state"]=="UNSIGNED" and r["signatureCount"]==0, r'

# Visible appearances survive reconstruction: the signature widget, drawn
# (vector) and raster handwritten marks keep ink where the source had it.
appearance() {
  docker run --rm --network none --group-add 10010 -v "$TEST_STORAGE:/s:ro" \
    -v "$root/compose/pdf-gateway/app/tests/compare_regions.py:/t.py:ro" --entrypoint python3 "$project-analysis-worker" \
    /t.py "/s/fixtures/$2" "$(output "$1")" /s/fixtures/regions.json "$2"
}
appearance signed signed.pdf
appearance handwritten handwritten.pdf

# Every API view uses only OpenAPI contract values; internal detail is in stage.
api_view() {
  curl -fsS -H "Authorization: Bearer $PDF_GATEWAY_BOOTSTRAP_ADMIN_TOKEN" "$base/api/v1/scans/${SCAN[$1]}" \
    | python3 -I "$root/compose/pdf-gateway/app/tests/check_scan_contract.py" "$root/compose/pdf-gateway/app/contracts/openapi.yaml"
}
for name in "${!SCAN[@]}"; do api_view "$name" >/dev/null; done
eq "$(api_view signed)" '{"status": "READY", "stage": "READY", "detection": "NO_THREATS_DETECTED", "policy": "ALLOW_SANITIZED", "artifact": "PUBLISHED", "originalRetention": "SIGNED_RESTRICTED"}'
eq "$(api_view modified)" '{"status": "REVIEW_REQUIRED", "stage": "REVIEW_REQUIRED", "detection": "NO_THREATS_DETECTED", "policy": "REVIEW_REQUIRED", "artifact": "ORIGINAL_QUARANTINED", "originalRetention": "SIGNED_RESTRICTED"}'
eq "$(api_view eicar)" '{"status": "BLOCKED", "stage": "BLOCKED", "detection": "MALICIOUS", "policy": "BLOCK", "artifact": "ORIGINAL_QUARANTINED", "originalRetention": "STANDARD"}'

# The archive: named-issuer invoice filed by rule with embedded metadata;
# the signed original's viewing copy is unsigned; everything verifies standalone.
invoice_doc="doc-${SCAN[invoice]:5}"
eq "$(curl -fsS -H "Authorization: Bearer $PDF_GATEWAY_BOOTSTRAP_ADMIN_TOKEN" "$base/api/v1/archive/$invoice_doc" \
      | python3 -c 'import json,sys; v=json.load(sys.stdin); print(v["category"]+"|"+v["reviewState"]+"|"+v["archivePath"])')" "Finance|RULE|Finance/Example Telecom"
curl -fsS -H "Authorization: Bearer $PDF_GATEWAY_BOOTSTRAP_ADMIN_TOKEN" -o "$scratch/invoice.pdf" "$base/api/v1/archive/$invoice_doc/file"
docker run --rm --network none -v "$scratch/invoice.pdf:/i.pdf:ro" --entrypoint python "$project-archive-worker" -c '
import pikepdf
with pikepdf.open("/i.pdf") as pdf:
    info={str(k):str(v) for k,v in pdf.docinfo.items()}
assert info["/Title"]=="Internet Invoice - October 2026" and info["/Keywords"]=="finance, internet, invoice" and "/Author" not in info, info
print("embedded:", info)'
docker run --rm --network none --group-add 10010 -v "$scratch/invoice.pdf:/i.pdf:ro" "$project-signature-worker" /i.pdf \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); assert r["state"]=="UNSIGNED", r'
signed_meta="$(curl -fsS -H "Authorization: Bearer $PDF_GATEWAY_BOOTSTRAP_ADMIN_TOKEN" "$base/api/v1/archive/doc-${SCAN[signed]:5}/metadata")"
python3 -c 'import json,sys; s=json.loads(sys.argv[1])["signature"]; assert s["originalSigned"] and not s["viewingCopySigned"], s' "$signed_meta"
docker run --rm --network none --read-only --user 10005:10005 -v "$PDF_GATEWAY_ARCHIVE_ROOT:/archive:ro" "$project-archive-worker" --root /archive verify \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); assert r["ok"] and r["documents"]==6+int(sys.argv[1]), r; print("archive verify:", r["documents"], "documents")' \
    "$(q "SELECT count(*) FROM scan_jobs WHERE status='READY' AND id NOT IN ($fixture_ids)")"
find "$PDF_GATEWAY_ARCHIVE_ROOT/archive" -mindepth 3 -maxdepth 4 -type d | sed "s|$PDF_GATEWAY_ARCHIVE_ROOT/archive/||" | grep -v -F -f <(for n in "${real_names[@]}"; do echo "doc-${SCAN[$n]:5}"; done) | sort

for name in "${real_names[@]}"; do
  id="${SCAN[$name]}"
  status="$(js "$name" status)"
  [[ $status =~ ^(READY|REVIEW_REQUIRED|BLOCKED|FAILED)$ ]] || { echo "$name stuck in $status" >&2; exit 1; }
  if [[ $status == READY ]]; then
    eq "$(q "SELECT string_agg(verdict,',' ORDER BY attempt) FROM final_validations WHERE scan_id='$id'")" PASS,PASS
  fi
  # Anonymous outcome only: no names, titles, issuers, paths or text.
  q "SELECT '$name', s.status, s.detection, s.policy, s.original_retention,
            (SELECT details->'textExtraction'->>'status' FROM job_steps WHERE scan_id=s.id AND step_type='ANALYSIS' AND status='SUCCEEDED'),
            (SELECT details->'textExtraction'->>'pages' FROM job_steps WHERE scan_id=s.id AND step_type='ANALYSIS' AND status='SUCCEEDED'),
            (SELECT string_agg(c.status||':'||COALESCE(c.failure_code,'-')||':'||COALESCE(c.duration_ms::text,'-')||'ms:'||COALESCE(c.output_bytes::text,'-')||'B',' ' ORDER BY attempt) FROM cdr_results c WHERE c.scan_id=s.id),
            (SELECT string_agg(f.verdict||':'||f.failure_codes::text,' ' ORDER BY attempt) FROM final_validations f WHERE f.scan_id=s.id),
            (SELECT d.category||'/'||d.review_state FROM archive_documents d WHERE d.scan_id=s.id),
            s.source_bytes
       FROM scan_jobs s WHERE s.id='$id'"
  q "SELECT coalesce(string_agg(f->>'code', ',' ORDER BY f->>'code'),'none') FROM job_steps, jsonb_array_elements(details->'findings') f WHERE scan_id='$id' AND step_type='ANALYSIS' AND status='SUCCEEDED'" | sed "s/^/$name analysis findings: /"
done

# The API exposes no route that serves original or staged bytes.
for path in "/api/v1/quarantine/${SCAN[signed]}" "/api/v1/artifacts/cdr-${SCAN[signed]:5}-1/download"; do
  code="$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $PDF_GATEWAY_BOOTSTRAP_ADMIN_TOKEN" "$base$path")"
  [[ $code != 2* ]] || { echo "$path returned $code" >&2; exit 1; }
done
echo "Pipeline end-to-end: signature-aware CDR, final validation, metadata revalidation, archive publication, immutability, and fail-closed tests passed"
