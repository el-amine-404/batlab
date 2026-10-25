# Shared helpers for the Lab2 M3 stage tests. Callers set: root, compose,
# scratch, TEST_STORAGE. Requires the rootless Dangerzone account on Lab2.

export CONTAINERS_CONF=/etc/batlab/pdf-cdr/containers.conf
unit="pdf-cdr-provider-test-$$"
provider="$root/hosts/lab2/pdf-cdr-provider/provider.py"
unit_file="$root/hosts/lab2/pdf-cdr-provider/pdf-cdr-provider.service"
# Callers may predefine dc (e.g. production compose plus a test override).
if ! declare -p dc >/dev/null 2>&1; then dc=(docker compose -f "$compose"); fi
psql=("${dc[@]}" exec -T postgres psql -U pdf_gateway -d pdf_gateway -v ON_ERROR_STOP=1 -q)
USER_ID=00000000-0000-0000-0000-000000000001

eq() { [[ "$1" == "$2" ]] || { echo "expected [$2], got [$1]" >&2; return 1; }; }
q() { "${psql[@]}" -Atc "$1"; }
scan_id() { echo "scan-01J000000000000000000000$1"; }
object_id() { echo "quar-01J000000000000000000000$1"; }
sv() { q "SELECT $2 FROM scan_jobs WHERE id='$(scan_id "$1")'"; }

