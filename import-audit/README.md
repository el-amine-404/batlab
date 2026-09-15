# Import audit

Sonarr and Radarr can finish a download and still get the library wrong without
saying so. In 2026-09 a *Courage the Cowardly Dog* season pack was named in TVDB
order, Sonarr translated the numbers through a scene-numbering map that did not
fit it, filed each two-segment file as four episodes, rejected the real files
for the episodes it believed were taken, and reported the season complete. A
fifth of the show was missing and a third of the titles were wrong.

`scripts/audit.py` runs hourly and posts to `#downloads` when:

| Check | Means |
| --- | --- |
| wrong episodes | a library file's torrent source is named for other episodes than Sonarr assigned |
| left behind | a finished video in `torrents/` is in no library folder, and its episode or movie still has no file |
| stuck in queue | Sonarr or Radarr flags a download as blocked or failing |

Every check reads the current state, so a fixed problem stops being reported.
Downloads younger than three hours are skipped while they may still be
importing. Files replaced by an upgrade, and movie extras such as featurettes
next to an imported film, are not problems. Episode numbers are read from the
file name rather than from Sonarr's parser, which applies the same scene maps
that caused the original mistake. Each problem is posted once, and again only if
it disappears and comes back; the list lives in
`~/.local/state/batlab-import-audit/reported.json`.

The API keys and the webhook come from `compose/.env` (`SONARR_API_KEY`,
`RADARR_API_KEY`, `DISCORD_WEBHOOK_DOWNLOADS`).

```bash
import-audit/scripts/audit.py --dry-run     # print what it would report
python3 -m unittest discover -s import-audit/scripts/tests
```

## Fixing what it reports

Sonarr or Radarr → Wanted → Manual Import, pick the torrent folder, set each
file's episodes from its name, import. For a wrong episode, delete the series'
affected episode files first (they are hardlinks; the torrent copy stays).

## One-time installation on the server

```bash
sudo install -m 644 import-audit/systemd/batlab-import-audit.service \
  import-audit/systemd/batlab-import-audit.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-import-audit.timer
```
