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
- `source_root`: local folder or readable CIFS mount, not an SMB URL.
- `repair_root`: external directory for copied inputs and recovery attempts.
- `case`: a short case identifier.
- `broken`: damaged file's path relative to source_root.
- `references`: relative paths of healthy references, or `[]` for ranked selection.
- `fps`: empty unless evidence supports a reconstruction frame rate.

## Build, scan and repair

Run in a terminal with working Docker Compose access:

```bash
make untrunc-build
make untrunc-case-scan
make untrunc-case-prepare
make untrunc-case-run
```

The targets default to `~/.config/batlab/case.json`. Use
`REPAIR_CONFIG=/path/to/private.json` to select another saved case.
Explicit references allow skipping the scan. Preparation is rerunnable, copies
rather than links source files, preserves basenames and verifies hashes. It refuses
to overwrite different bytes. Runtime sources are mounted read-only.

Scanning fully decodes supported video formats under source_root, so start with a
small folder. The resulting `work/*scan*/catalog/catalog.json` separates clean
videos, decode errors, unreadable files and scan failures. Unreadable is not proof
of corruption: permissions and unsupported formats can also prevent reading.
`scan_mode: "probe"` is faster but does not certify healthy reference candidates.

Rankings compare available codec configuration, dimensions, frame rate, audio,
device metadata, directory and dates. Missing metadata weakens the ranking, which
is labelled heuristic-only. Matching dates do not establish a shared camera.
The scan may run before `broken` names an existing file; inspect the catalog, then
set `broken` before preparing the case. Empty `references` selects ranked healthy
clips. Explicit references override ranking. Size/mtime checks reject stale scans.

Results go under the configured case's `work/` directory. Each attempt records
commands, hashes, metadata, decode errors and review frames. The latest summary's
`results.json` lists candidate paths; `/work/` means this case's work directory on
the host. Inspect footage, audio synchronization, duration and orientation.
Make may report `Error 2` even when candidates exist: check the verification report.
Decode-clean is not proof of complete original recovery.

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
