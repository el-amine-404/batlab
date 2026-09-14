#!/usr/bin/env bash
# shellcheck disable=SC2059,SC2016,SC2029  # colour codes live in printf formats; remote snippets are quoted with %q on purpose
# Copies folders from a drive mounted on this machine into the server's photo and
# file trees, as described by a drive file (see photos/conf/import-drive.example.conf).
# Safe to interrupt and re-run: finished files are skipped, partial ones resume.
# Nothing is ever deleted on either side.
#
#   import-drive.sh <drive> [options]     plan, confirm, copy, verify
#
#   <drive>                  a drive file, or a name in ~/.config/batlab-import/<name>.conf
#   --dry-run                plan only
#   --verify-only            compare both sides without copying
#   --skip-verify            copy without the final comparison
#   -y, --yes                copy without asking
#   --source PATH            mount point, overriding the drive file
#   --remote HOST            SSH host alias of the server (default my-homelab)
#   --data PATH              data root on the server (default /mnt/storage/data)
#   --max-cpu-temp C         pause while the server CPU is at or above C
#   --cpu-temp-sensor NAME   hwmon sensor to read, required with --max-cpu-temp
#
# Verify before organize-media.py renames anything: after a rename the two sides
# are meant to differ. The end of every copy or verification is posted to Discord
# through DISCORD_WEBHOOK_ALERTS in compose/.env.

set -Eeuo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly REPO_DIR
readonly CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/batlab-import"

# Caches and filesystem metadata that other machines regenerate or ignore.
readonly COMMON_FILTERS=("--exclude=._*" "--exclude=.DS_Store" "--exclude=Thumbs.db" "--exclude=desktop.ini"
  "--exclude=/.Spotlight-V100" "--exclude=/System Volume Information")

# Content that legitimately exists on the server without a source counterpart in
# trees organize-media.py works on: folders filed there before the import, its
# state folder and its sidecars. Every other destination must match exactly.
readonly ORGANIZED_TREES="^photos/(library|inbox)/"
readonly ORGANIZED_PROTECT=("--filter=P /_to-merge/" "--filter=P /.organize/" "--filter=P *.xmp")

# Only regular files and folders are imported. Dry runs list symlinks, devices and
# special files so they are reported instead of being skipped silently.
readonly LIST_NON_REGULAR=(--links --devices --specials)

# Turns NUL-separated remote paths into lines, escaping tabs and newlines the way
# rsync does, so a path never spans or splits a report row.
readonly ESCAPE_LINES='sed -z '\''s/\t/\\#011/g; s/\n/\\#012/g'\'' | tr "\0" "\n"'

if [[ -t 1 ]]; then
  B=$'\e[1m' D=$'\e[2m' R=$'\e[31m' G=$'\e[32m' Y=$'\e[33m' C=$'\e[36m' N=$'\e[0m'
else
  B="" D="" R="" G="" Y="" C="" N=""
fi

ok() { printf "  ${G}✔${N} %s\n" "$*"; }
fail() { FAILURE="$*"; printf "  ${R}✘${N} %s\n" "$*" >&2; exit 1; }
note() { printf "  ${D}%s${N}\n" "$*"; }
title() { printf "\n${B}${C}━━ %s ${N}${D}%s${N}\n" "$1" "${2:-}"; }
human() { numfmt --to=iec --suffix=B --format="%.1f" "$1"; }

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  printf '%s' "${value%"${value##*[![:space:]]}"}"
}

