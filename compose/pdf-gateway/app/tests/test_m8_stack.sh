#!/usr/bin/env bash
# M8: the homelab stack file (compose/pdf-gateway/docker-compose.yml) as `make`
# runs it, on folders made by the Makefile's own `setup` lines, behind the
# homelab Caddy site (compose/caddy/conf/pdf.caddy): the local CA served over
# HTTP, HTTPS sign-in with the Secure cookie, a browser upload with CSRF and the
# https Origin, the stages writing as their own uids, Homepage's READ_ONLY card
# through the web container, and no release while the CDR provider is absent.
set -Eeuo pipefail
trap 'echo "test_m8_stack.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
scratch="$(mktemp -d)"
project=pdf-gateway-m8-test
caddy=pgw-m8-caddy
created_network=0
env_file="$scratch/test.env"
dc=(docker compose --env-file "$root/compose/versions.env" --env-file "$env_file" --env-file "$root/hosts/lab2/compose.env"
    -f "$root/compose/pdf-gateway/docker-compose.yml" -p "$project")

cleanup() {
  docker rm -f -v "$caddy" >/dev/null 2>&1 || true
  "${dc[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  ((created_network)) && docker network rm home_server >/dev/null 2>&1 || true
  # Files owned by the stages' uids: remove them as root.
  docker run --rm -u 0 -v "$scratch:/s" "$IMAGE" sh -c 'rm -rf /s/volumes /s/data' >/dev/null 2>&1 || true
  rm -rf "$scratch"
}
trap cleanup EXIT

eq() { [[ $1 == "$2" ]] || { echo "expected [$2], got [$1]" >&2; return 1; }; }
json() { python3 -c "import json,sys; v=json.load(sys.stdin); print(eval(sys.argv[1], {}, {'v': v}))" "$1"; }

set -a; source "$root/compose/versions.env"; set +a
IMAGE="$IMAGE_CADDY:$TAG_CADDY"
admin="$(openssl rand -hex 32)"
cat >"$env_file" <<EOF
PDF_GATEWAY_PROJECT_NAME=$project
TZ=UTC
VOLUMES_ROOT=$scratch/volumes
DATA_ROOT=$scratch/data
PDF_GATEWAY_DATABASE_PASSWORD=$(openssl rand -hex 16)
PDF_GATEWAY_RABBITMQ_PASSWORD=$(openssl rand -hex 16)
PDF_GATEWAY_BOOTSTRAP_ADMIN_TOKEN=$admin
PDF_GATEWAY_PAPERLESS_TOKEN=
EOF

# The Makefile's pdf-gateway setup lines, run as root in a container (no sudo).
awk '/^setup-pdf-gateway:/{on=1; next} on && /^$/{exit} on' "$root/Makefile" \
  | sed -e 's/^\t//' -e '/^#/d' -e '/^@echo/d' -e 's/^sudo //' | sed -e ':a' -e '/\\$/N; s/\\\n//; ta' \
  | sed -e "s#\$(VOLUMES_ROOT)#$scratch/volumes#g" -e "s#\$(DATA_ROOT)#$scratch/data#g" \
        -e "s#\$(PUID)#$(id -u)#g" -e "s#\$(PGID)#$(id -g)#g" >"$scratch/setup.sh"
grep -q "pdf-archive" "$scratch/setup.sh"
docker run --rm -u 0 -v "$scratch:$scratch" "$IMAGE" sh -eu "$scratch/setup.sh"
eq "$(stat -c '%u:%g %a' "$scratch/volumes/pdf-gateway/storage/cdr-output")" "10004:10010 750"
eq "$(stat -c '%u:%g %a' "$scratch/data/documents/pdf-archive")" "10005:10005 755"

if ! docker network inspect home_server >/dev/null 2>&1; then
  docker network create home_server >/dev/null; created_network=1
fi
"${dc[@]}" config --quiet
"${dc[@]}" build -q
"${dc[@]}" up -d --no-build --wait --wait-timeout 600

# The homelab Caddy, with only the pdf site and the snippet it imports.
mkdir -p "$scratch/caddy"
cp "$root/compose/caddy/conf/pdf.caddy" "$scratch/caddy/"
sed -n '1,/^import \*\.caddy/p' "$root/compose/caddy/conf/Caddyfile" >"$scratch/caddy/Caddyfile"
docker run -d --name "$caddy" --network home_server -p 127.0.0.1:18480:80 -p 127.0.0.1:18443:443 \
  -v "$scratch/caddy:/etc/caddy:ro" "$IMAGE" >/dev/null
resolve=(--resolve pdf.homelab.lan:18480:127.0.0.1 --resolve pdf.homelab.lan:18443:127.0.0.1)
for _ in $(seq 30); do
  curl -fsS "${resolve[@]}" -o "$scratch/ca.crt" http://pdf.homelab.lan:18480/ca.crt 2>/dev/null && break; sleep 1
done
openssl x509 -in "$scratch/ca.crt" -noout -subject | grep -q "Caddy Local Authority"
eq "$(curl -sS "${resolve[@]}" -o /dev/null -w '%{http_code} %{redirect_url}' http://pdf.homelab.lan:18480/scans)" \
   "308 https://pdf.homelab.lan/scans"

