#!/usr/bin/env bash
# shellcheck disable=SC2059,SC2016,SC2029  # colour codes in formats; remote commands are quoted on purpose
# Copies the HDD_500_11 family archive from this laptop into lab1's photo and
# file trees. Safe to interrupt and re-run: finished files are skipped, partial
# ones resume. Nothing is ever deleted on either side.
#
#   photos/scripts/import-hdd-500-11.sh              plan, confirm, copy, verify
#   photos/scripts/import-hdd-500-11.sh --dry-run    plan only
#   photos/scripts/import-hdd-500-11.sh --verify-only
#   photos/scripts/import-hdd-500-11.sh --yes --skip-verify

set -Eeuo pipefail

readonly SOURCE="${SOURCE:-/media/amine/HDD_500_11}"
readonly REMOTE="${REMOTE:-my-homelab}"
readonly DATA="${DATA:-/mnt/storage/data}"
readonly STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/batlab-import/hdd-500-11"
readonly HOT_C="${HOT_C:-88}"
readonly COOL_C="${COOL_C:-80}"

# name | source (relative to SOURCE) | destination (relative to DATA) | extra rsync filters
readonly JOBS=(
  "library|MEMORIES/|photos/library/|--exclude=/results.txt --exclude=/old-structure.txt"
  "private|OTHER6DRIVE/OTHER_DATA/TO_FORGET/|photos/private/to-forget/|"
  "sisters-docs|OTHER6DRIVE/OTHER_DATA/SISTERS_DOCS/|files/family/sisters-docs/|"
  "notes|./|files/archives/hdd-500-11/|--include=/OPTIMIZATIONS/*** --include=/USEFUL_TRICKS/*** --include=/UPLOAD/ --include=/results.txt --include=/MEMORIES/ --include=/MEMORIES/results.txt --include=/MEMORIES/old-structure.txt --exclude=*"
  "recycle-bin|./|files/archives/hdd-500-11/recycle-bin/|--include=/\$RECYCLE.BIN/ --include=/\$RECYCLE.BIN/\$R* --include=/.Trashes/ --include=/.Trashes/501/ --include=/.Trashes/501/[!.]* --exclude=*"
)

# Caches and filesystem metadata that other machines regenerate or ignore.
readonly COMMON_FILTERS=("--exclude=._*" "--exclude=.DS_Store" "--exclude=Thumbs.db" "--exclude=desktop.ini"
  "--exclude=/.Spotlight-V100" "--exclude=/System Volume Information")

if [[ -t 1 ]]; then
  B=$'\e[1m' D=$'\e[2m' R=$'\e[31m' G=$'\e[32m' Y=$'\e[33m' C=$'\e[36m' N=$'\e[0m'
else
  B="" D="" R="" G="" Y="" C="" N=""
fi

ok() { printf "  ${G}✔${N} %s\n" "$*"; }
fail() { printf "  ${R}✘${N} %s\n" "$*" >&2; exit 1; }
note() { printf "  ${D}%s${N}\n" "$*"; }
title() { printf "\n${B}${C}━━ %s ${N}${D}%s${N}\n" "$1" "${2:-}"; }
human() { numfmt --to=iec --suffix=B --format="%.1f" "$1"; }

DRY_RUN=0 VERIFY_ONLY=0 SKIP_VERIFY=0 ASSUME_YES=0
for argument in "$@"; do
  case "$argument" in
    --dry-run) DRY_RUN=1 ;;
    --verify-only) VERIFY_ONLY=1 ;;
    --skip-verify) SKIP_VERIFY=1 ;;
    -y | --yes) ASSUME_YES=1 ;;
    -h | --help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fail "unknown option: $argument" ;;
  esac
done

install -d -m 700 "$STATE_DIR"
RUN_ID="$(date +%Y%m%d-%H%M%S)"
readonly RUN_ID
readonly LOG="$STATE_DIR/$RUN_ID.log"
# Unix sockets cap paths at 108 bytes, so the shared connection lives in the runtime dir.
readonly CONTROL="${XDG_RUNTIME_DIR:-/tmp}/batlab-import-%C"
readonly SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$CONTROL" -o ControlPersist=15m
  -o Compression=no -o ServerAliveInterval=15 -c aes128-gcm@openssh.com -x)
readonly RSYNC_SSH="ssh ${SSH_OPTS[*]}"

remote() { ssh "${SSH_OPTS[@]}" "$REMOTE" "$@"; }

