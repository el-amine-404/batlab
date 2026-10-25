#!/usr/bin/env bash
# M3 slice 1: real Dangerzone conversions through the host provider and the
# durable cdr-worker. Must run on the CDR host (Lab2) as the rootless account.
set -Eeuo pipefail
trap 'echo "test_m3_cdr.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
compose="$root/pdf-gateway/tests/docker-compose.m3-cdr.yml"
unit_file="$root/hosts/lab2/pdf-cdr-provider/pdf-cdr-provider.service"
provider="$root/hosts/lab2/pdf-cdr-provider/provider.py"
export CONTAINERS_CONF=/etc/batlab/pdf-cdr/containers.conf
scratch="$(mktemp -d)"
export TEST_STORAGE="$scratch/storage"
mkdir -p "$TEST_STORAGE"/{builder,fixtures,quarantine,cdr-output,cdr-socket} "$scratch/provider"
chmod 0755 "$TEST_STORAGE" "$TEST_STORAGE/quarantine" "$TEST_STORAGE/cdr-socket"
chmod 0777 "$TEST_STORAGE/builder" "$TEST_STORAGE/cdr-output"
unit="pdf-cdr-provider-test-$$"
# Shared thermal guard (Lab2 must stay healthy while this runs).
eval "$(sed -n '/^package_temp()/,$p' "$root/pdf-gateway/tests/lib_m3.sh")"
thermal_guard

stop_provider() { systemctl --user stop "$unit" >/dev/null 2>&1 || true; }
cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then
    docker compose -f "$compose" logs --no-color --tail=80 >&2 || true
    journalctl --user -u "$unit" --no-pager -n 40 >&2 2>/dev/null || true
  fi
  stop_provider
  docker compose -f "$compose" down --volumes --remove-orphans >/dev/null 2>&1 || true
  chmod -R u+rwX "$scratch" 2>/dev/null || true
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

# Resource properties come from the tracked unit so the test exercises them.
mapfile -t properties < <(grep -E '^(Nice|IOSchedulingClass|CPUQuota|MemoryMax|MemorySwapMax|TasksMax)=' "$unit_file" | sed 's/^/--property=/')
start_provider() {
  local timeout=$1
  stop_provider
  systemd-run --user --quiet --collect --unit "$unit" "${properties[@]}" \
    --setenv=CONTAINERS_CONF="$CONTAINERS_CONF" -- \
    /usr/bin/python3 -I "$provider" --socket "$TEST_STORAGE/cdr-socket/cdr.sock" \
    --allowed-uid 10004 --scratch "$scratch/provider" --timeout "$timeout"
  for _ in $(seq 1 50); do [[ -S $TEST_STORAGE/cdr-socket/cdr.sock ]] && return; sleep 0.2; done
  echo "provider did not start" >&2; return 1
}

python3 -I "$root/pdf-gateway/tests/make_fixtures.py" "$TEST_STORAGE/fixtures"
cp "$TEST_STORAGE/fixtures/unsigned.pdf" "$TEST_STORAGE/builder/unsigned.pdf"
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -sha256 -subj '/CN=M3 CDR Root' \
  -keyout "$TEST_STORAGE/builder/root.key" -out "$TEST_STORAGE/builder/root.crt" \
  -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign' >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes -sha256 -subj '/CN=M3 CDR Signer' \
  -keyout "$TEST_STORAGE/builder/signer.key" -out "$TEST_STORAGE/builder/signer.csr" >/dev/null 2>&1
printf '%s\n' 'basicConstraints=critical,CA:FALSE' 'keyUsage=critical,digitalSignature,nonRepudiation' >"$TEST_STORAGE/builder/signer.ext"
openssl x509 -req -in "$TEST_STORAGE/builder/signer.csr" -CA "$TEST_STORAGE/builder/root.crt" -CAkey "$TEST_STORAGE/builder/root.key" \
  -CAcreateserial -days 2 -sha256 -extfile "$TEST_STORAGE/builder/signer.ext" -out "$TEST_STORAGE/builder/signer.crt" >/dev/null 2>&1