base=https://pdf.homelab.lan:18443
tls=(--cacert "$scratch/ca.crt" "${resolve[@]}")
# Without the CA the certificate is not trusted (it is Caddy's own, not public).
! curl -sS "${resolve[@]}" -o /dev/null "$base/" 2>/dev/null
eq "$(curl -sS "${tls[@]}" -o /dev/null -w '%{http_code}' "$base/")" 200

# Browser sign-in: Secure, HttpOnly, host-only cookie, then a CSRF-checked upload
# whose Origin is the https site (forwarded scheme kept through both proxies).
eq "$(curl -sS "${tls[@]}" -c "$scratch/jar" -D "$scratch/login.h" -o "$scratch/session.json" -w '%{http_code}' \
      -H 'Content-Type: application/json' -H "Origin: $base" -d "{\"token\":\"$admin\"}" "$base/api/v1/session")" 201
grep -i '^set-cookie: __Host-pgw_session=' "$scratch/login.h" | grep -qi 'secure'
grep -i '^set-cookie: __Host-pgw_session=' "$scratch/login.h" | grep -qi 'httponly'
csrf="$(json 'v["csrfToken"]' <"$scratch/session.json")"
python3 "$root/compose/pdf-gateway/app/tests/make_fixtures.py" "$scratch/fx" >/dev/null
upload() { curl -sS "${tls[@]}" -b "$scratch/jar" -H "X-CSRF-Token: $csrf" -H "Idempotency-Key: m8-upload-$(openssl rand -hex 8)" "$@" \
             -F intent=ARCHIVE -F cdrMode=RASTER -F "file=@$scratch/fx/invoice-en.pdf;type=application/pdf" "$base/api/v1/scans"; }
eq "$(upload -o /dev/null -w '%{http_code}' -H 'Origin: https://evil.example')" 403
eq "$(upload -o "$scratch/accepted.json" -w '%{http_code}' -H "Origin: $base")" 202
scan="$(json 'v["scanId"]' <"$scratch/accepted.json")"

# Each stage writes as its own uid into its own folder.
for _ in $(seq 120); do
  status="$(curl -sS "${tls[@]}" -b "$scratch/jar" "$base/api/v1/scans/$scan" | json 'v["status"]')"
  [[ $status == SANITIZING || $status == BLOCKED || $status == FAILED || $status == REVIEW_REQUIRED ]] && break; sleep 2
done
echo "status without a CDR provider: $status"
q() { docker exec pdf-gateway-db psql -qtA -U pdf_gateway -d pdf_gateway -c "$1"; }
# Report folders are 0700 to their stage: look as root.
owners() { docker run --rm -u 0 -v "$scratch:/s:ro" "$IMAGE" sh -c "find /s/volumes/pdf-gateway/storage/$1 -type f -exec stat -c %u {} + | sort -u"; }
eq "$(owners quarantine)" 10001
eq "$(owners reports)" 10002
eq "$(owners signature-reports)" 10003
# No provider: nothing is released or archived.
sleep 20
eq "$(q "SELECT count(*) FROM archive_documents")" 0
[[ $(curl -sS "${tls[@]}" -b "$scratch/jar" "$base/api/v1/scans/$scan" | json 'v["status"]') != READY ]]
eq "$(docker run --rm -u 0 -v "$scratch:/s:ro" "$IMAGE" find /s/data/documents/pdf-archive -name '*.pdf' | wc -l)" 0

# Homepage: a READ_ONLY token, through the web container on home_server.
ro="$(openssl rand -hex 32)"
q "INSERT INTO users(id,username,role) VALUES (gen_random_uuid(),'homepage','READ_ONLY')" >/dev/null
q "INSERT INTO api_tokens(id,user_id,token_hash,label) SELECT gen_random_uuid(),id,'$(printf %s "$ro" | sha256sum | cut -d' ' -f1)','homepage' FROM users WHERE username='homepage'" >/dev/null
card="$(docker run --rm --network home_server "$IMAGE" wget -qO- --header "Authorization: Bearer $ro" http://pdf-gateway-web:8080/api/v1/system/status)"
eq "$(printf %s "$card" | json 'str(v["archived"])+"|"+str(v["uploads24h"])')" "0|1"
eq "$(docker run --rm --network home_server "$IMAGE" sh -c 'wget -qO- http://pdf-gateway-web:8080/api/v1/system/status >/dev/null 2>&1; echo $?')" 1
# The read-only token cannot upload.
eq "$(curl -sS "${tls[@]}" -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $ro" -H 'Idempotency-Key: m8-read-only-upload-0001' \
      -F intent=ARCHIVE -F "file=@$scratch/fx/invoice-en.pdf;type=application/pdf" "$base/api/v1/scans")" 403

# Without a Paperless token the adapter takes nothing (copies wait in the queue).
docker logs pdf-gateway-paperless 2>&1 | grep -q '"paperless_not_configured"'

# Database and broker are unreachable from home_server (internal network).
eq "$(docker run --rm --network home_server "$IMAGE" sh -c 'nc -z -w 3 pdf-gateway-db 5432 2>/dev/null; echo $?')" 1

echo "M8 stack: Makefile folders, HTTPS via Caddy's local CA, Secure session, CSRF/Origin upload, per-stage uids, fail-closed without CDR, idle Paperless adapter without a token, Homepage READ_ONLY card passed"