rsync_base() {
  local job_filters="$1"
  local extra=()
  # Split on spaces without letting bash expand the rsync patterns as globs.
  set -f
  # shellcheck disable=SC2206
  extra=($job_filters)
  set +f
  # shellcheck disable=SC2054  # --chmod takes a comma-separated list
  RSYNC_ARGS=(
    --recursive --times --modify-window=2 --protect-args --mkpath
    --chmod=D755,F644 --partial-dir=.rsync-partial
    --checksum-choice=xxh128 --rsync-path="ionice -c2 -n7 rsync"
    -e "$RSYNC_SSH" "${COMMON_FILTERS[@]}" "${extra[@]}"
  )
}

cleanup() {
  if [[ -n "${GUARD_PID:-}" ]]; then kill "$GUARD_PID" 2>/dev/null || true; fi
  ssh "${SSH_OPTS[@]}" -O exit "$REMOTE" 2>/dev/null || true
}
trap cleanup EXIT
trap 'printf "\n${Y}Interrupted.${N} Re-run the same command to resume; finished files are kept.\n"; exit 130' INT TERM

remote_temperature() {
  remote 'for h in /sys/class/hwmon/hwmon*; do [ "$(cat $h/name 2>/dev/null)" = k10temp ] && { echo $(( $(cat $h/temp1_input) / 1000 )); exit; }; done; echo 0'
}

# Pauses the running rsync with SIGSTOP when the server runs hot and resumes it
# once it cools. lab1 has powered off from sustained heat before.
thermal_guard() {
  local rsync_pid="$1" paused=0 temp
  while kill -0 "$rsync_pid" 2>/dev/null; do
    temp="$(remote_temperature 2>/dev/null || echo 0)"
    if ((paused == 0 && temp >= HOT_C)); then
      kill -STOP "$rsync_pid" 2>/dev/null || true
      paused=1
      printf "\n  ${Y}⏸  server CPU at %s °C, pausing until %s °C${N}\n" "$temp" "$COOL_C"
      echo "$(date -Is) paused at ${temp}C" >>"$LOG"
    elif ((paused == 1 && temp <= COOL_C)); then
      kill -CONT "$rsync_pid" 2>/dev/null || true
      paused=0
      printf "\n  ${G}▶  server CPU at %s °C, resuming${N}\n" "$temp"
      echo "$(date -Is) resumed at ${temp}C" >>"$LOG"
    fi
    sleep 20
  done
}

printf "${B}Import HDD_500_11 → %s:%s${N}\n" "$REMOTE" "$DATA"
note "log: $LOG"

title "1. Preflight"
[[ -d "$SOURCE/MEMORIES" ]] || fail "$SOURCE is not mounted or has no MEMORIES folder"
ok "source mounted: $SOURCE ($(findmnt -no FSTYPE --target "$SOURCE"))"
rsync_new_enough() { [[ "$1" =~ version\ 3\.([2-9]|[1-9][0-9]) ]]; }
rsync_new_enough "$(rsync --version)" || fail "local rsync is older than 3.2"
remote true 2>/dev/null || fail "cannot reach $REMOTE over SSH without a password"
ok "SSH connection to $REMOTE (shared, aes128-gcm, no compression)"
rsync_new_enough "$(remote 'rsync --version')" || fail "server rsync is older than 3.2"
ok "rsync ≥ 3.2 on both sides (xxh128 checksums)"
remote "mountpoint -q '$DATA'" || fail "$DATA is not mounted on $REMOTE; is the data disk connected?"
ok "$DATA is mounted on the server"
remote "test -w '$DATA/photos/library' && test -w '$DATA/files'" || fail "photos/library or files is not writable on $REMOTE"
ok "destination folders writable"
free_bytes="$(remote "df -B1 --output=avail '$DATA' | tail -1 | tr -d ' '")"
ok "free space on the data disk: $(human "$free_bytes")"
ok "server CPU temperature: $(remote_temperature) °C (pause at $HOT_C, resume at $COOL_C)"

title "2. Plan" "(what still has to be copied)"
total_bytes=0 total_files=0
printf "  ${B}%-13s %-40s %-32s %9s %9s${N}\n" STEP FROM TO FILES SIZE
for job in "${JOBS[@]}"; do
  IFS='|' read -r name src dest filters <<<"$job"
  rsync_base "$filters"
  stats="$(rsync "${RSYNC_ARGS[@]}" --dry-run --stats "$SOURCE/$src" "$REMOTE:$DATA/$dest" 2>>"$LOG")"
  files="$(awk -F': ' '/Number of regular files transferred/ {gsub(/,/,"",$2); print $2}' <<<"$stats")"
  bytes="$(awk -F': ' '/Total transferred file size/ {gsub(/[, a-z]/,"",$2); print $2}' <<<"$stats")"
  total_files=$((total_files + ${files:-0}))
  total_bytes=$((total_bytes + ${bytes:-0}))
  printf "  %-13s %-40s %-32s %9s %9s\n" "$name" "${src:0:40}" "${dest:0:32}" "${files:-0}" "$(human "${bytes:-0}")"
