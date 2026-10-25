#!/usr/bin/env bash
# M7: optional OCR/AI enrichment on the AI laptop (this PC), pulled by the real
# agent with real Tesseract and the real LM Studio. The gateway stack is local.
#   compose/pdf-gateway/app/tests/test_m7_enrichment.sh        (LM Studio must be running)
set -Eeuo pipefail
trap 'echo "test_m7_enrichment.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
scratch="$(mktemp -d)"
export TEST_STORAGE="$scratch/storage" TEST_ARCHIVE="$scratch/archive" TEST_ADMIN_TOKEN="$(openssl rand -hex 32)"
dc=(docker compose -f "$root/compose/pdf-gateway/app/tests/docker-compose.m4-gate.yml" -f "$root/compose/pdf-gateway/app/tests/docker-compose.m7-enrichment.override.yml")
psql=("${dc[@]}" exec -T postgres psql -U pdf_gateway -d pdf_gateway -v ON_ERROR_STOP=1 -q)
base=http://127.0.0.1:18289
model="${PDF_GATEWAY_LLM_MODEL:-qwen/qwen3-4b-2507}"
limits=(--network none --read-only --tmpfs /tmp:size=32m --cap-drop ALL --security-opt no-new-privileges --cpus 1 --memory 512m)
agent_token="$(openssl rand -hex 32)"; user_token="$(openssl rand -hex 32)"

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then "${dc[@]}" logs --no-color --tail=30 archive-worker >&2 || true; docker logs --tail=30 pgw-m7-agent >&2 2>/dev/null || true; fi
  docker rm -f -v pgw-m7-agent >/dev/null 2>&1 || true
  "${dc[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  docker run --rm --network none --user 10005:10005 -v "$TEST_ARCHIVE:/archive" --entrypoint sh pdf-gateway-m4-test/archive-worker:local \
    -c 'rm -rf /archive/archive /archive/staging /archive/manifests/*' >/dev/null 2>&1 || true
  docker run --rm --network none -v "$TEST_STORAGE/api:/data" --entrypoint sh pdf-gateway-m4-test/archive-worker:local -c 'rm -rf /data/*' >/dev/null 2>&1 || true
  chmod -R u+rwX "$scratch" 2>/dev/null || true
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

eq() { [[ "$1" == "$2" ]] || { echo "expected [$2], got [$1]" >&2; return 1; }; }
q() { "${psql[@]}" -Atc "$1"; }
scan_id() { echo "scan-01J000000000000000000000$1"; }
doc_id() { echo "doc-01J000000000000000000000$1"; }
json() { python3 -c "import json,sys; v=json.load(sys.stdin); print(eval(sys.argv[1], {}, {'v': v}))" "$1"; }
code() { curl -sS -o /dev/null -w '%{http_code}' "$@"; }
ai_state() { curl -sS -H "Authorization: Bearer $TEST_ADMIN_TOKEN" "$base/api/v1/system/ai" | json 'v["state"]'; }
agent() {  # agent NAME [env...] -- run the real agent container on the host network
  local name=$1; shift
  docker run "$@" --name "$name" --network host --read-only --tmpfs /tmp:size=512m --cap-drop ALL --security-opt no-new-privileges \
    --cpus 4 --memory 2g -e PDF_GATEWAY_URL="$base" -e PDF_GATEWAY_AGENT_TOKEN="$agent_token" -e PDF_GATEWAY_AGENT_POLL_SECONDS=3 \
    pdf-gateway-m7-test/enrichment-agent:local "${AGENT_ARGS[@]}"
}
jobs_done() { [[ "$(q "SELECT count(*) FROM enrichment_jobs WHERE status IN ('WAITING_FOR_AI','LEASED','SUBMITTED')")" == 0 ]]; }

