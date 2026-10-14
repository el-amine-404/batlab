---
title: API
order: 2
---

# API reference

songdrop's page uses this API, and so can any other app: a music browser that
sends a song to songdrop, a script, a phone shortcut.

## Basics

| | |
| --- | --- |
| Base URL | `http://songdrop.homelab.lan/api/v1` |
| Format | JSON in and out, UTF-8. Times are Unix seconds (`1792008398.7`). |
| Access | The home network only. No key or login. |
| OpenAPI | [`/api/v1/openapi.json`](/api/v1/openapi.json) |
| Versioning | A change that breaks a caller goes to `/api/v2`; v1 keeps working. New fields may appear in any answer: ignore the ones you don't know. |

### Errors

Every error answers with a status code and a sentence a person can read:

```json
{"detail": "yt-dlp doesn't know this site, so it can't be downloaded here."}
```

| Status | Meaning |
| --- | --- |
| `400` | The link or file can't be used. `detail` says why. |
| `404` | No such job, file or route. |
| `409` | Not now: the job is still running, or has to be cancelled first. |
| `410` | The file has expired. |
| `413` | The upload is too big. |
| `422` | The request is malformed, or an option or setting isn't valid. |
| `500` | songdrop failed; its log says why. |

## Quick start

Download a link, wait for it, fetch the songs:

```bash
# 1. Start a job
curl -s -X POST http://songdrop.homelab.lan/api/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://open.spotify.com/track/3v3STGBmaci4IJQRlC2V1z"}'
# → 201 {"id": "9b082a7de1bc42efb37b088312b869e3", "status": "queued", …}

# 2. Ask until "status" is done, partial, failed or cancelled
curl -s http://songdrop.homelab.lan/api/v1/jobs/9b082a7de1bc42efb37b088312b869e3

# 3. Download a song: each file has its own "url"
curl -OJ http://songdrop.homelab.lan/api/v1/jobs/9b082a7de1bc42efb37b088312b869e3/files/4f1c2a9e0b7d
```