done
printf "  ${B}%-13s %-40s %-32s %9s %9s${N}\n" TOTAL "" "" "$total_files" "$(human "$total_bytes")"
((total_bytes < free_bytes)) || fail "not enough free space on the server"
if ((total_bytes > 0)); then
  note "at the data disk's ~30 MB/s this takes about $((total_bytes / 30000000 / 60)) min"
fi

((DRY_RUN)) && { printf "\n${G}Dry run only, nothing copied.${N}\n"; exit 0; }

if ((VERIFY_ONLY == 0 && total_bytes > 0)); then
  if ((ASSUME_YES == 0)); then
    printf "\n  Copy %s in %s files? Nothing is deleted on either side. [y/N] " "$(human "$total_bytes")" "$total_files"
    read -r answer
    [[ "$answer" =~ ^[yY]$ ]] || { echo "  Aborted."; exit 1; }
  fi

  title "3. Copy" "(each file is checksummed as it lands)"
  started=$SECONDS
  step=0
  for job in "${JOBS[@]}"; do
    step=$((step + 1))
    IFS='|' read -r name src dest filters <<<"$job"
    printf "\n  ${B}[%d/%d] %s${N}  ${D}%s → %s${N}\n" "$step" "${#JOBS[@]}" "$name" "$src" "$dest"
    rsync_base "$filters"
    rsync "${RSYNC_ARGS[@]}" --no-inc-recursive --info=progress2,stats1 --log-file="$LOG" \
      "$SOURCE/$src" "$REMOTE:$DATA/$dest" &
    rsync_pid=$!
    thermal_guard "$rsync_pid" &
    GUARD_PID=$!
    if ! wait "$rsync_pid"; then
      kill "$GUARD_PID" 2>/dev/null || true
      fail "step $name failed; see $LOG, then re-run to resume"
    fi
    kill "$GUARD_PID" 2>/dev/null || true
    GUARD_PID=""
  done
  elapsed=$((SECONDS - started))
  ok "copied $(human "$total_bytes") in $((elapsed / 60)) min $((elapsed % 60)) s"
elif ((VERIFY_ONLY == 0)); then
  title "3. Copy"
  ok "nothing left to copy"
fi

((SKIP_VERIFY)) && { printf "\n${Y}Verification skipped.${N} Run with --verify-only later.\n"; exit 0; }

title "4. Verify" "(full xxh128 comparison of every file on both sides)"
note "reads everything again on both disks; expect roughly as long as the copy"
mismatch_file="$STATE_DIR/$RUN_ID.mismatches"
: >"$mismatch_file"
step=0
for job in "${JOBS[@]}"; do
  step=$((step + 1))
  IFS='|' read -r name src dest filters <<<"$job"
  rsync_base "$filters"
  begun=$SECONDS
  rsync "${RSYNC_ARGS[@]}" --checksum --dry-run --out-format='%n' "$SOURCE/$src" "$REMOTE:$DATA/$dest" \
    2>>"$LOG" | grep -v '/$' >"$STATE_DIR/verify-$name" &
  verify_pid=$!
  thermal_guard "$verify_pid" &
  GUARD_PID=$!
  spin='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
  i=0
  while kill -0 "$verify_pid" 2>/dev/null; do
    printf "\r  ${C}%s${N} [%d/%d] %-13s %4d s" "${spin:i++%${#spin}:1}" "$step" "${#JOBS[@]}" "$name" $((SECONDS - begun))
    sleep 0.2
  done
  wait "$verify_pid" || true
  kill "$GUARD_PID" 2>/dev/null || true
  GUARD_PID=""
  count="$(wc -l <"$STATE_DIR/verify-$name")"
  if ((count == 0)); then
    printf "\r  ${G}✔${N} [%d/%d] %-13s identical on both sides (%d s)\n" "$step" "${#JOBS[@]}" "$name" $((SECONDS - begun))
  else
    printf "\r  ${R}✘${N} [%d/%d] %-13s %d file(s) differ\n" "$step" "${#JOBS[@]}" "$name" "$count"
    sed "s#^#$name: #" "$STATE_DIR/verify-$name" >>"$mismatch_file"
  fi
  rm -f "$STATE_DIR/verify-$name"
done

if [[ -s "$mismatch_file" ]]; then
  printf "\n${R}${B}Verification failed${N} for %s file(s), listed in %s\n" "$(wc -l <"$mismatch_file")" "$mismatch_file"
  printf "Re-run without --verify-only to recopy them, then verify again.\n"
  exit 2
fi
rm -f "$mismatch_file"
printf "\n${G}${B}All files verified.${N} The HDD is still untouched; keep it until offsite backup exists.\n"
