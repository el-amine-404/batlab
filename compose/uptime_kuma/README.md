# Uptime Kuma

Its alerts go through Apprise (compose/apprise) as the sender `uptime`, so they
are the same Discord card as every other batlab message: "qBittorrent is down"
or "is back", a red or green stripe, and the error, the downtime, the ping and
the address one per line.

The notification lives in Uptime Kuma's database, not here. To set it up again
(Settings, Notifications, the one applied to every monitor):

- Type **Webhook**, URL `http://apprise:8000/notify/uptime`, method POST
- Request body **Custom body**: the contents of `notifications/apprise-body.liquid`
- Additional headers: `{"Content-Type": "application/json"}`

Its Discord integration wrote "✅ Your service X is up! ✅" with the name
repeated below it; that is what this replaced on 2026-10-17.
