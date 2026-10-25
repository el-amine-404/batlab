#!/usr/bin/env bash
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
compose="$root/pdf-gateway/tests/docker-compose.m3-pipeline.yml"
scratch="$(mktemp -d)"
export TEST_STORAGE="$scratch/storage"
mkdir -p "$TEST_STORAGE"/{builder,quarantine,signature-reports,trust}
chmod 0777 "$TEST_STORAGE/builder" "$TEST_STORAGE/signature-reports"

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then docker compose -f "$compose" logs --no-color --tail=120 >&2 || true; fi
  docker compose -f "$compose" down --volumes --remove-orphans >/dev/null 2>&1 || true
  chmod -R u+rwX "$scratch" 2>/dev/null || true
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

python3 - "$TEST_STORAGE/builder/unsigned.pdf" <<'PY'
import pathlib,sys
body=b"BT /F1 18 Tf 30 110 Td (M3 durable signature fixture) Tj ET"
objects=[b"<< /Type /Catalog /Pages 2 0 R >>",b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 150] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",f"<< /Length {len(body)} >>\nstream\n".encode()+body+b"\nendstream",b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
d=bytearray(b"%PDF-1.4\n"); offsets=[]
for n,o in enumerate(objects,1): offsets.append(len(d)); d.extend(f"{n} 0 obj\n".encode()+o+b"\nendobj\n")
x=len(d); d.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
for off in offsets:d.extend(f"{off:010d} 00000 n \n".encode())
d.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()); pathlib.Path(sys.argv[1]).write_bytes(d)
PY
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -sha256 -subj '/CN=M3 Pipeline Root' \
  -keyout "$TEST_STORAGE/builder/root.key" -out "$TEST_STORAGE/builder/root.crt" \
  -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign' >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes -sha256 -subj '/CN=M3 Pipeline Signer' \
  -keyout "$TEST_STORAGE/builder/signer.key" -out "$TEST_STORAGE/builder/signer.csr" >/dev/null 2>&1
printf '%s\n' 'basicConstraints=critical,CA:FALSE' 'keyUsage=critical,digitalSignature,nonRepudiation' >"$TEST_STORAGE/builder/signer.ext"
openssl x509 -req -in "$TEST_STORAGE/builder/signer.csr" -CA "$TEST_STORAGE/builder/root.crt" -CAkey "$TEST_STORAGE/builder/root.key" \
  -CAcreateserial -days 2 -sha256 -extfile "$TEST_STORAGE/builder/signer.ext" -out "$TEST_STORAGE/builder/signer.crt" >/dev/null 2>&1
