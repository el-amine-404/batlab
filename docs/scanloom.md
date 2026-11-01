# Scanloom

Scanloom turns photographs of paper, cards and receipts into clean document
pages, on the home network at `https://scanloom.homelab.lan`.

## What it promises

- **Scanning starts as soon as photos arrive.** Dropped files or camera pictures
  go up one at a time, in order; the screen shows each step and the time left.
- **Scans are kept until deleted**, and are the same on every device: one live
  stream tells each open page what changed. `retention_days` in the settings can
  turn on automatic deletion for new scans.
- **A file is never scanned twice.** The same file in the same scan isn't added;
  the same file in another scan of the same owner reuses the earlier work (a hard
  link to the clean copy, the same corners) and only makes the page.
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
