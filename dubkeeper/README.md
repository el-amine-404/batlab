# Dub keeper

Sonarr's anime profile ranks a release by picture first (the release group's
tier) and by dub second (`Anime Dual Audio` only breaks ties within a tier). That
is the intended order, but it means an upgrade can replace a dual-audio file
with a better Japanese-only one, and with no recycle bin the English track was
gone for good. On 2026-10-02 that nearly happened to all of *Vinland Saga* S1,
which also had Arabic, English and French sidecar subtitles made by hand.

`scripts/dubkeeper.py` runs every 15 minutes and, for each Sonarr upgrade of the
last 14 days:

1. finds the replaced file in Sonarr's recycle bin;
2. lists the audio languages it had and the new file lacks (commentary aside);
3. checks that the two files are the same video: video lengths within two
   seconds, and a language both carry whose loudness lines up, with one
   consistent offset, at 20% and 65% of the episode;
4. copies the missing tracks into the new file with ffmpeg stream copy (no
   re-encode, about 40 s of disk time and 12 s of CPU for an episode), shifted
   by the measured offset when it is 40 ms or more, and not marked default;
5. copies back sidecar subtitles (`.en.srt` and the like) the new file lacks,
   renamed to match it, once ffsubsync (the tool Bazarr syncs with, run in the
   Bazarr container) has checked each against the new file's audio: it measures
   each half of the subtitle separately, and a subtitle is restored only when
   both halves want the same shift (within 0.3 s), shifted by it;
6. asks Sonarr to rescan the series and posts to `#download-issues`.

A pair that fails step 3, or a new file that is not MKV, is reported and left
alone; the old file stays in the recycle bin until Sonarr's cleanup removes it.

Subtitles are checked on their own because comparing the two videos is the
wrong question for them. On 2026-10-03 *Attack on Titan* S04's Blu-ray replaced
TV and WEB releases, and a first version copied their subtitles over unchecked.
Measured afterwards, per subtitle and per half:

| Subtitle | First half | Second half | Outcome now |
| --- | --- | --- | --- |
| S04E10 `.en.hi` (TV, 6 s longer) | −0.08 s | +0.07 s | restored as is |
| S04E15 `.en.hi` (TV) | +21.77 s | +21.95 s | restored, shifted +21.84 s |
| S04E24 `.en` (WEB) | −0.62 s | +0.47 s | refused: cut differently |
| S04E24 `.ar.hi` (WEB) | −0.11 s | +0.06 s | restored as is |

A video check alone refused E10 and E15, which fit or needed only a shift.
Only SRT is checked; other formats are left in the recycle bin.

```bash
dubkeeper/scripts/dubkeeper.py --dry-run          # print decisions, change nothing
dubkeeper/scripts/dubkeeper.py --check OLD NEW    # analyse one pair of files
python3 -m unittest discover -s dubkeeper/scripts/tests
```

The *Vinland Saga* pair, iAHD as the old file and UQW as the new, measured
−20 ms at r = 0.999 and 1.000. The merge kept UQW's 48 font attachments, five
chapters and both ASS tracks.

## Requires Sonarr's recycle bin

Settings → Media Management → Recycling Bin is `/data/recycle`, cleanup after
14 days (set 2026-10-03; Sonarr keeps it in its database, not in this
repository). The folder is `/mnt/storage/data/recycle` on the host, on the same
disk as the library so a recycle is a rename. `--days` should match the cleanup
period. Cleanuparr's orphaned-files cleaner is not configured; if it ever is,
the recycle bin must be excluded from it.

## Side effects

The merged file replaces the library file, so it is no longer a hardlink of the
torrent copy. That copy drops to one link, Cleanuparr tags it `unlinked`, and the
torrent is deleted after seven idle days, as after any upgrade.

Sonarr's file name keeps the audio tags of the release (`[JA]`) until the files
are renamed; the custom format score is that of the release, so Cleanuparr's
Seeker may still look for a dual-audio copy in the same tier.

## One-time installation on the server

```bash
sudo install -m 644 dubkeeper/systemd/batlab-dubkeeper.service \
  dubkeeper/systemd/batlab-dubkeeper.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-dubkeeper.timer
```
