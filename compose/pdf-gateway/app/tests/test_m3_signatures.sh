#!/usr/bin/env bash
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
compose="$root/compose/pdf-gateway/app/tests/docker-compose.m3-signatures.yml"
scratch="$(mktemp -d)"
export TEST_FIXTURES="$scratch/fixtures"
mkdir -m 0777 "$TEST_FIXTURES"

cleanup() {
  status=$?
  docker compose -f "$compose" down --volumes --remove-orphans >/dev/null 2>&1 || true
  chmod -R u+rwX "$scratch" 2>/dev/null || true
  rm -rf -- "$scratch"
  exit "$status"
}
trap cleanup EXIT

python3 - "$TEST_FIXTURES/unsigned.pdf" "$TEST_FIXTURES/visible-only.pdf" <<'PY'
import pathlib,sys
def pdf(path, text):
    body=f"BT /F1 18 Tf 30 110 Td ({text}) Tj ET".encode()
    objects=[b"<< /Type /Catalog /Pages 2 0 R >>",b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 150] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",f"<< /Length {len(body)} >>\nstream\n".encode()+body+b"\nendstream",b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    data=bytearray(b"%PDF-1.4\n"); offsets=[]
    for n,obj in enumerate(objects,1): offsets.append(len(data)); data.extend(f"{n} 0 obj\n".encode()+obj+b"\nendobj\n")
    xref=len(data); data.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets:data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    pathlib.Path(path).write_bytes(data)
pdf(sys.argv[1], "M3 signature fixture")
pdf(sys.argv[2], "Handwritten signature appearance: Alice Example")
PY
printf 'not a PDF\n' >"$TEST_FIXTURES/malformed.pdf"

openssl req -x509 -newkey rsa:2048 -nodes -days 2 -sha256 \
  -subj '/CN=M3 Test Root' -keyout "$TEST_FIXTURES/root.key" -out "$TEST_FIXTURES/root.crt" \
  -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign' >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes -sha256 -subj '/CN=M3 Test Signer' \
  -keyout "$TEST_FIXTURES/signer.key" -out "$TEST_FIXTURES/signer.csr" >/dev/null 2>&1
printf '%s\n' 'basicConstraints=critical,CA:FALSE' 'keyUsage=critical,digitalSignature,nonRepudiation' >"$TEST_FIXTURES/signer.ext"
openssl x509 -req -in "$TEST_FIXTURES/signer.csr" -CA "$TEST_FIXTURES/root.crt" -CAkey "$TEST_FIXTURES/root.key" \
  -CAcreateserial -days 2 -sha256 -extfile "$TEST_FIXTURES/signer.ext" -out "$TEST_FIXTURES/signer.crt" >/dev/null 2>&1
chmod 0644 "$TEST_FIXTURES"/*

docker compose -f "$compose" build inspector
docker compose -f "$compose" run --rm --no-deps signer

python3 - "$TEST_FIXTURES/signed.pdf" "$TEST_FIXTURES/tampered.pdf" <<'PY'
import pathlib,sys
data=pathlib.Path(sys.argv[1]).read_bytes()
old=b"M3 signature fixture"; new=b"M3 signatvre fixture"
assert old in data and len(old)==len(new)
pathlib.Path(sys.argv[2]).write_bytes(data.replace(old,new,1))
PY
chmod 0644 "$TEST_FIXTURES/tampered.pdf"

run() {
  local input=$1 output=$2; shift 2
  docker compose -f "$compose" run --rm --no-deps inspector "$@" "/fixtures/$input" >"$output"
}
run unsigned.pdf "$scratch/unsigned.json"
run visible-only.pdf "$scratch/visible.json"
run signed.pdf "$scratch/trusted.json" --trust-root /fixtures/root.crt
run signed.pdf "$scratch/untrusted.json"
run tampered.pdf "$scratch/tampered.json"
set +e
run malformed.pdf "$scratch/malformed.json"
malformed_status=$?
set -e
[[ $malformed_status -eq 2 ]]

python3 - "$scratch/unsigned.json" "$scratch/visible.json" "$scratch/trusted.json" "$scratch/untrusted.json" "$scratch/tampered.json" "$scratch/malformed.json" <<'PY'
import json,sys
unsigned,visible,trusted,untrusted,tampered,malformed=[json.load(open(p)) for p in sys.argv[1:]]
assert unsigned["state"]=="UNSIGNED" and unsigned["signatureCount"]==0,unsigned
assert visible["state"]=="UNSIGNED" and visible["signatureCount"]==0,visible
assert trusted["state"]=="SIGNED_VALID_TRUSTED" and trusted["preserveOriginal"],trusted
assert trusted["signatures"][0]["integrity"]=="INTACT",trusted
assert trusted["signatures"][0]["trust"]=="TRUSTED",trusted
assert untrusted["state"]=="SIGNED_VALID_UNTRUSTED" and untrusted["preserveOriginal"],untrusted
assert tampered["state"] in {"SIGNED_INVALID","UNKNOWN"},tampered
assert tampered["policyDecision"]=="REVIEW_REQUIRED",tampered
assert malformed["state"]=="UNKNOWN" and malformed["policyDecision"]=="REVIEW_REQUIRED",malformed
assert all(r["engine"]["networkFetching"] is False for r in (unsigned,visible,trusted,untrusted,tampered)),trusted
PY

before="$(sha256sum "$TEST_FIXTURES/signed.pdf" | cut -d' ' -f1)"
run signed.pdf "$scratch/repeat.json" --trust-root /fixtures/root.crt
after="$(sha256sum "$TEST_FIXTURES/signed.pdf" | cut -d' ' -f1)"
[[ $before == "$after" ]]
echo "M3 signature integrity, trust separation, visible-mark distinction, tamper detection, and immutability tests passed"
