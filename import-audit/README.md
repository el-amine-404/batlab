# Import audit

Sonarr and Radarr can finish a download and still get the library wrong without
saying so. In 2026-09 a *Courage the Cowardly Dog* season pack was named in TVDB
order, Sonarr translated the numbers through a scene-numbering map that did not
fit it, filed each two-segment file as four episodes, rejected the real files
for the episodes it believed were taken, and reported the season complete. A
fifth of the show was missing and a third of the titles were wrong.

`scripts/audit.py` runs hourly and posts to `#download-issues` when:

| Check | Means |
| --- | --- |
| wrong episodes | a library file's torrent source is named for other episodes than Sonarr assigned |
| left behind | a finished video in `torrents/` is in no library folder, and its episode or movie still has no file |
| stuck in queue | Sonarr or Radarr flags a download as blocked or failing |
| duplicate downloads | two queued downloads cover the same episode or movie; the message names the one to keep (higher score, then further along) |

Every check reads the current state, so a fixed problem stops being reported.
Downloads younger than three hours are skipped while they may still be
importing, and so is any download still in Sonarr's or Radarr's queue: a file's
age is its own, and a 110 GB pack whose files finished hours earlier waited 36
minutes for its import on 2026-10-03. A file that names no episode, in a pack
whose episodes were imported, is an extra (creditless openings, making-ofs). Files replaced by an upgrade, and movie extras such as featurettes
next to an imported film, are not problems. Episode numbers are read from the
file name rather than from Sonarr's parser, which applies the same scene maps
that caused the original mistake. Each problem is posted once, and again only if
it disappears and comes back; the list lives in
`~/.local/state/batlab-import-audit/reported.json`.

The API keys and the webhook come from `compose/.env` (`SONARR_API_KEY`,
`RADARR_API_KEY`, `DISCORD_WEBHOOK_DOWNLOAD_ISSUES`).

```bash
import-audit/scripts/audit.py --dry-run     # print what it would report
python3 -m unittest discover -s import-audit/scripts/tests
```

## Auto-import (opt-in)

With `--auto-import`, a Sonarr download blocked although every video names its
own episode is imported by those names: `SxxEyy`, and the looser `S1 - Ep01`
that made Sonarr take each file of *Vinland Saga*'s UQW pack (2026-10-02) for the
whole season. It applies only when every video maps to one existing episode and
no two map to the same one; Sonarr then re-checks the mapping (upgrade, quality,
sample) and any objection leaves the download in the stuck-in-queue report. An
import is posted as `auto imported`. A season pack is now reported once, not
once per episode.

It is on in the service since 2026-10-03, once the UQW pack it would have
imported over hand-made subtitles was gone. Reinstall the unit after pulling:

```bash
sudo install -m 644 import-audit/systemd/batlab-import-audit.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Check first with `--dry-run --auto-import`, which lists what it would import:
any download it names will replace library files if Sonarr counts it as an
upgrade. The [dub keeper](../dubkeeper/README.md) then carries over audio and
subtitles the upgrade lost.

## Fixing what it reports

Sonarr or Radarr → Wanted → Manual Import, pick the torrent folder, set each
file's episodes from its name, import. For a wrong episode, delete the series'
affected episode files first (they are hardlinks; the torrent copy stays).

For a duplicate, Activity → Queue → ✕ on the download it says to remove:
remove from download client, do not blocklist (the release is fine), skip the
redownload. Keeping both is also fine when the smaller one finishes days
sooner: it can be watched now and the other replaces it on import. Sonarr
grabbed a season pack's episodes a second time on 2026-09-30, most likely
before it had matched the queued pack to its episodes.

## One-time installation on the server

```bash
sudo install -m 644 import-audit/systemd/batlab-import-audit.service \
  import-audit/systemd/batlab-import-audit.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-import-audit.timer
```
