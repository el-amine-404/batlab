# Scanloom

Scanloom turns photographs of paper, cards, and receipts into clean document
pages. It is independent of the PDF gateway: a person may download corrected
images, an ordered ZIP, or a PDF, and may send that PDF anywhere they choose.

## Product contract

- Uploading starts automatic processing immediately. The upload order is the
  initial page order; no margins, paper size, or ordering questions interrupt it.
- Every page is malware-scanned before an image decoder sees it, decoded with
  strict size/type limits, normalized to a new metadata-free raster, detected,
  perspective-corrected, and quality-scored.
- The automatic result is the primary screen. Fine-tuning corners, rotation,
  appearance, order, or page membership is optional and creates a new result.
- Originals and derivatives are temporary. A project and all of its capability
  links expire; cleanup is automatic. There is no account or API authentication
  in the first release, so Caddy restricts the app to the trusted LAN.
- Processing never uses generative reconstruction. A blur, glare, clipped-edge,
  or low-resolution warning asks for a better capture rather than inventing data.
- "Integrity" means file integrity: exact JPEG/PNG/WebP parsing, rejection of
  appended/polyglot payloads, malware scanning, decompression limits, and a
  fresh metadata-free raster. Scanloom does not claim that it can reliably
  determine whether the pictured document was created or altered by AI; that
  is a different forensic problem and automated detectors are not a sound
  authenticity control.

## Services and trust boundaries

| Service | Rights | Responsibility |
| --- | --- | --- |
| `web` | no storage | Static PWA and same-origin proxy |
| `api` | incoming RW; derivatives RO | REST API, streaming upload, settings, downloads |
| `worker` | incoming + work + exports | ClamAV gate, decoding, detection, correction, exports |
| `postgres` | database only | Projects, page metadata, settings, leases, events |
| `clamav` | definitions only | Malware signatures and bounded stream scanning |

The API never decodes images. The worker claims durable PostgreSQL jobs with
`FOR UPDATE SKIP LOCKED`, so another worker can be added without introducing a
second queue. Jobs have expiring leases and bounded retries. Paths never cross
the API: records carry opaque IDs and every filesystem lookup is derived from an
ID below a configured root.

## API outline

- `POST /api/v1/projects` — multipart images; returns the project immediately.
- `GET /api/v1/projects/{id}` and `/events` — state and live progress.
- `PATCH /api/v1/projects/{id}` — title, profile, and page order.
- `PATCH /api/v1/projects/{id}/pages/{pageId}` — corners, rotation, appearance.
- `POST /api/v1/projects/{id}/render` — rerender after optional edits.
- `POST /api/v1/projects/{id}/exports` — image, ZIP, or PDF expiring link.
- `GET/PUT /api/v1/settings` — every operational/product setting.
- `GET /api/v1/system/status` and `/health` — Homepage and health checks.

OpenAPI at `/docs` is the authoritative request/response reference.

## Data lifecycle

Incoming files are write-only to the API and read-only to a worker. Successful
normalization removes embedded profiles and metadata. Default project retention
is 24 hours and default download-link life is one hour. Deleting a project or
expiry removes every file and database row. PostgreSQL is backed up; transient
image and export directories are excluded from Restic because their contract is
automatic expiry, not durable storage.

## Verification

The image unit suite covers signatures, polyglot/trailing payload refusal,
limits, corner ordering, perspective output, and quality warnings. Release
verification additionally runs a real photograph through ClamAV, correction,
image/ZIP/PDF exports, expiring downloads, settings, disguised-file refusal,
and worker-owned deletion. Browser verification covers desktop, iPhone-sized,
and Android-sized upload/result/edit/settings/export flows plus PWA registration.
