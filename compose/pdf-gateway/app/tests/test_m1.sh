#!/usr/bin/env bash
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
compose="$root/compose/pdf-gateway/app/tests/docker-compose.m1.yml"
scratch="$(mktemp -d)"
export TEST_STORAGE="$scratch/storage"
export TEST_DB_PASSWORD="$(openssl rand -hex 24)"
export TEST_RABBIT_PASSWORD="$(openssl rand -hex 24)"
export TEST_ADMIN_TOKEN="$(openssl rand -hex 32)"
mkdir -m 0700 "$TEST_STORAGE"
mkdir -m 0777 "$TEST_STORAGE/quarantine" "$TEST_STORAGE/multipart"
chmod 0777 "$TEST_STORAGE" "$TEST_STORAGE/quarantine" "$TEST_STORAGE/multipart"
# World-writable directories exist only inside this disposable rootless fixture.
# Production directories are provisioned with a dedicated owner and mode 0700.

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then
    echo "M1 test failed; container status and logs:" >&2
    docker compose -f "$compose" ps >&2 || true
    docker compose -f "$compose" logs --no-color --tail=200 api >&2 || true
  fi
  docker compose -f "$compose" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

docker compose -f "$compose" up -d --build --wait
base="http://127.0.0.1:18089"

python3 - "$scratch/sample.pdf" <<'PY'
import pathlib, sys
body=b"BT /F1 18 Tf 30 50 Td (M1 real quarantine test) Tj ET"
objects=[b"<< /Type /Catalog /Pages 2 0 R >>",b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",f"<< /Length {len(body)} >>\nstream\n".encode()+body+b"\nendstream",b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
data=bytearray(b"%PDF-1.4\n"); offsets=[]
for n,obj in enumerate(objects,1): offsets.append(len(data)); data.extend(f"{n} 0 obj\n".encode()+obj+b"\nendobj\n")
xref=len(data); data.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
for off in offsets: data.extend(f"{off:010d} 00000 n \n".encode())
data.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()); pathlib.Path(sys.argv[1]).write_bytes(data)
PY

status=$(curl -sS -o "$scratch/unauthorized.json" -w '%{http_code}' "$base/api/v1/scans/scan-00000000000000000000000000")
[[ $status == 401 ]]

status=$(curl -sS -o "$scratch/notpdf.json" -w '%{http_code}' -H "Authorization: Bearer $TEST_ADMIN_TOKEN" -H 'Idempotency-Key: non-pdf-request-0001' -F 'intent=ARCHIVE' -F 'file=@/etc/hostname;type=application/pdf' "$base/api/v1/scans")
[[ $status == 400 ]]

status=$(curl -sS -o "$scratch/accepted.json" -w '%{http_code}' -H "Authorization: Bearer $TEST_ADMIN_TOKEN" -H 'Idempotency-Key: sample-upload-request-0001' -F 'intent=ARCHIVE' -F 'cdrMode=RASTER' -F "file=@$scratch/sample.pdf;filename=../../hostile.pdf;type=application/pdf" "$base/api/v1/scans")
if [[ $status != 202 ]]; then echo "expected upload 202, got $status: $(<"$scratch/accepted.json")" >&2; exit 1; fi
scan_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["scanId"])' "$scratch/accepted.json")
[[ $scan_id =~ ^scan-[0-9A-HJKMNP-TV-Z]{26}$ ]]

curl -fsS -H "Authorization: Bearer $TEST_ADMIN_TOKEN" -H 'Idempotency-Key: sample-upload-request-0001' -F 'intent=ARCHIVE' -F 'cdrMode=RASTER' -F "file=@$scratch/sample.pdf;type=application/pdf" "$base/api/v1/scans" >"$scratch/replayed.json"
[[ $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["scanId"])' "$scratch/replayed.json") == "$scan_id" ]]
[[ $(find "$TEST_STORAGE/quarantine" -type f -name '*.bin' | wc -l) -eq 1 ]]

printf '%%PDF-1.4\nchanged\n' >"$scratch/different.pdf"
status=$(curl -sS -o "$scratch/conflict.json" -w '%{http_code}' -H "Authorization: Bearer $TEST_ADMIN_TOKEN" -H 'Idempotency-Key: sample-upload-request-0001' -F 'intent=ARCHIVE' -F "file=@$scratch/different.pdf;type=application/pdf" "$base/api/v1/scans")
[[ $status == 409 ]]
[[ $(find "$TEST_STORAGE/quarantine" -type f -name '*.bin' | wc -l) -eq 1 ]]

other_token=$(openssl rand -hex 32)
other_hash=$(printf %s "$other_token" | sha256sum | cut -d' ' -f1)
docker compose -f "$compose" exec -T postgres psql -v ON_ERROR_STOP=1 -U pdf_gateway -d pdf_gateway -v hash="$other_hash" <<'SQL' >/dev/null
INSERT INTO users(id,username,role) VALUES ('00000000-0000-0000-0000-000000000002','other','USER');
INSERT INTO api_tokens(id,user_id,token_hash,label) VALUES ('00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000002',:'hash','test');
SQL
status=$(curl -sS -o "$scratch/cross-user.json" -w '%{http_code}' -H "Authorization: Bearer $other_token" "$base/api/v1/scans/$scan_id")
[[ $status == 404 ]]

readonly_token=$(openssl rand -hex 32)
readonly_hash=$(printf %s "$readonly_token" | sha256sum | cut -d' ' -f1)
docker compose -f "$compose" exec -T postgres psql -v ON_ERROR_STOP=1 -U pdf_gateway -d pdf_gateway -v hash="$readonly_hash" <<'SQL' >/dev/null
INSERT INTO users(id,username,role) VALUES ('00000000-0000-0000-0000-000000000004','reader','READ_ONLY');
INSERT INTO api_tokens(id,user_id,token_hash,label) VALUES ('00000000-0000-0000-0000-000000000005','00000000-0000-0000-0000-000000000004',:'hash','test');
SQL
status=$(curl -sS -o "$scratch/readonly.json" -w '%{http_code}' -H "Authorization: Bearer $readonly_token" -H 'Idempotency-Key: readonly-upload-request-0001' -F 'intent=ARCHIVE' -F "file=@$scratch/sample.pdf;type=application/pdf" "$base/api/v1/scans")
[[ $status == 403 ]]

status=$(curl -sS -o "$scratch/raw.json" -w '%{http_code}' -H "Authorization: Bearer $TEST_ADMIN_TOKEN" "$base/api/v1/quarantine/$scan_id")
# No route serves quarantined bytes: 404, or 405 since M5 added an admin DELETE on this path.
[[ $status == 404 || $status == 405 ]]
! grep -q '%PDF' "$scratch/raw.json"

docker compose -f "$compose" restart api >/dev/null
for _ in $(seq 1 40); do curl -fsS "$base/internal/actuator/health/readiness" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS -H "Authorization: Bearer $TEST_ADMIN_TOKEN" "$base/api/v1/scans/$scan_id" | grep -q '"status":"QUEUED"'

docker compose -f "$compose" exec -T postgres psql -At -U pdf_gateway -d pdf_gateway -c "SELECT count(*) FROM audit_events WHERE resource_id='$scan_id' AND action='SCAN_CREATED'" | grep -qx 1
docker compose -f "$compose" exec -T postgres psql -At -U pdf_gateway -d pdf_gateway -c "SELECT count(*) FROM outbox_events WHERE aggregate_id='$scan_id' AND published_at IS NOT NULL" | grep -qx 1

echo "M1 authentication/RBAC, quarantine, idempotency, ownership, restart, audit, and outbox tests passed"
