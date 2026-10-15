# Connect

**Settings, Connect** lists where songdrop tells you what happened. Each
connection has a type, the events it gets, and an on/off switch. Each card says
how its last message went.

## Add a connection

1. Press **Add a connection** and choose its type.
2. Fill in its fields. A field like a webhook URL is a secret: once saved it is
   never shown again; leave it empty when editing to keep it.
3. Tick the events it should get.
4. **Test** sends a test message with what the form says now; **Save** keeps it.

## Types

| Type | What it does |
| --- | --- |
| Discord | Posts a message to a channel, through a webhook (the channel's settings, Integrations, Webhooks). |
| Webhook | Sends each event as JSON to an address of your choice: any app can listen. |

## Events

| Event | Ticked for a new connection | When |
| --- | --- | --- |
| `job.incomplete` | yes | A download ended without some of its songs: which, why, and when they are tried again. |
| `song.recovered` | yes | A later try downloaded a missing song. |
| `song.gave_up` | yes | The last later try of a song failed. |
| `job.finished` | no | Any download that ended, complete or not. |

## What a webhook receives

A `POST` with JSON, the format the planned batlab notifier takes
(`docs/services.md` in the repository):

```json
{
  "source": "songdrop",
  "event": "job.incomplete",
  "time": 1792008398.7,
  "subject": "songdrop · DESPERADO (15 of 16 songs)",
  "text": "- missing: Stormy, JRK 19 - EL MEJOR: YouTube refused the audio. Trying again in 10 min.",
  "url": "http://songdrop.homelab.lan/activity/missing",
  "data": {"job": {"id": "9b082a7de1bc42efb37b088312b869e3", "status": "partial", "…": "…"}}
}
```

`data.job` is the job as `GET /api/v1/jobs/{id}` gives it. `song.recovered` and
`song.gave_up` add `data.song` (its name) and `data.tries`. Answer with any
`2xx`; songdrop doesn't send a failed message again.

## Before Connect

songdrop used to take one Discord webhook in Settings. On its first start with
Connect, that webhook (or `SONGDROP_DISCORD_WEBHOOK`) becomes a connection named
Discord with the events ticked by default. After that, only Connect counts.