curl -fsS -m 3 http://127.0.0.1:1234/v1/models | json '[m["id"] for m in v["data"]]' | grep -q "$model" || { echo "LM Studio with $model is required" >&2; exit 1; }
mkdir -p "$TEST_STORAGE"/{fixtures,cdr-output,text-extracts,api/quarantine,api/multipart} "$TEST_ARCHIVE/manifests"
chmod 0777 "$TEST_ARCHIVE" "$TEST_ARCHIVE/manifests" "$TEST_STORAGE/api" "$TEST_STORAGE/api/quarantine" "$TEST_STORAGE/api/multipart"
for image in api analysis-worker; do docker build -q -t "pdf-gateway-m4-test/$image:local" "$root/compose/pdf-gateway/app/$image" >/dev/null; done
docker build -q -f "$root/compose/pdf-gateway/app/archive-worker/Dockerfile" -t pdf-gateway-m4-test/archive-worker:local "$root/compose/pdf-gateway/app" >/dev/null
docker build -q -t pdf-gateway-m7-test/enrichment-agent:local "$root/compose/pdf-gateway/app/enrichment-agent" >/dev/null
python3 -I "$root/compose/pdf-gateway/app/tests/make_fixtures.py" "$TEST_STORAGE/fixtures"
chmod 0777 "$TEST_STORAGE/fixtures"
# An image-only "scan" of the invoice: rendered to pixels, so no text layer survives.
docker run --rm "${limits[@]}" --user "$(id -u):$(id -g)" -v "$TEST_STORAGE/fixtures:/f" --entrypoint sh pdf-gateway-m4-test/analysis-worker:local -c \
  'pdftoppm -r 150 -gray -singlefile /f/invoice-en.pdf /tmp/page && python3 - /tmp/page.pgm /f/scanned-invoice.pdf <<"PY"
