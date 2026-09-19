# Diagnosing incorrect frame boundaries

A repaired MP4 can group multiple H.264 frames into a single sample, causing
frame-number changes, slice-header errors, incorrect frame counts and a video
track much shorter than its audio. Another reference can change the apparent
duration without correcting picture decoding.

Test this hypothesis by extracting a small candidate stream to Annex B H.264
and allowing FFmpeg to parse access-unit boundaries using validated SPS/PPS.
If the sample decodes cleanly, apply the method to the full stream and verify it.
The `untrunc-reframe` target implements this for H.264 without B-frames, using an
explicit FPS assumption. It retains available audio and does not trim automatically.
Constant-rate reconstruction does not restore missing original timestamps.

Verify rotation on the installed FFmpeg version. Older versions write display
metadata during remux; newer versions support an input display-rotation override.
Always compare the resulting metadata and review frames.

For residual errors, map packet times to original byte offsets. Inspect NAL
lengths, parsed slice headers, frame numbering and dependency chains from IDR
frames. Random bytes can resemble valid headers. Test a suspected damaged boundary
with and without its candidate NAL before reconstructing the full stream.

Store file-specific assumptions and original hashes only in a private recipe.
Never transfer a recording's SPS/PPS, bit lengths, GOP cycle, offsets or frame
counts to another input without independent evidence. Header searches should use
bounded, justified hypotheses and short samples first.

Removing a damaged ending is cleanup, not recovery. Request authorization for a
shortened derivative and retain the maximal recovery and full recovered audio.
A decode-clean result does not establish that all original footage survived.