Instead of asking, pass a `callback_url` in step 1: songdrop posts the job to it
when it ends ([Callbacks](#callbacks)).

## Links

### Check a link

`POST /detect` tells which engine would take a link, without downloading.

```json
{"url": "https://www.youtube.com/watch?v=kg_BSGvRei0&list=PL123"}
```

```json
{"supported": true, "engine": "ytdlp", "engine_label": "yt-dlp", "site": "YouTube",
 "kind": "media", "url": "https://www.youtube.com/watch?v=kg_BSGvRei0&list=PL123", "has_playlist": true}
```

| Field | |
| --- | --- |
| `engine` | `spotdl` (Spotify links) or `ytdlp` (everything else). |
| `kind` | Spotify: `track`, `album`, `playlist`, `artist`. Other sites: `media` or `playlist`. |
| `has_playlist` | A video link that also names a playlist; the `playlist` option decides which is downloaded. |

A link songdrop can't take answers `200` with `{"supported": false, "message": "…"}`.

### List the options

`GET /options` lists each engine's download options, with the values they
accept and their current defaults (Settings can change them).

```json
{"engines": {"ytdlp": {"label": "yt-dlp", "fields": [
  {"key": "format", "type": "select", "flag": "--audio-format", "label": "Format",
   "choices": [{"value": "original", "label": "Original (no conversion)"}, …], "default": "original"},
  {"key": "clip_start", "type": "time", "kinds": ["media"], "default": ""}, …]}, "spotdl": {…}}}
```

| Field | |
| --- | --- |
| `type` | `select` (one of `choices`), `switch` (true or false), `time` (`"1:23"`, `"1:02:03"`, seconds, or `""`). |
| `kinds` | Only for these kinds of link. |
| `needs` | `has_playlist`: only for a video inside a playlist. `list`: only for a playlist, or a video inside one. |

## Jobs

### Start a job

`POST /jobs` → `201` and the [job](#the-job-object).

| Field | Required | |
| --- | --- | --- |
| `url` | yes | The link. |
| `options` | no | Options for this job, by `key`. What you leave out takes the default. |
| `callback_url` | no | `http://` or `https://` address that gets the job when it ends. |

```json
{"url": "https://www.youtube.com/watch?v=kg_BSGvRei0",
 "options": {"split_chapters": true, "format": "original"},
 "callback_url": "http://browser.lan/songdrop-done"}
```

Fails with `400` for a link songdrop can't take, `422` for an option that isn't
valid (`"The clip ends before it starts."`).

### List jobs

`GET /jobs` → every job, newest first, and how long finished jobs are kept.

```json
{"jobs": [{…}, {…}], "retention_hours": 24}
```

### Get a job

`GET /jobs/{id}` → the [job](#the-job-object).

### Cancel a job

`POST /jobs/{id}/cancel` → the job. A queued job is cancelled at once; a running
one stops within seconds, keeping the songs it finished.

### Try a missing song again

`POST /jobs/{id}/errors/{index}/retry` → the job. `index` is the missing song's
`index` in `errors`. Works whatever its `state`, also after songdrop gave up.
`404` when the song has no link of its own, `409` while the job is running.

### Remove a job

`DELETE /jobs/{id}` → `204`. Removes the job and its files from songdrop; copies
already in the output folder stay. `409` while it runs: cancel it first.

### The job object

```json
{
  "id": "9b082a7de1bc42efb37b088312b869e3",
  "url": "https://open.spotify.com/album/3Fhd5mu9DDnph1m4j9Hioo",
  "engine": "spotdl", "site": "Spotify", "kind": "album",
  "status": "partial",
  "title": "DESPERADO",
  "message": null,
  "total": 16, "completed": 15, "progress": 1.0, "current": null,
  "created_at": 1792001000.1, "started_at": 1792001001.3,
  "finished_at": 1792001240.8, "expires_at": 1792087640.8,
  "files": [
    {"id": "4f1c2a9e0b7d", "name": "Stormy - SKI.opus", "size": 2306112,
     "title": "SKI", "artist": "Stormy", "delivered": true,
     "url": "/api/v1/jobs/9b082a7de1bc42efb37b088312b869e3/files/4f1c2a9e0b7d"}
  ],
  "errors": [
    {"index": 3, "name": "Stormy, JRK 19 - EL MEJOR",
     "message": "YouTube refused the audio three times in a row. Try this one again later.",
     "url": "https://open.spotify.com/track/3v3STGBmaci4IJQRlC2V1z",
     "state": "waiting", "tries": 1, "max_tries": 4, "next_retry_at": 1792001840.8}
  ],
  "notes": [{"name": "ElGrandeToto - Intro",
             "message": "spotDL found nothing; got it from another upload (0.93 like Spotify's preview)."}],
  "archive_url": "/api/v1/jobs/9b082a7de1bc42efb37b088312b869e3/archive"
}
```

| Field | |
| --- | --- |
| `status` | `queued`, `running`, then `done` (every song), `partial` (some), `failed` (none) or `cancelled`. |
| `message` | Why a job failed or stopped, as a sentence. |
| `total`, `completed` | Songs in the link, and songs downloaded. `total` is `0` until known. |
| `progress` | `0` to `1` while running; `null` when unknown. |
| `current` | The song downloading now. |
| `expires_at` | When the job and its files are removed (not the copies in the output folder). |
| `files[].delivered` | Copied to the output folder. |
| `errors[]` | Songs that failed. |
| `errors[].state` | `waiting` (for its next try at `next_retry_at`), `queued` or `retrying` (being tried now), `gave_up` (no more tries on its own), `failed` (can't be tried alone: no `url`). |
| `notes[]` | Worth knowing: another upload replaced a wrong one, a video had no chapters. |
| `archive_url` | All songs in one zip, when there is more than one and the job has ended. |

## Files

### Download a song

`GET /jobs/{id}/files/{file_id}` → the file, as an attachment. `410` once expired.

### Download all songs

`GET /jobs/{id}/archive` → a zip of the job's songs. `409` while the job runs.

## Callbacks

A job started with a `callback_url` is posted there when it ends, and again
each time a later try of one of its songs ends (the song came back, or songdrop
gave up):

```http
POST /songdrop-done HTTP/1.1
Content-Type: application/json
User-Agent: songdrop

{"event": "job.updated", "job": {the job object}}
```

Answer with any `2xx`. songdrop does not retry a callback that fails; ask
`GET /jobs/{id}` when in doubt.

## Settings

### Get the settings

`GET /settings`

```json
{
  "deliver": true, "output_root": "/output", "output_folder": "music/songdrop", "output_folder_exists": true,
  "retention_hours": 24, "max_items": 200, "concurrent_jobs": 1, "spotdl_parallel": 3,
  "job_timeout_minutes": 180, "retry_minutes": [10, 60, 360],
  "discord_webhook_set": true, "public_url": "http://songdrop.homelab.lan",
  "defaults": {"spotdl": {"format": "opus", …}, "ytdlp": {"format": "original", …}},
  "limits": {"retention_hours": [1, 720], "max_items": [1, 1000], "concurrent_jobs": [1, 4],
             "spotdl_parallel": [1, 8], "job_timeout_minutes": [10, 1440], "retry_minutes": [1, 10080], "retries": 6}
}
```

The Discord webhook is never returned: `discord_webhook_set` says whether there
is one. `limits` gives each number's lowest and highest value.

### Change settings

`PATCH /settings` with the settings to change → the new settings. What you leave
out keeps its value. Changes apply to the next job and survive a restart.

```json
{"output_folder": "music/songdrop", "retry_minutes": [5, 30], "concurrent_jobs": 2}
```

| Field | |
| --- | --- |
| `deliver` | Copy songs to the output folder. |
| `output_folder` | A path inside `output_root`, e.g. `music/songdrop`. |
| `retention_hours`, `max_items`, `concurrent_jobs`, `spotdl_parallel`, `job_timeout_minutes` | Numbers within `limits`. |
| `retry_minutes` | Minutes before each later try, at most 6. `[]`: none. |
| `discord_webhook` | A `https://discord.com/api/webhooks/…` link; `""` removes it. |
| `public_url` | The page's address, for links in messages. |
| `defaults` | Default options per engine: `{"ytdlp": {"format": "mp3"}}`. |

`422` with the reason when a value isn't valid; nothing is changed then.

### YouTube cookies

| Request | |
| --- | --- |
| `GET /cookies` | `{"present": false}`, or `{"present": true, "saved_at": …, "expires_at": …, "expired": false}`. Never the cookies. |
| `PUT /cookies` | The request body is the `cookies.txt` file itself (Netscape format, at most 1 MB, with youtube.com cookies). → the status. `400` with the reason otherwise. |
| `DELETE /cookies` | → `204`. |

```bash
curl -X PUT --data-binary @cookies.txt http://songdrop.homelab.lan/api/v1/cookies
```

## Service

`GET /health` → `{"ok": true}` while songdrop runs.
