#!/usr/bin/env bash
# Run one Lab2-only test without heating Lab2: build every image on this machine,
# stream them to Lab2 with docker save | docker load, sync the worktree, then run
# the test there with PDF_GATEWAY_PREBUILT=1 while sampling the package temperature.
#   pdf-gateway/tools/lab2_test.sh test_pipeline_e2e    (or test_m3_cdr, test_m3_validation)
set -Eeuo pipefail
test_name=${1:?usage: lab2_test.sh TEST_NAME}
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
remote=${LAB2_HOST:-lab2}
remote_dir=${LAB2_DIR:-/home/potato/pdf-gateway-test-work}

declare -A contexts=([api]=api [analysis-worker]=analysis-worker [signature-worker]=signature-worker [cdr-worker]=cdr-worker)
tags=()
for service in "${!contexts[@]}"; do
  docker build -q -t "pdf-gateway-e2e-$service" -t "pdf-gateway-m3-test/$service:local" "$root/pdf-gateway/${contexts[$service]}" >/dev/null
  tags+=("pdf-gateway-e2e-$service" "pdf-gateway-m3-test/$service:local")
done
docker build -q -f "$root/pdf-gateway/archive-worker/Dockerfile" -t pdf-gateway-e2e-archive-worker "$root/pdf-gateway" >/dev/null
tags+=(pdf-gateway-e2e-archive-worker)
echo "built ${#tags[@]} tags locally; loading on $remote"
docker save "${tags[@]}" | gzip -1 | ssh "$remote" 'gunzip | docker load -q' >/dev/null
rsync -a --delete --exclude .git --exclude '__pycache__' --exclude target "$root/" "$remote:$remote_dir/"
# Optional private real documents: copied to a 0700 directory, removed afterwards.
sample_env=""
if [[ -n ${REAL_SAMPLE_DIR:-} ]]; then
  ssh "$remote" "install -d -m 0700 '$remote_dir/.real-sample'"
  scp -q "$REAL_SAMPLE_DIR"/*.pdf "$remote:$remote_dir/.real-sample/"
  sample_env="REAL_SAMPLE_DIR='$remote_dir/.real-sample'"
fi
ssh "$remote" "cd '$remote_dir' || exit 1
  ( while true; do for z in /sys/class/thermal/thermal_zone*; do [ \"\$(cat \$z/type)\" = x86_pkg_temp ] && echo \"\$(date +%T) \$(( \$(cat \$z/temp) / 1000 ))\"; done; sleep 1; done ) > /tmp/$test_name.temps 2>&1 &
  sampler=\$!
  THERMAL_ABORT_AT=${THERMAL_ABORT_AT:-85} $sample_env PDF_GATEWAY_PREBUILT=1 pdf-gateway/tests/$test_name.sh > /tmp/$test_name.log 2>&1; status=\$?
  kill \$sampler
  grep -E 'thermal guard|aborted|failed at line|expected \\[|passed|^real[0-9]|stuck' /tmp/$test_name.log
  rm -f /tmp/$test_name.log
  echo \"peak package temperature: \$(sort -k2 -n /tmp/$test_name.temps | tail -1) (\$(wc -l < /tmp/$test_name.temps) samples at 1 s)\"
  rm -rf '$remote_dir'
  exit \$status"
