#!/usr/bin/env bash
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
compose="$root/compose/pdf-gateway/app/tests/docker-compose.m2-pipeline.yml"
scratch="$(mktemp -d)"
export TEST_STORAGE="$scratch/storage"
mkdir -p "$TEST_STORAGE/quarantine" "$TEST_STORAGE/reports"
chmod 0777 "$TEST_STORAGE/reports"

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then docker compose -f "$compose" logs --no-color --tail=150 >&2 || true; fi
  docker compose -f "$compose" down --volumes --remove-orphans >/dev/null 2>&1 || true
  chmod -R u+rwX "$scratch" 2>/dev/null || true
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

scan_id="scan-01J00000000000000000000000"
object_id="quar-01J00000000000000000000000"
python3 - "$TEST_STORAGE/quarantine/$object_id.bin" <<'PY'
import pathlib,sys
body=b"BT /F1 18 Tf 30 50 Td (durable worker test) Tj ET"
objects=[b"<< /Type /Catalog /Pages 2 0 R >>",b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",f"<< /Length {len(body)} >>\nstream\n".encode()+body+b"\nendstream",b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
d=bytearray(b"%PDF-1.4\n"); offsets=[]
for n,o in enumerate(objects,1): offsets.append(len(d)); d.extend(f"{n} 0 obj\n".encode()+o+b"\nendobj\n")
x=len(d); d.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
for off in offsets:d.extend(f"{off:010d} 00000 n \n".encode())
d.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()); pathlib.Path(sys.argv[1]).write_bytes(d)
PY
hash="$(sha256sum "$TEST_STORAGE/quarantine/$object_id.bin" | cut -d' ' -f1)"
bytes="$(stat -c %s "$TEST_STORAGE/quarantine/$object_id.bin")"

docker compose -f "$compose" up -d --build --wait
psql_cmd=(docker compose -f "$compose" exec -T postgres psql -U pdf_gateway -d pdf_gateway -v ON_ERROR_STOP=1)

definitions_fresh=false
for _ in $(seq 1 90); do
  docker compose -f "$compose" exec -T clamav clamdscan --reload >/dev/null 2>&1 || true
  if version=$(docker compose -f "$compose" exec -T clamav clamdscan --version 2>/dev/null) \
     && python3 - "$version" <<'PY'
import datetime as dt,sys
parts=sys.argv[1].strip().split("/")
if len(parts) < 3: raise SystemExit(1)
stamp=dt.datetime.strptime(parts[2], "%a %b %d %H:%M:%S %Y").replace(tzinfo=dt.timezone.utc)
raise SystemExit(0 if (dt.datetime.now(dt.timezone.utc)-stamp).total_seconds() <= 48*3600 else 1)
PY
  then definitions_fresh=true; break; fi
  sleep 2
done
[[ $definitions_fresh == true ]]

"${psql_cmd[@]}" <<SQL
INSERT INTO users(id,username,role) VALUES ('00000000-0000-0000-0000-000000000001','pipeline','ADMIN');
INSERT INTO scan_jobs(id,owner_id,status,intent,cdr_mode,display_name,source_bytes,source_sha256,quarantine_object)
VALUES ('$scan_id','00000000-0000-0000-0000-000000000001','QUEUED','SCAN_ONLY','RASTER','fixture.pdf',$bytes,'$hash','$object_id');
SQL

publish() {
  docker compose -f "$compose" run --rm --no-deps --entrypoint python3 worker -c \
    "import json,os,pika;c=pika.BlockingConnection(pika.ConnectionParameters('rabbitmq',credentials=pika.PlainCredentials('pdf_gateway','test-rabbit-password')));c.channel().basic_publish('', 'pdf.gateway.scan.v1', json.dumps({'scanId':'$scan_id','artifactId':'$object_id'}), pika.BasicProperties(delivery_mode=2));c.close()"
}
publish

for _ in $(seq 1 90); do
  state="$("${psql_cmd[@]}" -Atc "SELECT status FROM scan_jobs WHERE id='$scan_id'")"
  [[ $state == AWAITING_POLICY ]] && break
  sleep 2
done
[[ ${state:-} == AWAITING_POLICY ]]
[[ -s "$TEST_STORAGE/reports/$scan_id.json" ]]
docker compose -f "$compose" exec -T worker python3 -c \
  "import json;r=json.load(open('/var/lib/pdf-gateway/reports/$scan_id.json'));assert r['detection']=='CLEAN',r;assert r['policyDecision']=='CDR_ELIGIBLE',r"
[[ "$("${psql_cmd[@]}" -Atc "SELECT count(*) FROM artifacts WHERE scan_id='$scan_id' AND kind='SCAN_REPORT'")" == 1 ]]

# Broker replay is acknowledged idempotently without a second analysis attempt.
publish
sleep 3
[[ "$("${psql_cmd[@]}" -Atc "SELECT count(*) FROM job_steps WHERE scan_id='$scan_id' AND step_type='ANALYSIS'")" == 1 ]]
echo "M2 durable worker, atomic report, database transition, and replay tests passed"