import re,sys,zlib
data=open(sys.argv[1],"rb").read(); m=re.match(rb"P5\s+(\d+)\s+(\d+)\s+(\d+)\s",data); w,h=int(m.group(1)),int(m.group(2)); px=zlib.compress(data[m.end():])
objs=[b"<< /Type /Catalog /Pages 2 0 R >>",b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
      b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>",
      b"<< /Length 31 >>\nstream\nq 400 0 0 200 0 0 cm /Im0 Do Q\nendstream",
      b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n" % (w,h,len(px)) + px + b"\nendstream"]
out=bytearray(b"%PDF-1.4\n"); off=[]
for n,o in enumerate(objs,1): off.append(len(out)); out.extend(b"%d 0 obj\n" % n + o + b"\nendobj\n")
x=len(out); out.extend(b"xref\n0 6\n0000000000 65535 f \n"); [out.extend(b"%010d 00000 n \n" % o) for o in off]
out.extend(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % x); open(sys.argv[2],"wb").write(out)
PY'
chmod 0755 "$TEST_STORAGE/fixtures" "$TEST_STORAGE/cdr-output" "$TEST_STORAGE/text-extracts"; chmod 0644 "$TEST_STORAGE/fixtures"/*
cat >"$TEST_ARCHIVE/manifests/rules.json" <<'JSON'
{"schemaVersion": "1.0.0", "revision": 1, "updatedAt": "2026-10-25T12:00:00Z", "rules": [
 {"id": "example-telecom-invoice", "issuer": "Example Telecom", "match": {"all": ["example telecom"], "any": ["invoice"]},
  "category": "Finance", "folder": "Invoices", "documentType": "invoice", "title": "Internet Invoice", "tags": ["finance", "internet", "invoice"]}]}
JSON
chmod 0644 "$TEST_ARCHIVE/manifests/rules.json"

"${dc[@]}" up -d --wait postgres rabbitmq api
"${dc[@]}" up -d --wait archive-worker
admin="$(q "SELECT id FROM users WHERE username='admin'")"
q "INSERT INTO users(id,username,role) VALUES ('00000000-0000-0000-0000-0000000000e1','t1000-agent','ENRICHMENT'),('00000000-0000-0000-0000-0000000000e2','someone','USER')"
q "INSERT INTO api_tokens(id,user_id,token_hash,label) VALUES
   (gen_random_uuid(),'00000000-0000-0000-0000-0000000000e1','$(printf %s "$agent_token" | sha256sum | cut -d' ' -f1)','agent'),
   (gen_random_uuid(),'00000000-0000-0000-0000-0000000000e2','$(printf %s "$user_token" | sha256sum | cut -d' ' -f1)','user')"
. "$root/compose/pdf-gateway/app/tests/lib_m4.sh"

eq "$(ai_state)" UNKNOWN
seed 80 scanned-invoice.pdf; seed 81 unfamiliar.pdf; seed 82 injection.pdf
for _ in $(seq 1 60); do [[ "$(q "SELECT count(*) FROM scan_jobs WHERE status='READY'")" == 3 ]] && break; sleep 1; done
eq "$(q "SELECT count(*) FROM scan_jobs WHERE status='READY'")" 3                      # the archive never waits for AI
eq "$(q "SELECT string_agg(kind||':'||status,',' ORDER BY document_id) FROM enrichment_jobs")" "OCR:WAITING_FOR_AI,SUGGEST:WAITING_FOR_AI,SUGGEST:WAITING_FOR_AI"
eq "$(q "SELECT category FROM archive_documents WHERE document_id='$(doc_id 80)'")" Unsorted
sha80="$(q "SELECT sha256 FROM archive_documents WHERE document_id='$(doc_id 80)'")"
before82="$(q "SELECT archive_path||'|'||metadata_revision FROM archive_documents WHERE document_id='$(doc_id 82)'")"
scans_before="$(q "SELECT string_agg(status||policy||detection,',' ORDER BY id) FROM scan_jobs")"

# --- Scope: the agent token can do enrichment and nothing else ---------------------
eq "$(code "$base/api/v1/enrichment/agents/t1000-laptop/lease" -X POST)" 401
eq "$(code -H "Authorization: Bearer $user_token" -X POST "$base/api/v1/enrichment/agents/t1000-laptop/lease")" 403
for path in /api/v1/scans /api/v1/archive /api/v1/quarantine /api/v1/dashboard/summary "/api/v1/archive/$(doc_id 80)/file"; do
  eq "$(code -H "Authorization: Bearer $agent_token" "$base$path")" 403
done
eq "$(code -H "Authorization: Bearer $agent_token" -H "X-Agent-Id: t1000-laptop" "$base/api/v1/enrichment/jobs/$(q "SELECT id FROM enrichment_jobs LIMIT 1")/document")" 403  # not registered yet

# --- The real agent: OCR with Tesseract, suggestions with LM Studio -------------------
AGENT_ARGS=(); agent pgw-m7-agent -d -e PDF_GATEWAY_AGENT_ID=t1000-laptop -e PDF_GATEWAY_LLM_MODEL="$model" >/dev/null
for _ in $(seq 1 150); do jobs_done && break; sleep 2; done
jobs_done
q "SELECT kind, status, COALESCE(error_code,'-'), attempts FROM enrichment_jobs ORDER BY document_id"
eq "$(q "SELECT string_agg(status,',' ORDER BY document_id) FROM enrichment_jobs")" "COMPLETED,COMPLETED,COMPLETED"
# OCR made the scan readable, and the reviewed rule re-filed it out of Unsorted.
eq "$(q "SELECT category||'|'||review_state||'|'||(sidecar->'textExtraction'->>'method') FROM archive_documents WHERE document_id='$(doc_id 80)'")" "Finance|RULE|ocr"
path80="$TEST_ARCHIVE/archive/$(q "SELECT archive_path FROM archive_documents WHERE document_id='$(doc_id 80)'")"
[[ $path80 == *"Finance/2026/Invoices/2026-10-15 - Example Telecom"* ]]
grep -qi "example telecom" "$path80/content.txt"
eq "$(sha256sum "$path80/$(q "SELECT pdf_file FROM archive_documents WHERE document_id='$(doc_id 80)'")" | cut -d' ' -f1)" "$sha80"   # PDF unchanged
docker run --rm "${limits[@]}" --user 10005:10005 -v "$TEST_ARCHIVE:/archive:ro" pdf-gateway-m4-test/archive-worker:local --root /archive verify | json 'v["ok"]' | grep -qx True
# AI proposals: evidence-backed, controlled vocabulary, never applied by themselves.
q "SELECT right(document_id,2), fields::text FROM ai_suggestions ORDER BY document_id" | cut -c1-400
python3 - "$(q "SELECT COALESCE(jsonb_agg(jsonb_build_object('doc',document_id,'fields',fields,'status',status)),'[]') FROM ai_suggestions")" <<'PY'
import json,sys
rows=json.loads(sys.argv[1])
for row in rows:
    f=row["fields"]; assert row["status"]=="PROPOSED", row
    assert all(t in {"finance","invoice","internet","utilities","bank","statement","tax","medical","employment","payslip","contract","insurance","identity","vehicle","property","education"} for t in f.get("tags",[])), f
    assert "hacked" not in json.dumps(f.get("tags",[])), f
print("proposals:", len(rows), "all evidence-validated, vocabulary-only, unapplied")
PY
eq "$(q "SELECT archive_path||'|'||metadata_revision FROM archive_documents WHERE document_id='$(doc_id 82)'")" "$before82"   # injection changed nothing
eq "$(q "SELECT string_agg(status||policy||detection,',' ORDER BY id) FROM scan_jobs")" "$scans_before"                       # no verdict moved
eq "$(ai_state)" READY

# --- Applying a proposal is an explicit, CAS-protected user edit ------------------------
sid="$(q "SELECT id FROM ai_suggestions WHERE document_id='$(doc_id 81)' AND (fields ? 'title' OR fields ? 'tags') LIMIT 1")"
if [[ -z $sid ]]; then
  # The model may cautiously propose no title/tags; exercise the apply path with a known proposal.
  sid="$(q "INSERT INTO ai_suggestions(id,document_id,job_id,model,prompt_version,fields)
            SELECT gen_random_uuid(),'$(doc_id 81)',id,'fixture','suggest-1','{\"title\":{\"value\":\"Garden meeting notes\",\"evidence\":\"Meeting notes about the garden\"},\"tags\":[\"personal\"]}'
              FROM enrichment_jobs WHERE document_id='$(doc_id 81)' LIMIT 1 RETURNING id")"
fi
if [[ -n $sid ]]; then
  rev="$(q "SELECT metadata_revision FROM archive_documents WHERE document_id='$(doc_id 81)'")"
  eq "$(code -X POST -H "Authorization: Bearer $TEST_ADMIN_TOKEN" -H "If-Match: \"rev-$((rev + 5))\"" "$base/api/v1/archive/$(doc_id 81)/suggestions/$sid/apply")" 409
  eq "$(code -X POST -H "Authorization: Bearer $TEST_ADMIN_TOKEN" -H "If-Match: \"rev-$rev\"" "$base/api/v1/archive/$(doc_id 81)/suggestions/$sid/apply")" 202
  for _ in $(seq 1 30); do [[ "$(q "SELECT metadata_revision FROM archive_documents WHERE document_id='$(doc_id 81)'")" -gt $rev ]] && break; sleep 1; done
  eq "$(q "SELECT status FROM ai_suggestions WHERE id='$sid'")" APPLIED
  eq "$(q "SELECT title FROM archive_documents WHERE document_id='$(doc_id 81)'")" "$(q "SELECT fields->'title'->>'value' FROM ai_suggestions WHERE id='$sid'")"
fi
other="$(q "SELECT id FROM ai_suggestions WHERE status='PROPOSED' LIMIT 1")"
[[ -z $other ]] || eq "$(code -X POST -H "Authorization: Bearer $TEST_ADMIN_TOKEN" "$base/api/v1/archive/$(q "SELECT document_id FROM ai_suggestions WHERE id='$other'")/suggestions/$other/dismiss")" 204
docker rm -f -v pgw-m7-agent >/dev/null

# --- Malformed or hostile results trip the breaker ----------------------------------------
H=(-H "Authorization: Bearer $agent_token" -H "X-Agent-Id: rogue" -H 'Content-Type: application/json')
eq "$(code "${H[@]}" -X POST -d '{"version":"rogue"}' "$base/api/v1/enrichment/agents/rogue/heartbeat")" 204
for attempt in 1 2 3; do
  q "INSERT INTO enrichment_jobs(id,document_id,artifact_sha256,kind) SELECT gen_random_uuid(),document_id,sha256,'SUGGEST' FROM archive_documents WHERE document_id='$(doc_id 81)' ON CONFLICT DO NOTHING" >/dev/null
  q "UPDATE enrichment_jobs SET status='WAITING_FOR_AI', result=NULL, attempts=0 WHERE document_id='$(doc_id 81)' AND kind='SUGGEST'"
  job="$(curl -sS "${H[@]}" -X POST "$base/api/v1/enrichment/agents/rogue/lease?kinds=SUGGEST" | json 'v["jobId"]')"
  eq "$(code "${H[@]}" -X POST -d '{"kind":"SUGGEST","model":"x","promptVersion":"x","fields":"not-an-object"}' "$base/api/v1/enrichment/jobs/$job/result")" 202
  for _ in $(seq 1 20); do [[ "$(q "SELECT status FROM enrichment_jobs WHERE id='$job'")" == FAILED ]] && break; sleep 1; done
  eq "$(q "SELECT status||','||error_code FROM enrichment_jobs WHERE id='$job'")" FAILED,INVALID_RESULT
done
eq "$(code "${H[@]}" -X POST "$base/api/v1/enrichment/agents/rogue/lease")" 429
eq "$(ai_state)" DEGRADED
eq "$(code "${H[@]}" -X POST -d 'not json' "$base/api/v1/enrichment/jobs/$job/result")" 409                       # no lease any more
eq "$(code -H "Authorization: Bearer $agent_token" -H 'Content-Type: application/json' -X POST -d '{}' "$base/api/v1/enrichment/agents/t1000-laptop/heartbeat")" 204
eq "$(code -H "Authorization: Bearer $user_token" -H 'Content-Type: application/json' -X POST -d '{}' "$base/api/v1/enrichment/agents/t1000-laptop/heartbeat")" 403

# --- Model missing, service down, offline and resume ---------------------------------------
AGENT_ARGS=(--once); agent pgw-m7-agent --rm -e PDF_GATEWAY_AGENT_ID=t1000-laptop -e PDF_GATEWAY_LLM_MODEL=no-such-model >/dev/null
eq "$(ai_state)" MODEL_MISSING
agent pgw-m7-agent --rm -e PDF_GATEWAY_AGENT_ID=t1000-laptop -e PDF_GATEWAY_LLM_URL=http://127.0.0.1:9 >/dev/null
eq "$(ai_state)" SERVICE_DOWN
q "UPDATE ai_agents SET last_seen_at = now() - interval '6 hours'"
eq "$(ai_state)" OFFLINE
seed 83 scanned-invoice.pdf                                       # published while the laptop is "away"
for _ in $(seq 1 60); do [[ "$(q "SELECT status FROM enrichment_jobs WHERE document_id='$(doc_id 83)'")" == WAITING_FOR_AI ]] && break; sleep 1; done
eq "$(q "SELECT status FROM scan_jobs WHERE id='scan-01J00000000000000000000083'")" READY
AGENT_ARGS=(); agent pgw-m7-agent -d -e PDF_GATEWAY_AGENT_ID=t1000-laptop -e PDF_GATEWAY_LLM_MODEL="$model" >/dev/null
for _ in $(seq 1 90); do [[ "$(q "SELECT status FROM enrichment_jobs WHERE document_id='$(doc_id 83)' AND kind='OCR'")" == COMPLETED ]] && break; sleep 2; done
eq "$(q "SELECT category FROM archive_documents WHERE document_id='$(doc_id 83)'")" Finance
docker rm -f -v pgw-m7-agent >/dev/null
echo "M7 enrichment: scope, OCR re-filing, evidence-checked AI proposals, injection, breaker, availability states and resume passed"