chmod 0644 "$TEST_STORAGE/builder"/*
# Lab2's default bridge has no DNS, so image builds use host networking.
docker build -q --network host -t pdf-gateway-m3-test/cdr-worker:local "$root/pdf-gateway/cdr-worker" >/dev/null
docker build -q --network host -t pdf-gateway-m3-test/signature-worker:local "$root/pdf-gateway/signature-worker" >/dev/null
docker compose -f "$compose" run --rm --no-deps signer
cp "$TEST_STORAGE/builder/signed.pdf" "$TEST_STORAGE/fixtures/signed.pdf"

docker compose -f "$compose" up -d --wait postgres rabbitmq cdr-worker
psql=(docker compose -f "$compose" exec -T postgres psql -U pdf_gateway -d pdf_gateway -v ON_ERROR_STOP=1 -q)
q() { "${psql[@]}" -Atc "$1"; }
q "INSERT INTO users(id,username,role) VALUES ('00000000-0000-0000-0000-000000000001','cdr','ADMIN')"

declare -A HASH
# seed SCAN FIXTURE [nosig] — stage-isolated seeding of committed M2/M3 evidence.
# The end-to-end chain with real analysis/signature engines is test_m3_e2e.sh.
seed() {
  local n=$1 fixture=$2 mode=${3:-}
  local scan="scan-01J000000000000000000000$n" object="quar-01J000000000000000000000$n"
  cp "$TEST_STORAGE/fixtures/$fixture" "$TEST_STORAGE/quarantine/$object.bin"
  chmod 0644 "$TEST_STORAGE/quarantine/$object.bin"
  local hash bytes; hash="$(sha256sum "$TEST_STORAGE/quarantine/$object.bin" | cut -d' ' -f1)"; bytes="$(stat -c %s "$TEST_STORAGE/quarantine/$object.bin")"
  HASH[$n]=$hash
  "${psql[@]}" <<SQL
INSERT INTO scan_jobs(id,owner_id,status,intent,cdr_mode,display_name,source_bytes,source_sha256,quarantine_object,detection,policy)
VALUES ('$scan','00000000-0000-0000-0000-000000000001','AWAITING_CDR','ARCHIVE','RASTER','$fixture',$bytes,'$hash','$object','CLEAN','CDR_ELIGIBLE');
INSERT INTO job_steps(id,scan_id,step_type,status,attempt,started_at,finished_at,details)
VALUES (gen_random_uuid(),'$scan','ANALYSIS','SUCCEEDED',1,now(),now(),'{"policyDecision":"CDR_ELIGIBLE","detection":"CLEAN","sourceSha256":"$hash"}');
SQL
  if [[ $mode != nosig ]]; then
    q "INSERT INTO signature_verifications(id,scan_id,attempt,source_sha256,state,policy_decision,preserve_original,signature_count,report) VALUES (gen_random_uuid(),'$scan',1,'$hash','UNSIGNED','CDR_REQUIRED',false,0,'{}')"
  fi
}
publish() {
  docker compose -f "$compose" run --rm --no-deps -T --entrypoint python3 cdr-worker -c \
    "import json,pika;c=pika.BlockingConnection(pika.ConnectionParameters('rabbitmq',credentials=pika.PlainCredentials('pdf_gateway','test-rabbit-password')));ch=c.channel();ch.queue_declare('pdf.gateway.cdr.v1',durable=True);ch.basic_publish('','pdf.gateway.cdr.v1',json.dumps({'scanId':'scan-01J000000000000000000000$1'}),pika.BasicProperties(delivery_mode=2));c.close()" >/dev/null
}
wait_status() {
  local n=$1 expected=$2 status=""
  for _ in $(seq 1 ${3:-120}); do
    status="$(q "SELECT status FROM scan_jobs WHERE id='scan-01J000000000000000000000$n'")"
    [[ $status == "$expected" ]] && return 0
    sleep 1
  done
  echo "scan $n: expected $expected, saw $status" >&2; return 1
}
eq() { [[ "$1" == "$2" ]] || { echo "expected [$2], got [$1]" >&2; return 1; }; }
col() { q "SELECT $2 FROM $3 WHERE scan_id='scan-01J000000000000000000000$1' ${4:-}"; }

expected_digest="$(podman image list --format '{{.Digest}}' ghcr.io/freedomofpress/dangerzone/v1 | sort -u)"
start_provider 300

# Peer credentials: this host account is not the cdr-worker UID.
python3 -I - "$TEST_STORAGE/cdr-socket/cdr.sock" <<'PY'
import json,socket,sys
s=socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); s.settimeout(10)
reply=json.loads(s.makefile("rb").readline())
assert reply["status"]=="FAILED" and reply["code"]=="PEER_NOT_ALLOWED", reply
PY

seed 20 unsigned.pdf
seed 21 active-js.pdf
seed 22 signed.pdf
q "UPDATE scan_jobs SET original_retention='SIGNED_RESTRICTED' WHERE id='scan-01J00000000000000000000022'"
seed 23 malformed.pdf
seed 24 unsigned.pdf nosig
for n in 20 21 22 23 24; do publish $n; done
for n in 20 21 22; do wait_status $n VALIDATING_OUTPUT; done
wait_status 23 REVIEW_REQUIRED
wait_status 24 REVIEW_REQUIRED

for n in 20 21 22; do
  eq "$(col $n "concat_ws(',',status,image_digest,coalesce(evidence->'isolation'->>'innerNetwork','null'),coalesce(evidence->'isolation'->>'hostMounts','null'))" cdr_results)" "SUCCEEDED,$expected_digest,none,none"
  eq "$(q "SELECT artifact_state FROM scan_jobs WHERE id='scan-01J000000000000000000000$n'")" SANITIZED_STAGED
  object="$(col $n storage_object artifacts "AND kind='CDR_OUTPUT'")"
  # The staged output is 0640 (worker UID, validation group 10010); the host
  # account cannot read it, so inspect it from inside the worker.
  read -r out_hash out_mode out_head < <(docker compose -f "$compose" exec -T cdr-worker python3 -c \
    "import hashlib,os,sys;p='/var/lib/pdf-gateway/'+sys.argv[1];d=open(p,'rb').read();print(hashlib.sha256(d).hexdigest(),oct(os.stat(p).st_mode&0o777)[2:],d[:5].decode())" "$object")
  eq "$out_hash" "$(col $n sha256 artifacts "AND kind='CDR_OUTPUT'")"
  [[ $out_hash != "${HASH[$n]}" ]]
  eq "$out_mode" 640
  eq "$out_head" "%PDF-"
  eq "$(col $n "count(*)" job_steps "AND step_type='CDR' AND status='SUCCEEDED'")" 1
  eq "$(q "SELECT count(*) FROM outbox_events WHERE aggregate_id='scan-01J000000000000000000000$n' AND event_type='VALIDATION_QUEUED'")" 1
  eq "$(sha256sum "$TEST_STORAGE/quarantine/quar-01J000000000000000000000$n.bin" | cut -d' ' -f1)" "${HASH[$n]}"
done
eq "$(q "SELECT original_retention FROM scan_jobs WHERE id='scan-01J00000000000000000000022'")" SIGNED_RESTRICTED
# Lowering signed-original retention is rejected by the database itself.
if q "UPDATE scan_jobs SET original_retention='STANDARD' WHERE id='scan-01J00000000000000000000022'" 2>/dev/null; then
  echo "signed retention was lowered" >&2; exit 1
fi
eq "$(col 23 "status||','||failure_code||','||retryable" cdr_results)" "FAILED,CONVERSION_FAILED,false"
eq "$(col 23 "count(*)" artifacts "AND kind='CDR_OUTPUT'")" 0
eq "$(q "SELECT count(*) FROM outbox_events WHERE aggregate_id='scan-01J00000000000000000000023'")" 0
eq "$(col 24 "count(*)" job_steps "AND step_type='CDR'")" 0
eq "$(q "SELECT details->>'code' FROM audit_events WHERE resource_id='scan-01J00000000000000000000024' AND action='CDR_PRECONDITION_FAILED'")" SIGNATURE_EVIDENCE_MISSING

# Replay of a completed job is a no-op.
publish 20; sleep 3
eq "$(col 20 "count(*)" cdr_results)" 1
eq "$(col 20 "count(*)" artifacts "AND kind='CDR_OUTPUT'")" 1

# Timeout: the provider kills exactly its sandbox; nothing is staged. While the
# sandbox runs, gVisor processes must sit inside the resource-limited unit.
start_provider 5
seed 25 many-pages.pdf
publish 25
cgroup="/sys/fs/cgroup$(systemctl --user show -p ControlGroup --value "$unit")"
contained=no
for _ in $(seq 1 100); do
  # Snapshot first: grep -q under pipefail would report SIGPIPE as failure.
  names="$(find "$cgroup" -name cgroup.procs -exec cat {} + 2>/dev/null | xargs -r -I{} cat /proc/{}/comm 2>/dev/null || true)"
  if grep -qx 'runsc' <<<"$names" && grep -qx 'gvisor_sentry' <<<"$names"; then
    contained=yes; break
  fi
  sleep 0.1
done
[[ $contained == yes ]]
eq "$(cat "$cgroup/memory.max")" "$((3 * 1024 * 1024 * 1024))"
wait_status 25 REVIEW_REQUIRED
eq "$(col 25 "failure_code" cdr_results)" TIMEOUT
[[ -z "$(podman ps -a -q --filter name=dangerzone-doc-to-pixels)" ]]
eq "$(col 25 "count(*)" artifacts "AND kind='CDR_OUTPUT'")" 0

# Provider down: typed retryable failure with a delayed durable retry.
stop_provider
seed 26 unsigned.pdf
publish 26
wait_status 26 AWAITING_CDR 30 || true
for _ in $(seq 1 30); do [[ "$(col 26 "count(*)" cdr_results)" == 1 ]] && break; sleep 1; done
eq "$(col 26 "status||','||failure_code||','||retryable" cdr_results)" "FAILED,PROVIDER_UNAVAILABLE,true"
eq "$(q "SELECT status FROM scan_jobs WHERE id='scan-01J00000000000000000000026'")" AWAITING_CDR
eq "$(q "SELECT count(*) FROM outbox_events WHERE aggregate_id='scan-01J00000000000000000000026' AND event_type='CDR_QUEUED' AND available_at > now() + interval '200 seconds'")" 1

# Nothing in this stage can be released.
eq "$(q "SELECT count(*) FROM scan_jobs WHERE status IN ('READY','ARCHIVING') OR artifact_state IN ('FINAL_VALIDATED','PUBLISHED') OR policy='ALLOW_SANITIZED'")" 0
q "SELECT n.id, n.status, c.failure_code, c.duration_ms, c.output_bytes FROM scan_jobs n LEFT JOIN cdr_results c ON c.scan_id=n.id ORDER BY n.id"
echo "M3 CDR provider, isolation evidence, staging, typed failure, timeout containment, retry, replay, and immutability tests passed"
