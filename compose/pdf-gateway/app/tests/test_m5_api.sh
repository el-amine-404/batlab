#!/usr/bin/env bash
# M5 slice 1: the API surface for the web UI. Browser sessions (cookie flags,
# CSRF, Origin, idle expiry, logout), IDOR, scan lists, findings without document
# content, SSE, quarantine safe-delete, summary and health. Local PC; capped.
set -Eeuo pipefail
trap 'echo "test_m5_api.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
compose="$root/compose/pdf-gateway/app/tests/docker-compose.m4-gate.yml"
scratch="$(mktemp -d)"
export TEST_STORAGE="$scratch/storage" TEST_ARCHIVE="$scratch/archive" TEST_ADMIN_TOKEN="$(openssl rand -hex 32)"
dc=(docker compose -f "$compose")
psql=("${dc[@]}" exec -T postgres psql -U pdf_gateway -d pdf_gateway -v ON_ERROR_STOP=1 -q)
# localhost (not 127.0.0.1): curl and browsers treat it as a secure context for Secure cookies.
base=http://localhost:18289
limits=(--network none --read-only --tmpfs /tmp:size=32m --cap-drop ALL --security-opt no-new-privileges --cpus 1 --memory 512m)

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then "${dc[@]}" logs --no-color --tail=40 api >&2 || true; fi
  "${dc[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  docker run --rm --network none --user 10005:10005 -v "$TEST_ARCHIVE:/archive" --entrypoint sh \
    pdf-gateway-m4-test/archive-worker:local -c 'rm -rf /archive/archive /archive/staging /archive/manifests/*' >/dev/null 2>&1 || true
  docker run --rm --network none -v "$TEST_STORAGE/api:/data" --entrypoint sh pdf-gateway-m4-test/archive-worker:local -c 'rm -rf /data/*' >/dev/null 2>&1 || true
  chmod -R u+rwX "$scratch" 2>/dev/null || true
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

eq() { [[ "$1" == "$2" ]] || { echo "expected [$2], got [$1]" >&2; return 1; }; }
q() { "${psql[@]}" -Atc "$1"; }
scan_id() { echo "scan-01J000000000000000000000$1"; }
json() { python3 -c "import json,sys; v=json.load(sys.stdin); print(eval(sys.argv[1], {}, {'v': v}))" "$1"; }
bearer() { curl -sS -H "Authorization: Bearer $TEST_ADMIN_TOKEN" "$@"; }
# Cookie-session request; JAR selects the browser profile.
web() { curl -sS -b "$scratch/${JAR:-admin}.jar" -c "$scratch/${JAR:-admin}.jar" "$@"; }
code() { "$@" -o /dev/null -w '%{http_code}'; }
login() { curl -sS -c "$scratch/$1.jar" -D "$scratch/$1.headers" -H 'Content-Type: application/json' -d "{\"token\":\"$2\"}" -o "$scratch/$1.session" -w '%{http_code}' "$base/api/v1/session"; }