DRY_RUN=0 VERIFY_ONLY=0 SKIP_VERIFY=0 ASSUME_YES=0
FAILURE="" NOTIFY=0 OUTCOME="" STARTED=$SECONDS LOG="-"
CONF="" SOURCE="" REMOTE="my-homelab" DATA="/mnt/storage/data" MAX_TEMP="" SENSOR=""
while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --verify-only) VERIFY_ONLY=1 ;;
    --skip-verify) SKIP_VERIFY=1 ;;
    -y | --yes) ASSUME_YES=1 ;;
    --source | --remote | --data | --max-cpu-temp | --cpu-temp-sensor)
      (($# > 1)) || fail "$1 needs a value"
      case "$1" in
        --source) SOURCE="$2" ;;
        --remote) REMOTE="$2" ;;
        --data) DATA="$2" ;;
        --max-cpu-temp) MAX_TEMP="$2" ;;
        --cpu-temp-sensor) SENSOR="$2" ;;
      esac
      shift
      ;;
    -h | --help) sed -n '3,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) fail "unknown option: $1" ;;
    *) [[ -z "$CONF" ]] || fail "only one drive at a time: $1"; CONF="$1" ;;
  esac
  shift
done
[[ -n "$CONF" ]] || fail "which drive? usage: import-drive.sh <drive> [options], see --help"
((VERIFY_ONLY && SKIP_VERIFY)) && fail "--verify-only and --skip-verify contradict each other"
[[ "$CONF" == */* || "$CONF" == *.conf ]] || CONF="$CONF_DIR/$CONF.conf"
[[ -r "$CONF" ]] || fail "cannot read drive file $CONF"
DRIVE="$(basename -- "$CONF" .conf)"

# Values reach remote shells and rsync arguments, so only plain names and paths are accepted.
[[ "$DRIVE" =~ ^[A-Za-z0-9._-]+$ ]] || fail "drive file name must be letters, digits, . _ and -: $DRIVE"
[[ "$REMOTE" =~ ^[A-Za-z0-9._@-]+$ ]] || fail "--remote must be a plain SSH host alias: $REMOTE"
[[ "$DATA" =~ ^/[A-Za-z0-9._/-]+$ ]] || fail "--data must be an absolute path of letters, digits, . _ - and /: $DATA"
if [[ -n "$MAX_TEMP" || -n "$SENSOR" ]]; then
  [[ "$MAX_TEMP" =~ ^[0-9]{2,3}$ ]] || fail "--max-cpu-temp must be whole degrees Celsius: $MAX_TEMP"
  [[ "$SENSOR" =~ ^[A-Za-z0-9_-]+$ ]] || fail "--cpu-temp-sensor names the hwmon sensor, such as k10temp or coretemp"
fi

JOBS=() CONF_SOURCE="" SOURCE_UUID=""
line_number=0
while IFS= read -r line || [[ -n "$line" ]]; do
  line_number=$((line_number + 1))
  [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
  read -r key rest <<<"$line"
  where="$CONF line $line_number"
  case "$key" in
    source) CONF_SOURCE="$rest" ;;
    uuid) SOURCE_UUID="$rest" ;;
    step)
      IFS='|' read -r name from to filters <<<"$rest"
      name="$(trim "$name")" from="$(trim "${from:-}")" to="$(trim "${to:-}")" filters="$(trim "${filters:-}")"
      [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]] || fail "$where: step name must be lowercase letters, digits and -"
      [[ "$from" == */ && "$from" != /* && "/$from" != */../* ]] || fail "$where: source folder must be relative to the drive and end in /"
      [[ "$to" =~ ^[A-Za-z0-9._-][A-Za-z0-9._/-]*/$ && "/$to" != */../* ]] || fail "$where: destination must be relative to the data root, plain characters, ending in /"
      for job in "${JOBS[@]}"; do
        [[ "${job%%|*}" != "$name" ]] || fail "$where: step $name is defined twice"
      done
      JOBS+=("$name|$from|$to|$filters")
      ;;
    *) fail "$where: unknown key '$key'; expected source, uuid or step" ;;
  esac
done <"$CONF"
SOURCE="${SOURCE:-$CONF_SOURCE}"
[[ -n "$SOURCE" ]] || fail "$CONF has no source line"
[[ -n "$SOURCE_UUID" ]] || fail "$CONF has no uuid line; findmnt -no UUID --mountpoint $SOURCE prints it"
((${#JOBS[@]})) || fail "$CONF has no step lines"
readonly SOURCE SOURCE_UUID REMOTE DATA MAX_TEMP SENSOR DRIVE CONF JOBS
readonly STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/batlab-import/$DRIVE"

install -d -m 700 "$STATE_DIR"
exec 8>"$STATE_DIR/.lock"
flock -n 8 || fail "another import is already running (lock: $STATE_DIR/.lock)"

RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
readonly RUN_ID
LOG="$STATE_DIR/$RUN_ID.log"
readonly LOG
readonly WORK="$STATE_DIR/$RUN_ID"
install -d -m 700 "$WORK"
# Unix sockets cap paths at 108 bytes, and each run owns its connection.
readonly CONTROL="${XDG_RUNTIME_DIR:-/tmp}/batlab-import-$$-%C"
readonly SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$CONTROL" -o ControlPersist=15m
  -o Compression=no -o ServerAliveInterval=15 -o BatchMode=yes -c aes128-gcm@openssh.com -x)
readonly RSYNC_SSH="ssh ${SSH_OPTS[*]}"

remote() { ssh -n "${SSH_OPTS[@]}" "$REMOTE" "$@"; }
quoted() { printf '%q' "$1"; }

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

ACTIVE_PID="" GUARD_PID=""

stop_active() {
  if [[ -n "$GUARD_PID" ]]; then
    kill "$GUARD_PID" 2>/dev/null || true
    wait "$GUARD_PID" 2>/dev/null || true
    GUARD_PID=""
  fi
  if [[ -n "$ACTIVE_PID" ]] && kill -0 "$ACTIVE_PID" 2>/dev/null; then
    # A process paused by the thermal guard ignores TERM until it is resumed.
    kill -CONT "$ACTIVE_PID" 2>/dev/null || true
    kill -TERM "$ACTIVE_PID" 2>/dev/null || true
    wait "$ACTIVE_PID" 2>/dev/null || true
  fi
  ACTIVE_PID=""
}

notify_discord() {
  local status="$1" title color result
  ((NOTIFY)) || return 0
  local webhook="${IMPORT_DISCORD_WEBHOOK:-$(sed -n 's/^DISCORD_WEBHOOK_ALERTS=//p' "$REPO_DIR/compose/.env" 2>/dev/null | tail -n 1)}"
  [[ "$webhook" == http* ]] || return 0
  case "$status" in
    0) title="🟢 Import of $DRIVE finished" color=3066993 result="${OUTCOME:-done}" ;;
    2) title="🔴 Import of $DRIVE: verification failed" color=15158332 result="$OUTCOME" ;;
    130) title="🟠 Import of $DRIVE interrupted" color=15105570 result="re-run the same command to resume" ;;
    *) title="🔴 Import of $DRIVE failed" color=15158332 result="${FAILURE:-exit $status}" ;;
  esac
  local elapsed=$((SECONDS - STARTED)) payload
  payload="$(python3 -c '
import datetime, json, socket, sys
title, color, pairs = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
host = socket.gethostname()
fields = [{"name": "Host", "value": host, "inline": True}]
fields += [{"name": n, "value": (v or "-")[:1000], "inline": False} for n, v in zip(pairs[::2], pairs[1::2])]
print(json.dumps({
    "username": "import on " + host,
    "avatar_url": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/immich.png",
    "embeds": [{"title": title, "color": color, "fields": fields,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}],
}))' "$title" "$color" \
    "Destination" "$REMOTE:$DATA" \
    "Result" "$result" \
    "Duration" "$((elapsed / 3600)) h $((elapsed % 3600 / 60)) min" \
    "Log" "$LOG")" || return 0
  curl -sS -m 15 -H "Content-Type: application/json" -d "$payload" "$webhook" >/dev/null 2>&1 || true
}

cleanup() {
  local status=$?
  stop_active
  notify_discord "$status"
  ssh "${SSH_OPTS[@]}" -O exit "$REMOTE" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT
trap 'printf "\n${Y}Interrupted.${N} Re-run the same command to resume; finished files are kept.\n"; exit 130' INT TERM HUP

remote_temperature() {
  remote 'for h in /sys/class/hwmon/hwmon*; do [ "$(cat $h/name 2>/dev/null)" = '"$SENSOR"' ] && { echo $(( $(cat $h/temp1_input) / 1000 )); exit; }; done; echo 0'
}

# Pauses the running rsync with SIGSTOP while the server runs hot and resumes it
# once it is 8 degrees cooler.
thermal_guard() {
  local target_pid="$1" paused=0 temp
  trap 'kill -CONT "$target_pid" 2>/dev/null; exit 0' TERM
  while kill -0 "$target_pid" 2>/dev/null; do
    temp="$(remote_temperature 2>/dev/null || echo 0)"
    [[ "$temp" =~ ^[0-9]+$ ]] || temp=0
    if ((paused == 0 && temp >= MAX_TEMP)); then
      kill -STOP "$target_pid" 2>/dev/null || true
      paused=1
      printf "\n  ${Y}⏸  server CPU at %s °C, pausing until %s °C${N}\n" "$temp" "$((MAX_TEMP - 8))"
      echo "$(date -Is) paused at ${temp}C" >>"$LOG"
    elif ((paused == 1 && temp <= MAX_TEMP - 8)); then
      kill -CONT "$target_pid" 2>/dev/null || true
      paused=0
      printf "\n  ${G}▶  server CPU at %s °C, resuming${N}\n" "$temp"
      echo "$(date -Is) resumed at ${temp}C" >>"$LOG"
    fi
    # Backgrounded so the TERM trap runs at once instead of after the sleep.
    sleep 20 &
    wait $! || true
  done
}

# Runs one rsync in the background under the thermal guard and returns its exit
# status. With an output file, only rsync's stdout goes there; the spinner stays
# on the terminal so it can never be mistaken for rsync output.
run_guarded() {
  local spinner_label="$1" output="$2"
  shift 2
  if [[ -n "$output" ]]; then
    "$@" >"$output" 2>>"$LOG" &
  else
    "$@" &
  fi
  ACTIVE_PID=$!
  if [[ -n "$MAX_TEMP" ]]; then
    thermal_guard "$ACTIVE_PID" &
    GUARD_PID=$!
  fi
  if [[ -n "$spinner_label" ]]; then
    local spin='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' i=0 begun=$SECONDS
    while kill -0 "$ACTIVE_PID" 2>/dev/null; do
      printf "\r  ${C}%s${N} %s %4d s" "${spin:i++%${#spin}:1}" "$spinner_label" $((SECONDS - begun))
      sleep 0.2
    done
  fi
  local status=0
  wait "$ACTIVE_PID" || status=$?
  ACTIVE_PID=""
  if [[ -n "$GUARD_PID" ]]; then
    kill "$GUARD_PID" 2>/dev/null || true
    wait "$GUARD_PID" 2>/dev/null || true
    GUARD_PID=""
  fi
  return "$status"
}

has_xxh128() { grep -A2 -i '^checksum list' <<<"$1" | grep -qw xxh128; }

# Every destination must be creatable: its nearest existing ancestor has to be
# writable for a copy and readable for a verification.
check_destinations() {
  local access="$1" job name src dest filters script=""
  for job in "${JOBS[@]}"; do
    IFS='|' read -r name src dest filters <<<"$job"
    script+="d=$(quoted "$DATA/$dest"); while [ ! -e \"\$d\" ]; do d=\$(dirname \"\$d\"); done; "
    script+="[ -d \"\$d\" ] && [ -$access \"\$d\" ] && [ -x \"\$d\" ] || { echo $(quoted "$name"):\"\$d\"; exit 1; }; "
  done
  remote "$script"
}

printf "${B}Import %s → %s:%s${N}\n" "$DRIVE" "$REMOTE" "$DATA"
note "log: $LOG"

title "1. Preflight"
mountpoint -q "$SOURCE" || fail "$SOURCE is not a mount point; is the drive plugged in and mounted?"
# uuid any is for sources without a filesystem UUID, such as a tmpfs.
if [[ "$SOURCE_UUID" != any ]]; then
  source_uuid="$(findmnt -no UUID --mountpoint "$SOURCE")"
  [[ "$source_uuid" == "$SOURCE_UUID" ]] ||
    fail "$SOURCE holds filesystem ${source_uuid:-without a UUID}, not $DRIVE ($SOURCE_UUID)"
fi
for job in "${JOBS[@]}"; do
  IFS='|' read -r name src dest filters <<<"$job"
  [[ -d "$SOURCE/$src" ]] || fail "step $name: $SOURCE/$src does not exist"
done
ok "source mounted: $SOURCE ($(findmnt -no FSTYPE,UUID --mountpoint "$SOURCE" | tr -s ' '))"
has_xxh128 "$(rsync --version)" || fail "local rsync does not support xxh128 checksums"
remote true 2>/dev/null || fail "cannot reach $REMOTE over SSH without a password"
ok "SSH connection to $REMOTE (this run's own, aes128-gcm, no compression)"
has_xxh128 "$(remote 'rsync --version')" || fail "server rsync does not support xxh128 checksums"
ok "both rsyncs support xxh128"
remote "mountpoint -q $(quoted "$DATA")" || fail "$DATA is not mounted on $REMOTE; is the data disk connected?"
ok "$DATA is mounted on the server"
if ((VERIFY_ONLY)); then
  denied="$(check_destinations r)" || fail "destination not readable on $REMOTE: $denied"
  ok "every destination readable"
else
  denied="$(check_destinations w)" || fail "destination not writable on $REMOTE: $denied"
  ok "every destination writable (${#JOBS[@]} steps)"
fi
if [[ -n "$MAX_TEMP" ]]; then
  temp="$(remote_temperature)"
  ((temp > 0)) || fail "sensor $SENSOR not found on $REMOTE; see /sys/class/hwmon/*/name there"
  ok "server CPU temperature: $temp °C (pause at $MAX_TEMP, resume at $((MAX_TEMP - 8)))"
fi
if [[ -n "${IMPORT_DISCORD_WEBHOOK:-}" ]] || grep -q '^DISCORD_WEBHOOK_ALERTS=https://' "$REPO_DIR/compose/.env" 2>/dev/null; then
  ok "Discord notification when the run ends"
else
  note "no DISCORD_WEBHOOK_ALERTS in compose/.env; the end of the run is not posted to Discord"
fi

if ((VERIFY_ONLY == 0)); then
  free_bytes="$(remote "df -B1 --output=avail $(quoted "$DATA") | tail -1 | tr -d ' '")"
  ok "free space on the data disk: $(human "$free_bytes")"

  title "2. Plan" "(what still has to be copied)"
  total_bytes=0 total_files=0
  non_regular="$STATE_DIR/$RUN_ID.non-regular.txt"
  : >"$non_regular"
  printf "  ${B}%-13s %-40s %-32s %9s %9s${N}\n" STEP FROM TO FILES SIZE
  for job in "${JOBS[@]}"; do
    IFS='|' read -r name src dest filters <<<"$job"
    rsync_base "$filters"
    listing="$WORK/plan-$name"
    rsync "${RSYNC_ARGS[@]}" "${LIST_NON_REGULAR[@]}" --dry-run --stats --out-format='%i %n' \
      "$SOURCE/$src" "$REMOTE:$DATA/$dest" >"$listing" 2>>"$LOG" || fail "could not plan step $name; see $LOG"
    awk -v step="$name" '/^[<>ch.][LDS]/ { print step ": " substr($0, 13) }' "$listing" >>"$non_regular"
    stats="$(<"$listing")"
    files="$(awk -F': ' '/Number of regular files transferred/ {gsub(/,/,"",$2); print $2}' <<<"$stats")"
    bytes="$(awk -F': ' '/Total transferred file size/ {gsub(/[, a-z]/,"",$2); print $2}' <<<"$stats")"
    [[ "$files" =~ ^[0-9]+$ && "$bytes" =~ ^[0-9]+$ ]] || fail "could not read rsync statistics for step $name"
    total_files=$((total_files + files))
    total_bytes=$((total_bytes + bytes))
    printf "  %-13s %-40s %-32s %9s %9s\n" "$name" "${src:0:40}" "${dest:0:32}" "$files" "$(human "$bytes")"
  done
  printf "  ${B}%-13s %-40s %-32s %9s %9s${N}\n" TOTAL "" "" "$total_files" "$(human "$total_bytes")"
  if [[ -s "$non_regular" ]]; then
    head -5 "$non_regular" | sed 's/^/    /'
    fail "the source has $(wc -l <"$non_regular") symlink(s) or special file(s), which are never imported; replace them with real files or remove them (full list: $non_regular)"
  fi
  rm -f "$non_regular"
  ((total_bytes < free_bytes)) || fail "not enough free space on the server"
  ((total_bytes > 0)) && note "at the data disk's ~30 MB/s this takes about $((total_bytes / 30000000 / 60)) min"

  ((DRY_RUN)) && { printf "\n${G}Dry run only, nothing copied.${N}\n"; exit 0; }

  if ((total_bytes > 0)); then
    if ((ASSUME_YES == 0)); then
      printf "\n  Copy %s in %s files? Nothing is deleted on either side. [y/N] " "$(human "$total_bytes")" "$total_files"
      read -r answer
      [[ "$answer" =~ ^[yY]$ ]] || { echo "  Aborted."; exit 1; }
    fi
    NOTIFY=1

    title "3. Copy" "(each file is checksummed as it lands)"
    started=$SECONDS
    step=0
    for job in "${JOBS[@]}"; do
      step=$((step + 1))
      IFS='|' read -r name src dest filters <<<"$job"
      printf "\n  ${B}[%d/%d] %s${N}  ${D}%s → %s${N}\n" "$step" "${#JOBS[@]}" "$name" "$src" "$dest"
      rsync_base "$filters"
      run_guarded "" "" rsync "${RSYNC_ARGS[@]}" --no-inc-recursive --info=progress2,stats1 --log-file="$LOG" \
        "$SOURCE/$src" "$REMOTE:$DATA/$dest" || fail "step $name failed (rsync exit $?); see $LOG, then re-run to resume"
    done
    elapsed=$((SECONDS - started))
    ok "copied $(human "$total_bytes") in $((elapsed / 60)) min $((elapsed % 60)) s"
    OUTCOME="copied $(human "$total_bytes") in $total_files files"
  else
    title "3. Copy"
    ok "nothing left to copy"
  fi
fi

((SKIP_VERIFY)) && {
  OUTCOME="${OUTCOME:-nothing to copy}; verification skipped"
  printf "\n${Y}Verification skipped.${N} Run with --verify-only later.\n"
  exit 0
}
NOTIFY=1

title "4. Verify" "(xxh128 content, missing files and files only on the server)"
note "reads everything again on both disks; expect roughly as long as the copy"
report="$STATE_DIR/$RUN_ID.verify.tsv"
printf "step\tproblem\tpath\n" >"$report"
problems=0
step=0
for job in "${JOBS[@]}"; do
  step=$((step + 1))
  IFS='|' read -r name src dest filters <<<"$job"
  rsync_base "$filters"
  protect=()
  [[ "$dest" =~ $ORGANIZED_TREES ]] && protect=("${ORGANIZED_PROTECT[@]}")
  itemized="$WORK/verify-$name"
  label="$(printf '[%d/%d] %-13s' "$step" "${#JOBS[@]}" "$name")"
  begun=$SECONDS
  # rsync writes straight to a file so its own exit status is what gets checked;
  # --delete in a dry run is what reports files that exist only on the server.
  if ! run_guarded "$label" "$itemized" rsync "${RSYNC_ARGS[@]}" "${protect[@]}" "${LIST_NON_REGULAR[@]}" \
    --checksum --delete --dry-run --out-format='%i %n' "$SOURCE/$src" "$REMOTE:$DATA/$dest"; then
    printf "\r  ${R}✘${N} %s could not be verified; see %s\n" "$label" "$LOG"
    printf "%s\tverification did not complete\t-\n" "$name" >>"$report"
    problems=$((problems + 1))
    continue
  fi
  # Each line is an 11-character item code, a space, then the path. rsync escapes
  # tabs, newlines and other unprintable bytes in paths as \#ooo, so a path always
  # stays on its line. Codes: <f or >f a file whose content or presence differs,
  # cd a missing directory, L/D/S a symlink, device or special file, *deleting
  # something only on the server. Attribute-only lines are not differences.
  found="$(awk -v step="$name" '
    { code = substr($0, 1, 11); path = substr($0, 13) }
    code ~ /^\*deleting/          { print step "\tonly on server\t" path; next }
    code ~ /^[<>ch.][LDS]/        { print step "\tnot a regular file, never imported\t" path; next }
    code ~ /^[<>]f\+\+\+\+/       { print step "\tmissing on server\t" path; next }
    code ~ /^[<>]f/               { print step "\tcontent differs\t" path; next }
    code ~ /^cd\+\+\+\+/          { print step "\tmissing folder on server\t" path; next }
  ' "$itemized")"
  partials="$(remote "find $(quoted "$DATA/$dest") -type d -name .rsync-partial -print0 2>/dev/null | head -zn 5 | $ESCAPE_LINES" || true)"
  if [[ -n "$partials" ]]; then
    found+="${found:+$'\n'}$(awk -v step="$name" '{ print step "\tleftover partial transfer\t" $0 }' <<<"$partials")"
  fi
  if [[ -z "$found" ]]; then
    printf "\r  ${G}✔${N} %s identical on both sides (%d s)\n" "$label" $((SECONDS - begun))
  else
    count="$(wc -l <<<"$found")"
    printf "\r  ${R}✘${N} %s %d difference(s)\n" "$label" "$count"
    printf "%s\n" "$found" >>"$report"
    problems=$((problems + count))
  fi
done

if ((problems)); then
  OUTCOME="${OUTCOME:+$OUTCOME; }$problems problem(s), listed in $report"
  printf "\n${R}${B}Verification failed${N}: %d problem(s), listed in %s\n" "$problems" "$report"
  grep -qP '\t(missing|leftover)' "$report" &&
    printf "Missing files and partial transfers: run the import again (without --verify-only), then verify.\n"
  grep -qP '\tcontent differs\t' "$report" &&
    printf "Differing content with the same size and time is not recopied by a re-run: find which side is\nright, and delete the bad server copy to have the next run copy it again.\n"
  grep -qP '\tonly on server\t' "$report" &&
    printf "Files only on the server are never deleted by this script; review them by hand.\n"
  grep -qP '\tnot a regular file' "$report" &&
    printf "Symlinks and special files are never imported: replace them with real files on the source, or remove them.\n"
  exit 2
fi
rm -f "$report"
OUTCOME="${OUTCOME:+$OUTCOME; }all files verified"
printf "\n${G}${B}All files verified.${N} The source drive is untouched; keep it until offsite backup exists.\n"
