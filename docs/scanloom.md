# Scanloom

Scanloom turns photographs of paper, cards and receipts into clean document
pages, on the home network at `https://scanloom.homelab.lan`.

## What it promises

- **Nothing is scanned until you say.** Dropped files or camera pictures go up one
  at a time, in order, and each gets the security check and a clean copy, so it can
  be shown, HEIC too. Remove any, then scan or cancel. Then the screen shows each step
  and the time left. (By the API, a page is scanned at once unless `hold=true`.)
- **Scans are kept until deleted**, and are the same on every device: one live
  stream tells each open page what changed. `retention_days` in the settings can
  turn on automatic deletion for new scans.
- **A file is never scanned twice.** Each original's SHA-256 is kept in PostgreSQL.
  The same file in the same scan isn't added. Scanned on its own in a one-page scan, it
  is refused in review with a link to that scan: only a copy would come of it. Part of
  a larger scan, it is welcome in another, and silently reuses the earlier work (a hard
  link to the clean copy, the same corners). The bytes must be the same: a re-encoded
  copy of a photo is a new file.
- **Every page is checked before a decoder sees it**: ClamAV, exact container
  checks, decode limits, then a new metadata-free raster. The upload itself is
  deleted once that copy exists.
- **Nothing is invented.** Blur, glare and low resolution become notes on the page.
  With no plausible page in a photo, the whole photo is kept and flagged.
- **A failed page doesn't hold the others back**: the rest can be downloaded, and
  the failed one tried again.

## Who may use it

Accounts as in batlab's other apps (`lib/batlab-common`): user name and password,
a setup code in the log until the first admin exists, then roles.

```
docker exec scanloom-api admin setup-code
docker exec -it scanloom-api admin reset-password <user name>
docker exec scanloom-api admin add-key <name> --can scan
```

`scan`: make scans and manage one's own. `everyone`: see everybody's. `settings`:
limits and the activity log. `admin`: all, and people and keys. Scans made before
accounts belong to nobody: admins see them. Only `/api/v1/health` and the counts of
`/api/v1/system/status` (Homepage) are open.

## Services

| Service | Rights | Does |
| --- | --- | --- |
| `web` (container `scanloom`) | none | The page, and the same-origin proxy |
| `api` | incoming and state RW; finished files RO | REST API, uploads straight to disk, downloads, live events; never decodes an image |
| `worker` | incoming, work, exports | Security check, decoding, detection, correction, exports, cleanup |
| `db` | its data | Scans, pages, settings, the queue, events |
| `clamav` | definitions | Signature scanning, by stream |

The queue is PostgreSQL (`FOR UPDATE SKIP LOCKED`), with leases that a heartbeat
extends and a limit on how often a job that kills its worker is retried; a worker
more needs no other change. New jobs and events go through `NOTIFY`, so nothing
polls. Downloads first, then edits, then new pages, then back-filling.

Each page has its finished PNG and four smaller forms: JPEG, `preview` (WebP 1800),
`thumb` (WebP 480) and `source` (the photo, WebP 2200, for the crop editor). An
image's address carries the page's version and is cached for good, so lists load
small files once.

## Finding the page

The model's corners, from the photo, its middle and, when unsure, parts of it, are
scored by whether real edges run along them, and looked at again from closer. Unsure
but seeing a large four-sided thing still beats the classical fallbacks, which latch
onto what is printed inside. A background outline along the photo's borders is
distrusted. What the live camera followed beats everything but a sure model. Nothing
plausible: the whole photo, flagged. "Find again" in the crop editor redoes it.

## Exports

`pages` takes page numbers as written, `1-3,6,7-9`: in that order, a range may run
backwards, a page named twice comes once, and a number outside the scan is refused
with a sentence. The same rules are in `backend/scanloom/pagespec.py` and
`frontend/src/lib/pageSpec.ts`, each tested. Advanced: file name, colour, largest side,
JPEG quality, and for a PDF a paper size with margins.

## The live camera

`frontend/src/scanner`: the DocAligner model in a worker, on a 256-pixel frame, the
next frame only once the last is answered. `tracker.ts` (tested, no browser in it)
decides over time, in milliseconds: an outline held through short drop-outs, a
shutter that fires once per page after 700 ms of stillness and waits for the page
to change. Only framing holds the shutter back; white paper reads as glare and a
small frame can't judge focus, so those are notes on the finished page. The full
photo comes from `ImageCapture` where there is one, else the video frame, whose
outline goes along as a hint for when the server's own detection is unsure.

## The API

OpenAPI at `/docs`. One photo is one request, its body the file:

```
curl -H "X-Api-Key: sl_…" -H "Content-Type: application/json" -d '{"title":"Invoices"}' https://scanloom.homelab.lan/api/v1/projects
curl -H "X-Api-Key: sl_…" --data-binary @photo.heic "https://scanloom.homelab.lan/api/v1/projects/<id>/pages?name=photo.heic"
```

## Data

`work/` (finished scans) and `state/accounts.db` are backed up by Restic, and the
database is dumped. `incoming/`, `exports/` (made again at will, removed after
`export_ttl_minutes`) and the ClamAV definitions aren't.

## Tests

The image build runs `backend/tests` and `frontend/src/__tests__`. `test_flow.py`
needs a PostgreSQL: set `SCANLOOM_TEST_DATABASE_URL`.
