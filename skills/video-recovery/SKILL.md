---
name: video-recovery
description: Diagnose damaged MP4/MOV recordings and run evidence-driven recovery experiments with Untrunc and FFmpeg, preserving originals and verifying decoded footage. Use for corrupt or unplayable recordings, not ordinary video editing or enhancement.
---

# Video recovery

Use the batlab checkout (default `~/batlab`; locate it if elsewhere). Read
`untrunc/NEXT-STEPS.md` and `untrunc/README.md` there for executable Make targets
and saved JSON case/agent configuration. Runtime cases belong
outside the repository. This skill is a procedure, not trained model weights.

## Invariants and success criteria

- Preserve original bytes, filenames and all previous attempts. Sources are
  mounted read-only. Record hashes, tool versions, commands and assumptions.
- Never trim footage or audio, freeze frames, interpolate images, stretch timing,
  or transcode as an undisclosed repair. Ask for explicit authorization for such
  an optional derivative unless already authorized. Keep maximal recovery too.
- A created MP4 or exit code zero is not success. Decode the entire file, examine
  error logs, compare audio/video lengths and frame counts, inspect beginning,
  middle and last frames, and have the user confirm playback and audio sync.
- Say `decode-clean`, `partially recovered`, `timing reconstructed`, or `trimmed`
  precisely. Decode-clean does not prove full recovery. Unknown original length
  must remain unknown. AI-generated details are not original recovered footage.

## Investigation loop

1. Create a named case with `make untrunc-init CASE=...`. Put copies of damaged
   inputs in input/ and healthy references in references/. Read probe metadata
   and container structure. SMB URLs need a readable filesystem mount or copy.
2. Choose healthy references by codec configuration, device and recording mode;
   nearest date alone is insufficient. Use inventory and decode verification. `untrunc-case-scan` creates a read-only
   full-decode catalog with explainable reference rankings; `untrunc-case-prepare`
   stages selected files from a saved case JSON. Missing codec metadata makes
   rankings heuristic-only, not evidence of the same device.
3. Run default Untrunc and, if it stops at unknown sequences, the separate `-s`
   attempt. `untrunc-auto` automates bounded trials and writes JSON reports.
   It accepts at most MAX_REFERENCES candidates (default 3), in saved selection
   order, or filename order without selection.json; curate the directory or set
   REFERENCE explicitly. It does not stop at the first short decode-clean output.
4. Compare outcomes. Do not blindly repeat equivalent references or add `-sv`
   to hide decoding errors. If there are frame-number changes, approximately
   doubled speed, or wrong frame counts, read [the reconstruction case study](references/frame-boundaries.md).
   `make untrunc-reframe` reparses H.264 boundaries using explicitly justified FPS.
5. If those steps fail, inspect SPS/PPS and slice headers on small samples before
   full reconstruction. Derive offsets, bit lengths and keyframe patterns from
   this file; never reuse a past recording's hardcoded header or GOP cycle.
6. Continue independent, non-destructive experiments while they yield new
   evidence. Default budget: three references and two additional distinct
   hypotheses after standard trials. Each command has a timeout. After two
   hypotheses with no measurable gain, summarize the best result and remaining
   uncertainty; request a different reference or a larger investigation budget.
   Do not loop until apparent success by discarding damaged content.
7. Retain reports and explain remaining corruption, losses, assumptions and
   review status. Confirm references and originals are unchanged. Preserve useful
   new lessons as a case note without storing family images or paths in git.

Prefer deterministic scripts for execution and measurements. Use model judgment
for selecting experiments, interpreting logs and reviewing frames. If the model
cannot view images or hear audio, state which checks need human review.