chmod 0644 "$TEST_STORAGE/builder"/*
docker compose -f "$compose" run --rm --no-deps signer
cp "$TEST_STORAGE/builder/root.crt" "$TEST_STORAGE/trust/root.crt"

signed_object="quar-01J00000000000000000000010"
tampered_object="quar-01J00000000000000000000011"
cp "$TEST_STORAGE/builder/signed.pdf" "$TEST_STORAGE/quarantine/$signed_object.bin"
python3 - "$TEST_STORAGE/builder/signed.pdf" "$TEST_STORAGE/quarantine/$tampered_object.bin" <<'PY'
import pathlib,sys
d=pathlib.Path(sys.argv[1]).read_bytes(); old=b"M3 durable signature fixture"; new=b"M3 durab1e signature fixture"
assert old in d and len(old)==len(new); pathlib.Path(sys.argv[2]).write_bytes(d.replace(old,new,1))
PY
chmod 0644 "$TEST_STORAGE/quarantine"/* "$TEST_STORAGE/trust/root.crt"

docker compose -f "$compose" up -d --build --wait postgres rabbitmq worker
psql=(docker compose -f "$compose" exec -T postgres psql -U pdf_gateway -d pdf_gateway -v ON_ERROR_STOP=1)
signed_scan="scan-01J00000000000000000000010"
tampered_scan="scan-01J00000000000000000000011"
signed_hash="$(sha256sum "$TEST_STORAGE/quarantine/$signed_object.bin" | cut -d' ' -f1)"
tampered_hash="$(sha256sum "$TEST_STORAGE/quarantine/$tampered_object.bin" | cut -d' ' -f1)"
signed_bytes="$(stat -c %s "$TEST_STORAGE/quarantine/$signed_object.bin")"
tampered_bytes="$(stat -c %s "$TEST_STORAGE/quarantine/$tampered_object.bin")"
"${psql[@]}" <<SQL
INSERT INTO users(id,username,role) VALUES ('00000000-0000-0000-0000-000000000001','pipeline','ADMIN');
INSERT INTO scan_jobs(id,owner_id,status,intent,cdr_mode,display_name,source_bytes,source_sha256,quarantine_object,detection,policy)
VALUES ('$signed_scan','00000000-0000-0000-0000-000000000001','AWAITING_POLICY','ARCHIVE','RASTER','signed.pdf',$signed_bytes,'$signed_hash','$signed_object','CLEAN','CDR_ELIGIBLE'),
       ('$tampered_scan','00000000-0000-0000-0000-000000000001','AWAITING_POLICY','ARCHIVE','RASTER','tampered.pdf',$tampered_bytes,'$tampered_hash','$tampered_object','CLEAN','CDR_ELIGIBLE');
SQL

publish() {
  local scan=$1
  docker compose -f "$compose" run --rm --no-deps --entrypoint python worker -c \
    "import json,pika;c=pika.BlockingConnection(pika.ConnectionParameters('rabbitmq',credentials=pika.PlainCredentials('pdf_gateway','test-rabbit-password')));c.channel().basic_publish('', 'pdf.gateway.signature.v1', json.dumps({'scanId':'$scan'}), pika.BasicProperties(delivery_mode=2));c.close()"
}
publish "$signed_scan"; publish "$tampered_scan"
for _ in $(seq 1 60); do
  states="$("${psql[@]}" -Atc "SELECT string_agg(status,',' ORDER BY id) FROM scan_jobs")"
  [[ $states == AWAITING_CDR,REVIEW_REQUIRED ]] && break
  sleep 1
done
[[ ${states:-} == AWAITING_CDR,REVIEW_REQUIRED ]]

[[ "$("${psql[@]}" -Atc "SELECT state FROM signature_verifications WHERE scan_id='$signed_scan'")" == SIGNED_VALID_TRUSTED ]]
[[ "$("${psql[@]}" -Atc "SELECT artifact_state FROM scan_jobs WHERE id='$signed_scan'")" == SIGNED_ORIGINAL_RESTRICTED ]]
[[ "$("${psql[@]}" -Atc "SELECT policy_decision FROM signature_verifications WHERE scan_id='$tampered_scan'")" == REVIEW_REQUIRED ]]
[[ "$("${psql[@]}" -Atc "SELECT count(*) FROM outbox_events WHERE aggregate_id='$signed_scan' AND event_type='CDR_QUEUED'")" == 1 ]]
[[ "$("${psql[@]}" -Atc "SELECT count(*) FROM outbox_events WHERE aggregate_id='$tampered_scan' AND event_type='CDR_QUEUED'")" == 0 ]]
[[ -s "$TEST_STORAGE/signature-reports/$signed_scan.json" ]]

publish "$signed_scan"; sleep 2
[[ "$("${psql[@]}" -Atc "SELECT count(*) FROM signature_verifications WHERE scan_id='$signed_scan'")" == 1 ]]
[[ "$("${psql[@]}" -Atc "SELECT count(*) FROM job_steps WHERE scan_id='$signed_scan' AND step_type='SIGNATURE'")" == 1 ]]
[[ "$(sha256sum "$TEST_STORAGE/quarantine/$signed_object.bin" | cut -d' ' -f1)" == "$signed_hash" ]]
echo "M3 durable signature state, restricted original, fail-closed tamper, outbox, replay, and immutability tests passed"