prepare_storage() {
  mkdir -p "$TEST_STORAGE"/{fixtures,quarantine,cdr-output,cdr-socket,validation-reports,signature-reports,reports,multipart,builder} "$scratch/provider"
  chmod 0755 "$TEST_STORAGE" "$TEST_STORAGE/quarantine" "$TEST_STORAGE/cdr-socket" "$TEST_STORAGE/fixtures"
  chmod 0777 "$TEST_STORAGE/cdr-output" "$TEST_STORAGE/validation-reports" "$TEST_STORAGE/signature-reports" \
    "$TEST_STORAGE/reports" "$TEST_STORAGE/multipart" "$TEST_STORAGE/builder"
  python3 -I "$root/compose/pdf-gateway/app/tests/make_fixtures.py" "$TEST_STORAGE/fixtures"
  chmod 0644 "$TEST_STORAGE/fixtures"/*
}

# Lab2's default bridge has no DNS, so image builds use host networking. With
# PDF_GATEWAY_PREBUILT=1 (set by tools/lab2_test.sh) images were built off-Lab2
# and loaded, because builds are not CPU-capped and must not heat Lab2.
build_image() {
  [[ ${PDF_GATEWAY_PREBUILT:-} == 1 ]] && { docker image inspect "pdf-gateway-m3-test/$1:local" >/dev/null; return; }
  docker build -q --network host -t "pdf-gateway-m3-test/$1:local" "$root/compose/pdf-gateway/app/$1" >/dev/null
}

stop_provider() { systemctl --user stop "$unit" >/dev/null 2>&1 || true; }
start_provider() {
  local timeout=$1 properties
  mapfile -t properties < <(grep -E '^(Nice|IOSchedulingClass|CPUQuota|MemoryMax|MemorySwapMax|TasksMax)=' "$unit_file" | sed 's/^/--property=/')
  stop_provider
  systemd-run --user --quiet --collect --unit "$unit" "${properties[@]}" \
    --setenv=CONTAINERS_CONF="$CONTAINERS_CONF" -- \
    /usr/bin/python3 -I "$provider" --socket "$TEST_STORAGE/cdr-socket/cdr.sock" \
    --allowed-uid 10004 --scratch "$scratch/provider" --timeout "$timeout"
  for _ in $(seq 1 50); do [[ -S $TEST_STORAGE/cdr-socket/cdr.sock ]] && return; sleep 0.2; done
  echo "provider did not start" >&2; return 1
}

wait_clamav_fresh() {
  for _ in $(seq 1 120); do
    "${dc[@]}" exec -T clamav clamdscan --reload >/dev/null 2>&1 || true
    if version=$("${dc[@]}" exec -T clamav clamdscan --version 2>/dev/null) \
       && python3 - "$version" <<'PY'
import datetime as dt,sys
parts=sys.argv[1].strip().split("/")
if len(parts) < 3: raise SystemExit(1)
stamp=dt.datetime.strptime(parts[2], "%a %b %d %H:%M:%S %Y").replace(tzinfo=dt.timezone.utc)
raise SystemExit(0 if (dt.datetime.now(dt.timezone.utc)-stamp).total_seconds() <= 48*3600 else 1)
PY
    then return 0; fi
    sleep 2
  done
  echo "ClamAV definitions did not become fresh" >&2; return 1
}

# Stage-isolated seeding: committed M2/signature evidence for a CDR-eligible job.
seed_eligible() {
  local n=$1 fixture=$2 scan object hash bytes
  scan="$(scan_id "$n")"; object="$(object_id "$n")"
  cp "$TEST_STORAGE/fixtures/$fixture" "$TEST_STORAGE/quarantine/$object.bin"; chmod 0644 "$TEST_STORAGE/quarantine/$object.bin"
  hash="$(sha256sum "$TEST_STORAGE/quarantine/$object.bin" | cut -d' ' -f1)"; bytes="$(stat -c %s "$TEST_STORAGE/quarantine/$object.bin")"
  "${psql[@]}" <<SQL
INSERT INTO scan_jobs(id,owner_id,status,intent,cdr_mode,display_name,source_bytes,source_sha256,quarantine_object,detection,policy)
VALUES ('$scan','$USER_ID','AWAITING_CDR','ARCHIVE','RASTER','$fixture',$bytes,'$hash','$object','CLEAN','CDR_ELIGIBLE');
INSERT INTO job_steps(id,scan_id,step_type,status,attempt,started_at,finished_at,details)
VALUES (gen_random_uuid(),'$scan','ANALYSIS','SUCCEEDED',1,now(),now(),'{"policyDecision":"CDR_ELIGIBLE","detection":"CLEAN","sourceSha256":"$hash"}');
INSERT INTO signature_verifications(id,scan_id,attempt,source_sha256,state,policy_decision,preserve_original,signature_count,report)
VALUES (gen_random_uuid(),'$scan',1,'$hash','UNSIGNED','CDR_REQUIRED',false,0,'{}');
INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (gen_random_uuid(),'SCAN','$scan','CDR_QUEUED',jsonb_build_object('scanId','$scan'));
SQL
}

# Stage-isolated seeding of a staged "CDR output", to drive final validation
# with outputs a compromised converter could produce. RECORDED is the file
# whose hash is recorded; OUTPUT is what is actually on disk.
seed_output() {
  local n=$1 source=$2 recorded=$3 output=${4:-$3} scan object hash bytes out_hash
  scan="$(scan_id "$n")"; object="$(object_id "$n")"
  cp "$TEST_STORAGE/fixtures/$source" "$TEST_STORAGE/quarantine/$object.bin"; chmod 0644 "$TEST_STORAGE/quarantine/$object.bin"
  hash="$(sha256sum "$TEST_STORAGE/quarantine/$object.bin" | cut -d' ' -f1)"; bytes="$(stat -c %s "$TEST_STORAGE/quarantine/$object.bin")"
  cp "$TEST_STORAGE/fixtures/$output" "$TEST_STORAGE/cdr-output/$scan-1.pdf"; chmod 0644 "$TEST_STORAGE/cdr-output/$scan-1.pdf"
  out_hash="$(sha256sum "$TEST_STORAGE/fixtures/$recorded" | cut -d' ' -f1)"
  "${psql[@]}" <<SQL
INSERT INTO scan_jobs(id,owner_id,status,intent,cdr_mode,display_name,source_bytes,source_sha256,quarantine_object,detection,policy,artifact_state)
VALUES ('$scan','$USER_ID','VALIDATING_OUTPUT','ARCHIVE','RASTER','$source',$bytes,'$hash','$object','CLEAN','CDR_ELIGIBLE','SANITIZED_STAGED');
INSERT INTO artifacts(id,scan_id,kind,storage_object,sha256,bytes,state)
VALUES ('cdr-${scan:5}-1','$scan','CDR_OUTPUT','cdr-output/$scan-1.pdf','$out_hash',$(stat -c %s "$TEST_STORAGE/fixtures/$recorded"),'SANITIZED_STAGED');
INSERT INTO cdr_results(id,scan_id,attempt,status,source_sha256,output_artifact_id,output_sha256,output_bytes,image_digest)
VALUES (gen_random_uuid(),'$scan',1,'SUCCEEDED','$hash','cdr-${scan:5}-1','$out_hash',$(stat -c %s "$TEST_STORAGE/fixtures/$recorded"),'sha256:seeded');
INSERT INTO job_steps(id,scan_id,step_type,status,attempt,started_at,finished_at) VALUES (gen_random_uuid(),'$scan','CDR','SUCCEEDED',1,now(),now());
INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (gen_random_uuid(),'SCAN','$scan','VALIDATION_QUEUED',jsonb_build_object('scanId','$scan'));
SQL
}

relay() { "${dc[@]}" run --rm --no-deps -T relay "$@" >/dev/null 2>&1; }

# Relay outbox events until no listed scan is in a working state.
pump() {
  local deadline=$((SECONDS + ${PUMP_SECONDS:-300})) busy
  while (( SECONDS < deadline )); do
    relay CDR_QUEUED VALIDATION_QUEUED
    busy="$(q "SELECT count(*) FROM scan_jobs WHERE id = ANY(ARRAY[$(printf "'%s'," "${@/#/scan-01J000000000000000000000}" | sed 's/,$//')]) AND status IN ('AWAITING_CDR','SANITIZING','VALIDATING_OUTPUT','QUEUED','SCANNING','ANALYZING','AWAITING_POLICY')")"
    [[ $busy == 0 ]] && return 0
    sleep 2
  done
  echo "jobs still busy after pump timeout" >&2
  q "SELECT id,status FROM scan_jobs ORDER BY id" >&2
  return 1
}

# Lab2 runs live services; a test must never be what overheats it. Wait for a
# cool start, then abort (running the cleanup trap) if the package runs hot.
package_temp() {
  local zone
  for zone in /sys/class/thermal/thermal_zone*; do
    [[ $(cat "$zone/type" 2>/dev/null) == x86_pkg_temp ]] && { echo $(( $(cat "$zone/temp") / 1000 )); return; }
  done
  echo 0
}
thermal_guard() {
  local start_max=${THERMAL_START_MAX:-75} abort_at=${THERMAL_ABORT_AT:-85} parent=$$
  for _ in $(seq 1 60); do (( $(package_temp) < start_max )) && break; sleep 10; done
  (( $(package_temp) < start_max )) || { echo "Lab2 stayed above ${start_max}°C; not starting" >&2; return 1; }
  trap 'echo "aborted: Lab2 package above ${THERMAL_ABORT_AT:-85}°C" >&2; exit 143' TERM
  ( while kill -0 "$parent" 2>/dev/null; do
      if (( $(package_temp) >= abort_at )); then kill -TERM "$parent"; exit; fi
      # 1 s: at 5 s, 87-88 °C peaks fell between samples (2026-10-25).
      sleep 1
    done ) &
  echo "thermal guard: start $(package_temp)°C, abort at ${abort_at}°C"
}
