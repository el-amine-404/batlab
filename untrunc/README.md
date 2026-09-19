# Video recovery with Untrunc

Start with [the repeatable next steps](NEXT-STEPS.md): private case configuration, library
scanning/reference selection, optional forensic cleanup, and local agent setup.

One-shot CPU tools for repair experiments, with FFmpeg verification and a reusable
agent skill. Original media is read-only; each attempt gets its own directory.
No model, GPU, homelab secrets or shared Docker network is needed for these tools.
The pinned Untrunc source is built in Docker; Ubuntu packages are resolved at build
time (the entire image is not bit-for-bit reproducible). Runtime has no network.

## First run

From the batlab checkout, with Docker Compose v2 and working Docker permissions:

```bash
make untrunc-build
make untrunc-init CASE=example-video
```

Copy files using your file manager into:

```text
~/video-repair/cases/example-video/
├── input/         # damaged.mp4; original filename can be retained
├── references/    # healthy clips from the same device/mode
└── work/          # separate attempts, logs, hashes, reports and review images
```

Use `REPAIR_ROOT=/mnt/storage/video-repair` on the homelab to change the location.
Set it consistently on every Make command. Use a dedicated case directory, not
an entire photo library. SELinux labels are shared container labels on these case
folders; originals elsewhere are never mounted. Runtime data stays outside git.
Do not run a repair while another program changes the input files.

```bash
make untrunc-inventory CASE=example-video
make untrunc-repair CASE=example-video BROKEN=damaged.mp4 REFERENCE=healthy.mp4
make untrunc-auto CASE=example-video BROKEN=damaged.mp4
```

`auto` checks up to three references in saved selection order (filename order
without selection.json), tries normal and skip-unknown modes, and verifies candidates.
It runs the bounded set even after a decode-clean result: a shorter clean output
must not hide additional recoverable footage. Curate references or specify `REFERENCE=healthy.mp4`.
`MAX_REFERENCES=1..10` changes the limit; `REPAIR_TIMEOUT=1800` is the per-command
limit in seconds. Multiple full copies/attempts may require several times the input
size in free space. A timeout stops that experiment; it does not establish failure
of all possible recovery methods.

When H.264 frame grouping is suspect and the recording rate is supported by evidence:

```bash
make untrunc-auto CASE=example-video BROKEN=damaged.mp4 FPS=30
# Or reparse one previous candidate (path relative to work/):
make untrunc-reframe CASE=example-video CANDIDATE=attempt/input/output.mp4 FPS=30
make untrunc-verify CASE=example-video CANDIDATE=attempt/reconstructed.mp4
```

The automated reframe supports H.264 without B-frames; B-frame timing requires
a specialized reconstruction. Set REFERENCE for a manual reframe if the candidate
lost its original rotation metadata; auto uses its chosen reference. FPS is a timing assumption, not original timestamp recovery. No automatic reframe
without FPS. No automatic trimming, frame fabrication, speed stretching, or source
replacement. The separate hash-guarded private-recipe cleanup requires ALLOW_TRIM=1. The original byte-scanning/header experiments require agent judgment;
`auto` is a bounded standard workflow, not an autonomous forensic specialist.

Each report distinguishes decoding errors from human review. Exit 0 means a
candidate passed decoding, **not** 100% recovery; exit 2 means investigation is still
needed; exit 1 is a setup/execution error. Make may itself report status 2 for any
failed recipe, so consult JSON reports. Review images, duration, rotation and audio
synchronization before accepting output. Decoding errors can coexist with exit 0
from FFmpeg; the helper checks both its exit code and error-level log.

These services use the `tools` profile, so normal homelab `make up` does not launch
repair jobs. `make untrunc-help` lists targets. `make untrunc-config` validates Compose
without requiring `compose/.env`. Never give a repair container the Docker socket.

## Local agent: Hermes + Ollama

Recommendation: use **Hermes Agent** to drive these tools and **Ollama** to serve a
local model. Start by benchmarking `qwen3.5:9b` (tools and vision; roughly 6.6 GB of
model weights). This is a starting point, not a tested performance promise for your
laptop. Context and runtime memory are additional. A smaller model may be faster
but less reliable for header diagnosis; a larger one may be slow on CPU.

Choose a model based on available VRAM, system RAM and measured tool reliability.
See [NEXT-STEPS.md](NEXT-STEPS.md) for saved JSON configuration and the
`untrunc-agent-install`, `untrunc-agent-setup`, `untrunc-agent-model`, and
`untrunc-agent` targets. No remembered shell exports or manual profile merge is needed.

The skill is at `skills/video-recovery/SKILL.md`. It retains the procedure and
general recovery procedures; it does not fine-tune weights. A skill + scripts +
measurable evaluations is the first investment. Fine-tuning would require a varied,
labelled collection of recovery cases and held-out tests, and cannot restore bytes
that no longer exist. Better prompts help an agent use evidence; they do not
substitute for model capacity or validation.

Hermes/model installation happens only when you invoke its install/model targets;
no GPU driver changes are performed.
Avoid granting the agent unrelated homelab access; run jobs where their case files
are mounted. Keep recovery logs locally: they can include private media metadata.

Sources:
- [Untrunc](https://github.com/anthwlock/untrunc)
- [Hermes local setup](https://hermes-agent.nousresearch.com/docs/guides/local-ollama-setup)
- [Hermes skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/)
- [Qwen3.5 9B model](https://ollama.com/library/qwen3.5:9b)

## Developer verification

```bash
python3 -m unittest discover -s untrunc/tests -v
make untrunc-config
```

Tests require host Python 3.10+, FFmpeg/FFprobe and libx264. They generate synthetic
media in temporary directories; private family footage is never committed.

The tests cover synthetic intact/corrupt media, reference ranking, safe staging,
path traversal, frame reconstruction and reproducible agent configuration. Build
and validate the container and agent on the deployment host as well.

Real case files, filenames, hashes, media and forensic recipes belong outside git.
The example case is a template; use a private copy under `~/.config/batlab/`.
