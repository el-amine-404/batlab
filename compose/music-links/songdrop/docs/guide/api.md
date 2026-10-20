# Using the API

songdrop's page uses this API, and so can any other app: a music browser that
sends a song to songdrop, a script, a phone shortcut. Every route, field and
answer is in the [API reference](/api/){target="_self"}, made from the running
API's own description, so it is always exact. This page is the overview.

## Basics

| | |
| --- | --- |
| Base URL | `http://songdrop.homelab.lan/api/v1` |
| Format | JSON in and out, UTF-8. Times are Unix seconds (`1792008398.7`). |
| Access | The home network only, and an API key: made in Settings → API keys (or `docker exec songdrop python -m app.admin add-key <name>`), sent as `X-Api-Key`. Each key sees its own downloads; only a key may ask for a `callback_url`. |
| Description | `GET /api/v1/openapi.json` (OpenAPI 3). |
| Versions | A change that breaks a caller goes to `/api/v2`; v1 keeps working. New fields may appear in any answer: ignore the ones you don't know. |

## Errors

Every error is a status code and a sentence a person can read:

```json
{"detail": "yt-dlp doesn't know this site, so it can't be downloaded here."}
```

| Status | Meaning |
| --- | --- |
| `400` | The link, file or folder can't be used. |
| `404` | No such job, file, connection or route. |
| `409` | Not now: the job is still running, or has to be cancelled first. |
| `410` | The file has expired. |
| `413` | The upload is too big. |
| `422` | A field isn't valid; `detail` says which and why. Nothing was changed. |
| `500` | songdrop failed; its log says why. |

## Download a link

```bash
# 1. Start a job
curl -s -X POST http://songdrop.homelab.lan/api/v1/jobs -H "X-Api-Key: $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://open.spotify.com/track/3v3STGBmaci4IJQRlC2V1z"}'
# → 201 {"id": "9b082a7de1bc42efb37b088312b869e3", "status": "queued", …}

# 2. Ask until "status" is done, partial, failed or cancelled
curl -s -H "X-Api-Key: $KEY" http://songdrop.homelab.lan/api/v1/jobs/9b082a7de1bc42efb37b088312b869e3

# 3. Download a song: each file has its own "url"
curl -OJ -H "X-Api-Key: $KEY" http://songdrop.homelab.lan/api/v1/jobs/9b082a7de1bc42efb37b088312b869e3/files/4f1c2a9e0b7d
```

`options` in step 1 changes how this one link is downloaded; what you leave out
takes the saved defaults. `GET /api/v1/options` lists them.

```json
{"url": "https://www.youtube.com/watch?v=kg_BSGvRei0", "options": {"split_chapters": true}}
```

## Be called back

Instead of asking in step 2, pass a `callback_url` with the job. songdrop posts
the job there when it ends, and again each time a later try of one of its songs
ends:

```http
POST /songdrop-done HTTP/1.1
Content-Type: application/json
User-Agent: songdrop

{"event": "job.updated", "job": {…the job, as GET /jobs/{id} gives it…}}
```

Answer with any `2xx`. songdrop doesn't send a failed callback again; ask
`GET /jobs/{id}` when in doubt.

To hear about every job, not only the ones you started, add a
[Webhook connection](connections) instead.

## Settings and connections

`GET /settings` and `PATCH /settings` (only the fields to change) are what the
Settings screens use. Secrets are write-only: connections say which secret
fields are set (`secrets_set`), never their values. `GET /folders` and
`GET /folders/check` list and check folders under the output root, for the
output folder.
