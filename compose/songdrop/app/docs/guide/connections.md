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
| Apprise | Sends through [Apprise](https://github.com/caronc/apprise-api) to the channel its tag names. Apprise delivers to Discord, Telegram, ntfy, e-mail and about 100 more, as its configuration says. The way batlab's own services send. |
| Discord | Posts a message to a channel, through a webhook (the channel's settings, Integrations, Webhooks). |
| Webhook | Sends each event as JSON to an address of your choice: any app can listen. |

### Apprise

| Field | |
| --- | --- |
| Apprise address | Its `/notify/` address with the configuration key: `http://apprise:8000/notify/songdrop` in batlab (each sender has its own key, so the message shows who sent it). |
| Channel | The tag of the destinations in Apprise's configuration, as `download-issues`. Several, separated by commas; empty sends to every destination. |

The message has the event's subject as its title, its details as the text and
a link to the page. Its type says how it reads: `warning` for missing songs,
`failure` when songdrop gives up, `success` for a song back or a finished
download. A **Test** that names a tag Apprise doesn't know says so.

## Events

| Event | Ticked for a new connection | When |
| --- | --- | --- |
| `job.incomplete` | yes | A download ended without some of its songs: which, why, and when they are tried again. |
| `song.recovered` | yes | A later try downloaded a missing song. |
| `song.gave_up` | yes | The last later try of a song failed. |
| `job.finished` | no | Any download that ended, complete or not. |

## What a webhook receives

A `POST` with JSON (batlab's event format, `docs/services.md` in the
repository):

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

## The first connection

When songdrop starts with no connection saved, it makes one from its
environment, with the events ticked by default: an Apprise connection when
`SONGDROP_APPRISE_URL` is set (`SONGDROP_APPRISE_TAG` its channel), else a
Discord one from `SONGDROP_DISCORD_WEBHOOK` or the webhook older versions kept
in Settings. After that, only Connect counts.