mkdir -p "$TEST_STORAGE"/{fixtures,cdr-output,text-extracts,api/quarantine} "$TEST_ARCHIVE/manifests"
chmod 0777 "$TEST_ARCHIVE" "$TEST_ARCHIVE/manifests" "$TEST_STORAGE/api" "$TEST_STORAGE/api/quarantine"
docker build -q -t pdf-gateway-m4-test/api:local "$root/compose/pdf-gateway/app/api" >/dev/null
docker build -q -t pdf-gateway-m4-test/analysis-worker:local "$root/compose/pdf-gateway/app/analysis-worker" >/dev/null
docker build -q -f "$root/compose/pdf-gateway/app/archive-worker/Dockerfile" -t pdf-gateway-m4-test/archive-worker:local "$root/compose/pdf-gateway/app" >/dev/null
python3 -I "$root/compose/pdf-gateway/app/tests/make_fixtures.py" "$TEST_STORAGE/fixtures"
chmod 0755 "$TEST_STORAGE/fixtures" "$TEST_STORAGE/cdr-output" "$TEST_STORAGE/text-extracts"; chmod 0644 "$TEST_STORAGE/fixtures"/*
"${dc[@]}" up -d --wait postgres rabbitmq api
"${dc[@]}" up -d --wait archive-worker
admin="$(q "SELECT id FROM users WHERE username='admin'")"
user_token="$(openssl rand -hex 32)"
q "INSERT INTO users(id,username,role) VALUES ('00000000-0000-0000-0000-0000000000b2','other','USER')"
q "INSERT INTO api_tokens(id,user_id,token_hash,label) VALUES (gen_random_uuid(),'00000000-0000-0000-0000-0000000000b2','$(printf %s "$user_token" | sha256sum | cut -d' ' -f1)','t')"
. "$root/compose/pdf-gateway/app/tests/lib_m4.sh"

# Two released documents, one held, one held signed original (with real files in quarantine).
seed 50 invoice-en.pdf; seed 51 unfamiliar.pdf
for n in 52 53; do
  printf '%%PDF-1.4 held original %s\n' "$n" >"$TEST_STORAGE/api/quarantine/quar-held$n.bin"; chmod 0666 "$TEST_STORAGE/api/quarantine/quar-held$n.bin"
done
q "INSERT INTO scan_jobs(id,owner_id,status,intent,cdr_mode,display_name,source_bytes,source_sha256,quarantine_object,detection,policy,original_retention) VALUES
   ('$(scan_id 52)','$admin','REVIEW_REQUIRED','ARCHIVE','RASTER','<img src=x onerror=alert(1)>.pdf',30,'$(printf 'a%.0s' {1..64})','quar-held52','UNKNOWN','REVIEW_REQUIRED','STANDARD'),
   ('$(scan_id 53)','$admin','REVIEW_REQUIRED','ARCHIVE','RASTER','signed.pdf',30,'$(printf 'b%.0s' {1..64})','quar-held53','CLEAN','REVIEW_REQUIRED','SIGNED_RESTRICTED')"
for _ in $(seq 1 60); do [[ "$(q "SELECT count(*) FROM scan_jobs WHERE status='READY'")" == 2 ]] && break; sleep 1; done
eq "$(q "SELECT count(*) FROM scan_jobs WHERE status='READY'")" 2

# --- Sessions ----------------------------------------------------------------
eq "$(curl -sS -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d '{"token":"0000000000000000000000000000000000000000"}' "$base/api/v1/session")" 401
eq "$(login admin "$TEST_ADMIN_TOKEN")" 201
cookie_line="$(grep -i '^set-cookie: __Host-pgw_session=' "$scratch/admin.headers")"
for flag in HttpOnly Secure 'SameSite=Strict' 'Path=/'; do grep -qi "; *$flag" <<<"$cookie_line" || { echo "cookie lacks $flag: $cookie_line" >&2; exit 1; }; done
! grep -qi 'domain=' <<<"$cookie_line"
! grep -q "$TEST_ADMIN_TOKEN" "$scratch/admin.session"
csrf="$(json 'v["csrfToken"]' <"$scratch/admin.session")"
eq "$(web "$base/api/v1/session" | json 'v["username"]+"|"+v["role"]+"|"+v["csrfToken"]')" "admin|ADMIN|$csrf"
eq "$(code web -X DELETE "$base/api/v1/quarantine/$(scan_id 52)")" 403
eq "$(web -X DELETE "$base/api/v1/quarantine/$(scan_id 52)" | json 'v["code"]')" CSRF_REJECTED
eq "$(code web -X DELETE -H "X-CSRF-Token: $(printf 'f%.0s' {1..64})" "$base/api/v1/quarantine/$(scan_id 52)")" 403
eq "$(code web -X DELETE -H "X-CSRF-Token: $csrf" -H 'Origin: https://evil.example' "$base/api/v1/quarantine/$(scan_id 52)")" 403
[[ -f $TEST_STORAGE/api/quarantine/quar-held52.bin ]]   # nothing happened on any rejected attempt

# --- Lists, contract, IDOR --------------------------------------------------
eq "$(web "$base/api/v1/scans" | json 'len(v["items"])')" 4
eq "$(web "$base/api/v1/scans?limit=1" | json 'v["nextCursor"]')" "$(scan_id 53)"
web "$base/api/v1/scans/$(scan_id 52)" | python3 -I "$root/compose/pdf-gateway/app/tests/check_scan_contract.py" "$root/compose/pdf-gateway/app/contracts/openapi.yaml" >/dev/null
eq "$(web "$base/api/v1/scans/$(scan_id 52)" | json 'v["displayName"]')" '<img src=x onerror=alert(1)>.pdf'
eq "$(web "$base/api/v1/quarantine" | json '",".join(sorted(i["scanId"][-2:] for i in v["items"]))')" "52,53"
eq "$(login other "$user_token")" 201
eq "$(JAR=other web "$base/api/v1/scans" | json 'len(v["items"])')" 0
eq "$(code curl -sS -b "$scratch/other.jar" "$base/api/v1/scans/$(scan_id 50)/findings")" 404
eq "$(code curl -sS -b "$scratch/other.jar" "$base/api/v1/scans/$(scan_id 50)/events")" 404
other_csrf="$(json 'v["csrfToken"]' <"$scratch/other.session")"
eq "$(code curl -sS -b "$scratch/other.jar" -X DELETE -H "X-CSRF-Token: $other_csrf" "$base/api/v1/quarantine/$(scan_id 52)")" 403

# --- Findings: typed evidence, no document content -----------------------------
web "$base/api/v1/scans/$(scan_id 50)/findings" >"$scratch/findings.json"
python3 - "$scratch/findings.json" <<'PY'
import json,sys
raw=open(sys.argv[1]).read(); v=json.loads(raw)
assert set(v) >= {"analysis","signature","cdr","validation","archive","steps","limitations"}, sorted(v)
assert v["analysis"]["policyDecision"]=="CDR_ELIGIBLE" and v["validation"][0]["verdict"]=="PASS", v
# No rules.json in this stack, so even the invoice is filed as an Unsorted fallback.
assert v["archive"]["category"]=="Unsorted" and v["archive"]["reviewState"]=="FALLBACK" and v["signature"]["state"]=="UNSIGNED"
assert set(v["analysis"]["textExtraction"]) == {"status","pages","characters"}
# Document text, extracted content and evidence blobs never reach the UI API.
for leak in ("29.99","Example Telecom S.A.","Total due","sourceSha256","evidence"):
    assert leak not in raw, leak
print("findings ok:", len(v["steps"]), "steps")
PY

# --- Server-sent events --------------------------------------------------------
timeout 15 curl -sS -N -b "$scratch/admin.jar" "$base/api/v1/scans/$(scan_id 50)/events" >"$scratch/events.txt" || true
grep -q '^event:status' "$scratch/events.txt"
grep '^data:' "$scratch/events.txt" | head -1 | sed 's/^data://' | json 'v["status"]+"|"+v["scanId"]' | grep -qx "READY|$(scan_id 50)"
eq "$(grep -c '^event:status' "$scratch/events.txt")" 1   # one change, then the stream completed (terminal)

# --- Quarantine safe-delete ----------------------------------------------------
eq "$(code web -X DELETE -H "X-CSRF-Token: $csrf" "$base/api/v1/quarantine/$(scan_id 50)")" 409   # released: never
eq "$(web -X DELETE -H "X-CSRF-Token: $csrf" "$base/api/v1/quarantine/$(scan_id 53)" | json 'v["code"]')" SIGNED_ORIGINAL
[[ -f $TEST_STORAGE/api/quarantine/quar-held53.bin ]]
eq "$(code web -X DELETE -H "X-CSRF-Token: $csrf" -H "Origin: $base" "$base/api/v1/quarantine/$(scan_id 52)")" 204
[[ ! -e $TEST_STORAGE/api/quarantine/quar-held52.bin ]]
eq "$(code web -X DELETE -H "X-CSRF-Token: $csrf" "$base/api/v1/quarantine/$(scan_id 53)?confirmSigned=true")" 204
eq "$(q "SELECT string_agg(status,',' ORDER BY id) FROM scan_jobs WHERE id IN ('$(scan_id 52)','$(scan_id 53)')")" CANCELLED,CANCELLED
eq "$(q "SELECT count(*) FROM audit_events WHERE action='QUARANTINE_DELETED'")" 2

# --- Summary and health --------------------------------------------------------
web "$base/api/v1/dashboard/summary" | json 'str(v["scans"])+"|"+str(v["archived"])+"|"+str(v["byStatus"].get("READY"))' | grep -qx "4|2|2"
# Homepage tile: system-wide counts with a READ_ONLY bearer token, nothing identifying.
ro_token="$(openssl rand -hex 32)"
q "INSERT INTO users(id,username,role) VALUES ('00000000-0000-0000-0000-0000000000b3','homepage','READ_ONLY')"
q "INSERT INTO api_tokens(id,user_id,token_hash,label) VALUES (gen_random_uuid(),'00000000-0000-0000-0000-0000000000b3','$(printf %s "$ro_token" | sha256sum | cut -d' ' -f1)','homepage')"
curl -sS -H "Authorization: Bearer $ro_token" "$base/api/v1/system/status" | json 'str(v["archived"])+"|"+str(v["blocked"]>=0)+"|"+",".join(sorted(v))' \
  | grep -qx "2|True|archived,blocked,held,inProgress,outboxBacklog,toReview,uploads24h"
[[ $(code curl -sS "$base/api/v1/system/status") == 401 ]]
web "$base/api/v1/system/health" | json 'v["database"]+"|"+v["broker"]+"|"+str(v["archiveReadable"])' | grep -qx "UP|UP|True"

# --- Re-uploading a file already in the archive: no second copy ------------------
upload() {  # upload TOKEN KEY -> HTTP code; headers in $scratch/upload.headers
  curl -sS -o "$scratch/upload.json" -D "$scratch/upload.headers" -w '%{http_code}' -H "Authorization: Bearer $1" \
    -H "Idempotency-Key: $2" -F intent=ARCHIVE_AND_IMPORT -F cdrMode=RASTER \
    -F "file=@$TEST_STORAGE/fixtures/streamco-invoice.pdf;type=application/pdf" "$base/api/v1/scans"
}
eq "$(upload "$TEST_ADMIN_TOKEN" dup-first-000000001)" 202
first="$(json 'v["scanId"]' <"$scratch/upload.json")"
before="$(q "SELECT count(*) FROM scan_jobs")"
eq "$(upload "$TEST_ADMIN_TOKEN" dup-second-00000001)" 409                       # same bytes, a new upload
eq "$(json 'v["code"]' <"$scratch/upload.json")" DUPLICATE_UPLOAD
grep -qi "^location: /api/v1/scans/$first" "$scratch/upload.headers"
eq "$(q "SELECT count(*) FROM scan_jobs")" "$before"                              # nothing stored or queued
eq "$(upload "$user_token" dup-other-000000001)" 202                              # another person's archive is theirs
q "UPDATE scan_jobs SET status='FAILED' WHERE id='$first'"
eq "$(upload "$TEST_ADMIN_TOKEN" dup-retry-00000001)" 202                         # a failed attempt can be retried

# --- Expiry and logout ---------------------------------------------------------
q "UPDATE web_sessions SET last_seen_at=now()-interval '3 hours' WHERE user_id='00000000-0000-0000-0000-0000000000b2'"
eq "$(code curl -sS -b "$scratch/other.jar" "$base/api/v1/scans")" 401
eq "$(code web -X DELETE -H "X-CSRF-Token: $csrf" "$base/api/v1/session")" 204
eq "$(code web "$base/api/v1/scans")" 401
eq "$(q "SELECT count(*) FROM web_sessions WHERE revoked_at IS NOT NULL")" 1
eq "$(q "SELECT count(*) FROM web_sessions WHERE session_hash IS NULL OR length(session_hash)<>64")" 0
echo "M5 API: sessions, CSRF, Origin, IDOR, lists, findings, SSE, quarantine, summary, health and logout tests passed"

