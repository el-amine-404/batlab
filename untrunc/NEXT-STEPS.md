# Configure a recovery case

Keep media, case configuration, hashes, forensic recipes, logs and reports outside
this public repository. Commit only generic tools, examples and synthetic tests.

## Create private configuration

```bash
cd ~/batlab
mkdir -p ~/.config/batlab
cp -n untrunc/cases/library.example.json ~/.config/batlab/case.json
```

Edit the private JSON:
- `source_root`: local folder or regular (kernel) CIFS/NFS mount, not an SMB URL. A desktop
  FUSE path can be read by Python but not by Docker; see
  [Desktop mount versus Docker access](#desktop-mount-versus-docker-access).
- `repair_root`: external directory for copied inputs and recovery attempts.
- `case`: a short case identifier.
- `broken`: damaged file's path relative to source_root.
- `references`: relative paths of healthy references, or `[]` for ranked selection.
- `fps`: empty unless evidence supports a reconstruction frame rate.

## Use a Samba share as the source

A file manager understands an address such as:

```text
smb://archive-user@nas.example/photos/library/example-event/
```

Python, FFmpeg and Docker bind mounts need a **local filesystem path**, not that
URL. The server name identifies the computer, `photos` is the share, and
`library/example-event/` is the folder inside the share. Do not put a password in
the URL or commit your real server, account or archive paths to this repository.

### Find a desktop file-manager mount

Many desktops expose an authenticated share as a local FUSE path, for example
KIO-Fuse on KDE (`/run/user/<uid>/kio-fuse-*`) or GVFS on GNOME
(`/run/user/<uid>/gvfs/`). Open the share in your file manager and authenticate
there. Then list the mounts visible to your terminal:

```bash
findmnt -o TARGET,FSTYPE,SOURCE | grep -E 'fuse|cifs|nfs'
```

The remaining steps use a KDE KIO-Fuse layout as the worked example; other desktops
name their directories differently, so always inspect with `ls`. On KDE the KIO-Fuse
mount can be listed directly:

```bash
findmnt -t fuse.kio-fuse -o TARGET
```

For example, it may show:

```text
/run/user/1000/kio-fuse-ABC123
```

Inspect the mount rather than guessing its internal directory names:

```bash
ls '/run/user/1000/kio-fuse-ABC123/'
ls '/run/user/1000/kio-fuse-ABC123/smb/'
ls '/run/user/1000/kio-fuse-ABC123/smb/archive-user@nas.example/'
```

If those entries exist, the example URL maps to:

```text
/run/user/1000/kio-fuse-ABC123/smb/archive-user@nas.example/photos/library/example-event
```

The pieces are: **actual mount target + smb + actual server/account directory +
share + folder inside the share**. The UID may differ from 1000; `id -u` shows
yours. The `ABC123` suffix is an example, not a fixed value. If the actual entry
uses different spelling or encoding, use the names returned by `ls`.

If `findmnt` returns nothing, no such mount is visible to that terminal.
Being able to browse a URL in a file manager does not by itself establish a usable
local mount. Copy the required folder locally with the file manager, or use a
regular CIFS mount.

Check the path and read access before configuring a scan:

```bash
media_path='/run/user/1000/kio-fuse-ABC123/smb/archive-user@nas.example/photos/library/example-event'
ls -ld "$media_path"
ls "$media_path"
# Substitute an actual file shown by ls; reads one byte without changing it.
head -c 1 "$media_path/example.mp4" >/dev/null
```

Paste the verified path into `source_root` in your **private** case JSON. JSON
requires the actual path: the shell variable `$media_path` above is only a temporary
inspection convenience. No shell export is needed for later Make commands.

### Desktop mount versus Docker access

A desktop FUSE mount is private to the user session that created it. It works in
your file manager and terminal, but the Docker daemon (which runs as root) is refused.
`make untrunc-case-scan` bind-mounts `source_root` into the scanner container, so it
fails with an error such as:

```text
invalid mount config for type "bind": stat /run/user/1000/<desktop-mount>/...: permission denied
```

This is an access problem, not evidence of damaged video. Do not change archive
permissions broadly or relabel the entire library to work around it. Before starting
Docker, the scan also prints a warning when `source_root` is on a user-space (FUSE)
mount.

Only the **scan** needs Docker to reach the source. `prepare` and `run` copy the
chosen files with your own account, so a desktop path can still work for them when
`references` lists explicit files and you skip the scan.

For a scan, use one of these instead:

**1. A regular CIFS mount** (read-only, at a stable location):

```bash
sudo mkdir -p /mnt/archive
sudo mount -t cifs //nas.example/photos /mnt/archive \
  -o ro,username=archive-user,uid="$(id -u)",gid="$(id -g)",file_mode=0444,dir_mode=0555
```

- This needs the CIFS mount helper (usually the `cifs-utils` package).
- It prompts for the password. For repeated use, pass `credentials=/path/to/file`
  instead: a root-owned file with mode `600` holding `username=` and `password=`
  lines. Keep it outside git.
- Do not add `vers=` unless the server requires it. Forcing a version the server does
  not support fails with `mount error(95): Operation not supported`; see
  [Troubleshooting source access](#troubleshooting-source-access).
- The mount does not survive a reboot. Remove it with `sudo umount /mnt/archive`.
- If `//nas.example/photos` is mounted at `/mnt/archive`, the example folder is
  `/mnt/archive/library/example-event`. Do **not** add `photos` again: the mount
  already represents that share.

Existing regular Samba mounts are listed with:

```bash
findmnt -t cifs -o TARGET,SOURCE
```

**2. A local copy** in a dedicated source folder, kept apart from the recovery case:

```bash
rsync -a --info=progress2 "/path/to/desktop-mount/example-event/" ~/video-repair/source/example-event/
```

`cp -a` also works. Keep modification times (`-a` does): `prepare` compares size and
modification time with the scan. Point `source_root` at the copy.

**3. Explicit references**, skipping the scan (see below).

Store the stable path as `source_root`, then follow the build/scan commands below.
The scanner mounts the library read-only and does not recursively relabel it.
A desktop-mount path (such as KIO-Fuse's random suffix) may change after logout or
remounting; rediscover it and update the private JSON if you keep using one.

## Troubleshooting source access

**`Permission denied accessing source directory` from the Python script**
- The share is not authenticated for this user, or the session expired. Open the
  share again in your file manager (or remount it) and repeat the `ls` check.
- Your shell's primary group differs from the one that owns the mount. Desktop FUSE
  mounts accept only your own user *and* group, so a shell started with `newgrp` or
  `sg` is refused. Compare `id -gn` with a normal login shell, and `exit` the
  `newgrp`/`sg` shell.

**`permission denied while trying to connect to the docker API at unix:///var/run/docker.sock`**
- This session may not be allowed to use Docker. Adding yourself to the `docker` group
  (for example `sudo usermod -aG docker "$USER"`) applies only to **new** login
  sessions. Log out and back in, or run `su - "$USER"` for a fresh login shell, then
  confirm with `id` that `docker` appears in the group list.
- Avoid `newgrp docker` for this workflow: it changes your primary group, which then
  breaks access to desktop FUSE mounts (previous item).
- Membership in `docker` is effectively root access on the host; follow your own
  policy. Rootless Docker does not need this group.

**`invalid mount config for type "bind": stat ...: permission denied`**
- Docker could not read `source_root`, usually because it is a desktop FUSE mount.
  See [Desktop mount versus Docker access](#desktop-mount-versus-docker-access).

**`mount error(95): Operation not supported` (or `mount error(13)`) from `mount -t cifs`**
- Read the kernel log for the exact reason: `sudo dmesg | grep -i cifs | tail`.
- `Dialect not supported by server` means the forced `vers=` is not offered by the
  server. Remove `vers=` to let the kernel negotiate, or try `vers=2.1`. SMB 1.0 is
  disabled in many kernels and is not recommended; use a local copy instead.
- `mount error(13)` normally means a wrong username, password or domain.

**`Source path not found`**
- The mount is absent or its path changed. Manual mounts vanish after a reboot;
  desktop mounts can change their directory name after logout. List current mounts with
  `findmnt -o TARGET,FSTYPE,SOURCE`, then update `source_root`.

## Build, scan and repair

Run in a terminal with working Docker Compose access:

```bash
make untrunc-build
make untrunc-case-scan
make untrunc-case-suspects   # list damaged-looking files and their best healthy matches
make untrunc-case-prepare
make untrunc-case-run        # repair the single file named by "broken"
make untrunc-case-batch      # or repair every suspect found by the scan
```

The targets default to `~/.config/batlab/case.json`. Use
`REPAIR_CONFIG=/path/to/private.json` to select another saved case.
Explicit references allow skipping the scan. Preparation is rerunnable, copies
rather than links source files, preserves basenames and verifies hashes. It refuses
to overwrite different bytes. Runtime sources are mounted read-only.

The scan first prints how many video files it found, then counts through them
(`Scanning 3/37: path/to/clip.mp4`).

**An interrupted scan resumes.** If a scan is stopped (Ctrl-C, `docker stop`, a crash, a
dropped share or a sleeping laptop), run the same `make untrunc-case-scan` command again. It
continues the newest unfinished scan and prints `Resuming ...`:
- Files already scanned are kept if their size and modification time are unchanged. Changed
  files are scanned again, and so are files whose scan failed (for example a timeout).
- Only the file that was being decoded when it stopped is redone.
- Progress is saved at least every 5 seconds and the catalog is replaced atomically, so a hard
  crash can lose at most a few seconds of results and never corrupts the catalog.
- It resumes only when the folder, `scan_mode` and scanner version are the same. Otherwise it says
  why and starts a new scan. A finished scan is never resumed: the next run scans again.
- To start over instead, use `make untrunc-case-scan CASE_ARGS=--fresh`.
- Until the scan finishes, `untrunc-case-suspects` warns that the catalog is incomplete and
  `untrunc-case-batch` and `untrunc-case-run` refuse to use it.

Scanning fully decodes supported video formats under source_root, so start with a
small folder. The resulting `work/*scan*/catalog/catalog.json` separates clean
videos, decode errors, unreadable files and scan failures. Unreadable is not proof
of corruption: permissions and unsupported formats can also prevent reading.
`scan_mode: "probe"` is faster but does not certify healthy reference candidates.

Audio that ffmpeg has no decoder for (for example the Apple spatial-audio track on recent
iPhone recordings, codec tag `apac`) cannot be decoded. Those tracks are still read packet by
packet in the same pass, so truncation and index damage are detected, but corruption
inside that audio is not. Such files stay `decode-clean` and are marked in the catalog
(`packet_checked_streams`), in the scan summary and in the suspects listing, which name
the codec tags involved: an unexpected tag deserves a closer look. Only a proper
four-character tag qualifies; a missing or garbled tag could be header damage, so that
file is reported as a decode error, as is any *video* track without a decoder (the picture
cannot be verified). The same rule applies when repairs are verified.

Rankings compare available codec configuration, dimensions, frame rate, audio,
device metadata, directory and dates. Missing metadata weakens the ranking, which
is labelled heuristic-only. Matching dates do not establish a shared camera.
The scan may run before `broken` names an existing file; inspect the catalog, then
set `broken` before preparing the case. Empty `references` selects ranked healthy
clips. Explicit references override ranking. Size/mtime checks reject stale scans.

The terminal shows named stages, reference/attempt counts and elapsed time.
Long-running stages print activity every five seconds. Full decoding checks show
approximate percentage and ETA when duration is available; other stages explicitly
say remaining time is unknown. Estimates cover the current stage, not the entire
experiment. Detailed tool errors remain in the log files.

After updating scripts in this repository, run `make untrunc-build` again: the
container contains a copy of the scripts. An already-running container continues
using its previous version.

Results go under the configured case's `work/` directory. Each attempt records
commands, hashes, metadata, decode errors and review frames. The latest summary's
`results.json` lists candidate paths; `/work/` means this case's work directory on
the host. Inspect footage, audio synchronization, duration and orientation.
Make may report `Error 2` even when candidates exist: check the verification report.
Decode-clean is not proof of complete original recovery.

## Find suspects and repair a whole folder

After a scan, list what it found without starting Docker or reading the media:

```bash
make untrunc-case-suspects REPAIR_CONFIG=~/.config/batlab/case.json
```

It reads the newest scan for the case and prints, for each file that decodes with
errors or cannot be read as video: its status, size, the first ffprobe or decoder
message, and the ranked healthy matches. A `*` marks the matches a repair would
use (`max_references`). It works even if the share is currently unmounted, and it warns
when the scan is incomplete or belongs to a different `source_root`. The ranking is a
hypothesis: check that a match really is the same camera and recording mode.

To repair one of them, put its path (relative to `source_root`) in `broken` and run
`make untrunc-case-run`. To repair **all** of them in turn:

```bash
make untrunc-case-batch REPAIR_CONFIG=~/.config/batlab/case.json
make untrunc-case-batch REPAIR_CONFIG=... CASE_ARGS='--limit 1'   # try one first
```

Requirements and behaviour:
- The latest scan must be complete, made with `scan_mode: "full"`, and for the same
  `source_root`. A file changed after the scan is rejected as stale: rescan.
- `broken` is ignored. `references` must be `[]` and `fps` empty: references come from
  the scan's ranking for each file, and a frame rate is evidence about one file, so
  use a single-file run when you have explicit references or a justified rate.
- Each suspect gets its own case folder, `<repair_root>/<case>/batch/<name>-<id>/`, with
  its own `input/`, `references/` and `work/`, so files never mix. The folder is
  named from the file, its references and their size/modification time: a changed source
  gets a new folder and the earlier one is kept.
- Per file it does what `untrunc-case-run` does: stage the copies, then up to
  `max_references` references, each in standard and skip-unknown modes. Suspects
  without a decode-verified match are reported as `no references` and not attempted.
- Files run one after another, so a long batch takes hours: expect the scan's decoding
  time plus up to six repair attempts per file. Each attempt copies the damaged
  file, so keep free space for several times the size of the damaged files.
- Progress is saved in `<repair_root>/<case>/batch/batch-report.json` after every
  file. **Ctrl-C is safe.** Running the same command again skips files that already
  have a decode-clean candidate and retries the rest; delete a file's entry from the
  report to force it again.
- After 3 consecutive errors (for example Docker not reachable) it stops instead of
  failing every remaining file. Fix the cause and run the command again.
- `--limit N` processes at most N pending files now; run it again for the next N.

Statuses in the summary:

| Status | Meaning |
|---|---|
| `candidate ready` | At least one output decoded without errors. Its path is printed. |
| `needs investigation` | Untrunc ran but no output was decode-clean. Read that file's `work/` reports. |
| `no references` | The scan found no decode-verified healthy match for it. |
| `error` | Staging or the container failed; the message says why. |

Exit status: 0 when every suspect has a candidate, 2 when any file is unfinished or needs
attention (`make` then prints `Error 2`, which does not mean the tool crashed), 1 when
the batch stopped on errors or could not start, 130 when interrupted.
**A decode-clean candidate is not proof of complete recovery:** play each one and check
the footage, duration and audio sync before keeping it.

## Optional forensic cleanup

Normal repair never trims footage or audio. A separately authorized cleanup can
use an externally stored recipe derived for a particular original:

```bash
make untrunc-case-clean ALLOW_TRIM=1
```

Set `recipes_root` in the private case configuration and `cleanup_recipe` to a
filename inside that directory. Otherwise recipes_root defaults to the case's
recipes/ directory. No real recipe is shipped in the image or repository.

The current recipe executor supports a restricted H.264 slice layout and checks
an exact original SHA-256 before applying derived settings. A knowledgeable agent
must establish SPS/PPS, frame numbering, continuity and the damaged boundary;
it is not an automatic general-purpose tail repair. Fields used by the executor
are documented in `scripts/tail_recipe.py`. It preserves full recovered audio as
a separate file before producing a shortened derivative. Originals and untrimmed
attempts remain. Report cleanup as cleanup, not recovered missing footage.

## Local agent setup

The repair tools work without AI. For an optional local assistant:

```bash
make untrunc-agent-install
make untrunc-agent-setup
make untrunc-agent-model
make untrunc-agent
```

The install target explicitly downloads/runs official Hermes and Ollama installers;
it can prompt for sudo/dependencies. Installer files and hashes are retained under
`~/.cache/batlab-agent-install/`. Upstream installers are rolling versions, not a
bit-for-bit dependency lock. No GPU driver changes are performed.

Settings live in `untrunc/agent/local.json`; use `AGENT_CONFIG=/private/agent.json`
for private overrides. Setup generates a dedicated Hermes home, backs up its prior
config, sets the repository working directory and discovers `skills/`. The launch
target supplies the required environment every time. Your normal Hermes profile
is not modified. The model target configures an explicit context window in Ollama.

The default 9B model needs more memory than its weight download alone. Low-VRAM
GPUs require CPU/RAM participation; benchmark speed and reliable tool calling.
A smaller model is an optional tradeoff, not an assurance of equivalent reasoning.
Use a local endpoint and avoid cloud fallbacks if archive data must stay local.
The agent operates with your local-user privileges; skills are not a sandbox.
Repair containers have read-only sources and no runtime networking.

Example request:

```text
Use video-recovery for my private case configuration. Inspect existing reports,
then run bounded non-destructive experiments. Verify decoding, duration,
orientation and tail frames. Keep full audio. Do not trim or invent footage;
report uncertainty and checks that require human playback review.
```

## Validation

```bash
make untrunc-test
make untrunc-config
```

Tests generate synthetic videos in temporary directories. Validate the actual
Docker image and model behavior on the deployment host before relying on them.

Sources:
- [Untrunc](https://github.com/anthwlock/untrunc)
- [Hermes local models](https://hermes-agent.nousresearch.com/docs/guides/local-ollama-setup)
- [Hermes profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)
- [Hermes skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/)
- [Ollama Qwen3.5](https://ollama.com/library/qwen3.5)
