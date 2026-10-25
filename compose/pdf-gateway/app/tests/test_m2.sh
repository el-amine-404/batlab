#!/usr/bin/env bash
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
compose="$root/compose/pdf-gateway/app/tests/docker-compose.m2.yml"
scratch="$(mktemp -d)"
export TEST_FIXTURES="$scratch/fixtures"
mkdir -m 0777 "$TEST_FIXTURES"

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then
    echo "M2 test failed; scanner status and logs:" >&2
    docker compose -f "$compose" ps >&2 || true
    docker compose -f "$compose" logs --no-color --tail=120 clamav >&2 || true
  fi
  docker compose -f "$compose" down --volumes --remove-orphans >/dev/null 2>&1 || true
  chmod -R u+rwX "$scratch" 2>/dev/null || true
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

python3 - "$TEST_FIXTURES/clean.pdf" "$TEST_FIXTURES/active.pdf" "$TEST_FIXTURES/eicar.com" <<'PY'
import pathlib, sys

def pdf(path, active=False):
    body=b"BT /F1 18 Tf 30 50 Td (M2 scanner fixture) Tj ET"
    action=b" /OpenAction 6 0 R" if active else b""
    objects=[b"<< /Type /Catalog /Pages 2 0 R"+action+b" >>",
             b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
             b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
             f"<< /Length {len(body)} >>\nstream\n".encode()+body+b"\nendstream",
             b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    if active: objects.append(b"<< /S /JavaScript /JS (app.alert('fixture')) >>")
    data=bytearray(b"%PDF-1.4\n"); offsets=[]
    for n,obj in enumerate(objects,1): offsets.append(len(data)); data.extend(f"{n} 0 obj\n".encode()+obj+b"\nendobj\n")
    xref=len(data); data.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for off in offsets: data.extend(f"{off:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    pathlib.Path(path).write_bytes(data)

pdf(sys.argv[1]); pdf(sys.argv[2], True)
eicar=b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
pathlib.Path(sys.argv[3]).write_bytes(eicar)
PY
printf '%%PDF-1.4\nmalformed with no objects or trailer\n' >"$TEST_FIXTURES/malformed.pdf"
printf 'safe attachment fixture\n' >"$TEST_FIXTURES/note.txt"

docker compose -f "$compose" up -d --build --wait clamav
docker compose -f "$compose" run --rm --no-deps fixture-builder clean.pdf --encrypt user owner 256 -- encrypted.pdf
docker compose -f "$compose" run --rm --no-deps fixture-builder clean.pdf --add-attachment note.txt -- attachment.pdf
docker compose -f "$compose" up -d clamav-timeout

definitions_fresh=false
for _ in $(seq 1 90); do
  # FreshClam and clamd start concurrently in the official image. Explicitly
  # request reloads so the daemon cannot keep serving the bundled older DB.
  docker compose -f "$compose" exec -T clamav clamdscan --reload >/dev/null 2>&1 || true
  if version=$(docker compose -f "$compose" exec -T clamav clamdscan --version 2>/dev/null) \
     && python3 - "$version" <<'PY'
import datetime as dt,sys
parts=sys.argv[1].strip().split("/")
if len(parts) < 3:
    raise SystemExit(1)
stamp=dt.datetime.strptime(parts[2], "%a %b %d %H:%M:%S %Y").replace(tzinfo=dt.timezone.utc)
raise SystemExit(0 if (dt.datetime.now(dt.timezone.utc)-stamp).total_seconds() <= 48*3600 else 1)
PY
  then
    definitions_fresh=true
    break
  fi
  sleep 2
done
if [[ $definitions_fresh != true ]]; then
  echo "ClamAV definitions did not become fresh within 3 minutes" >&2
  exit 1
fi

run_scan() {
  local input=$1 output=$2
  local status
  if docker compose -f "$compose" run --rm --no-deps analyzer "/fixtures/$input" >"$output"; then
    status=0
  else
    status=$?
  fi
  if [[ $status -ne 0 ]]; then
    echo "scan of $input failed with $status: $(<"$output")" >&2
  fi
  return "$status"
}

run_scan clean.pdf "$scratch/clean.json"
python3 - "$scratch/clean.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1])); assert r["detection"]=="CLEAN",r
assert r["policyDecision"]=="CDR_ELIGIBLE",r
assert r["engines"][0]["status"]=="CLEAN",r
assert all(e.get("version") not in (None,"UNKNOWN") for e in r["engines"]),r
PY

run_scan active.pdf "$scratch/active.json"
python3 - "$scratch/active.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1])); assert r["detection"]=="SUSPICIOUS",r
assert r["policyDecision"]=="CDR_ELIGIBLE",r
assert any(f["code"]=="YARA_MATCH" for f in r["findings"]),r
assert any(f["code"]=="PDF_KEYWORD_OPENACTION" for f in r["findings"]),r
PY

set +e
run_scan encrypted.pdf "$scratch/encrypted.json"
encrypted_status=$?
run_scan attachment.pdf "$scratch/attachment.json"
attachment_status=$?
set -e
[[ $encrypted_status -eq 2 && $attachment_status -eq 0 ]]
python3 - "$scratch/encrypted.json" "$scratch/attachment.json" <<'PY'
import json,sys
encrypted=json.load(open(sys.argv[1])); assert encrypted["detection"]=="UNKNOWN",encrypted
assert encrypted["policyDecision"]=="REVIEW_REQUIRED",encrypted
assert any(f["code"] in {"PDF_ENCRYPTED","PDF_STRUCTURE_INVALID"} for f in encrypted["findings"]),encrypted
attachment=json.load(open(sys.argv[2])); assert attachment["detection"]=="SUSPICIOUS",attachment
assert attachment["policyDecision"]=="REVIEW_REQUIRED",attachment
assert any(f["code"]=="PDF_KEYWORD_EMBEDDEDFILE" for f in attachment["findings"]),attachment
PY

set +e
run_scan eicar.com "$scratch/eicar.json"
eicar_status=$?
set -e
[[ $eicar_status -eq 0 ]]
python3 - "$scratch/eicar.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1])); assert r["detection"]=="MALICIOUS",r
assert r["policyDecision"]=="BLOCK",r
assert any(f["code"]=="MALWARE_DETECTED" for f in r["findings"]),r
PY

set +e
run_scan malformed.pdf "$scratch/malformed.json"
malformed_status=$?
set -e
[[ $malformed_status -eq 2 ]]
python3 - "$scratch/malformed.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1])); assert r["detection"]=="UNKNOWN",r
assert r["policyDecision"]=="REVIEW_REQUIRED",r
assert any(f["code"]=="PDF_STRUCTURE_INVALID" for f in r["findings"]),r
PY

set +e
docker compose -f "$compose" run --rm --no-deps analyzer --max-definition-age-hours 0 /fixtures/clean.pdf >"$scratch/stale.json"
stale_status=$?
docker compose -f "$compose" run --rm --no-deps analyzer --clamd-host unavailable /fixtures/clean.pdf >"$scratch/unavailable.json"
unavailable_status=$?
docker compose -f "$compose" run --rm --no-deps analyzer --clamd-host clamav-timeout --clamd-timeout 1 /fixtures/clean.pdf >"$scratch/timeout.json"
timeout_status=$?
set -e
[[ $stale_status -eq 2 && $unavailable_status -eq 2 && $timeout_status -eq 2 ]]
python3 - "$scratch/stale.json" "$scratch/unavailable.json" "$scratch/timeout.json" <<'PY'
import json,sys
for name in sys.argv[1:]:
    r=json.load(open(name)); assert r["detection"]=="UNKNOWN",r
    assert r["policyDecision"]=="REVIEW_REQUIRED",r
PY

echo "M2 live ClamAV, structural analysis, YARA, typed policy, and fail-closed tests passed"
