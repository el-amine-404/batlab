# VPN port guard

qBittorrent runs inside gluetun's network namespace and is reachable from
outside only through the port Proton forwards. Without one, peers cannot
connect to it, and a torrent with only a few reachable seeds stalls.

On 2026-10-03 two things took the port away within an hour:

- gluetun restarted the tunnel after a failed health check and landed on a
  server whose NAT-PMP gateway answered `not authorized`. gluetun does not retry
  that answer, so the port stayed 0 until a manual reconnect.
- the next server forwarded a port for four minutes, then its gateway timed out
  and refused (`10.2.0.1:5351: connection refused`). gluetun retries those, but
  the gateway stayed down.
- from 17:35 the port changed every minute or two and the tunnel lost half its
  packets, in Spain as in the Netherlands. Seven gluetun test containers, left
  running by `timeout docker run` while checking countries, were connected with
  the same WireGuard key; stopping them brought loss to 0% and the port held.

Separately, after any port change the trackers keep handing out the old port
until each torrent's next announce, up to half an hour later.

`scripts/vpnguard.py` runs every five minutes:

| Sees | Does |
| --- | --- |
| tunnel running, port 0 on two runs in a row | reconnects through gluetun's control server (another server from the pool), waits up to 2 min for a port, re-announces; at most 3 times an hour, then one "still down" message for the hour |
| port different from the last run | asks qBittorrent to re-announce every torrent |
| tunnel down | nothing: gluetun's health check restarts it, Uptime Kuma reports it |

Reconnects and the hourly give-up post to `#download-issues`. The control server
and qBittorrent listen only inside gluetun's namespace, so the calls go through
`docker exec`.

```bash
vpnguard/scripts/vpnguard.py --dry-run    # print the decision, change nothing
python3 -m unittest discover -s vpnguard/scripts/tests
```

## Choosing the exit country

Port forwarding narrows Proton to its P2P servers. From lab2 on 2026-10-03, with
the number of WireGuard servers that forward ports:

| Country | Ping | Servers |
| --- | --- | --- |
| Portugal | 14 ms | 4 |
| France | 32 ms | 13 |
| Spain | 33 ms | 6 |
| Switzerland | 43 ms | 9 |
| United Kingdom | 46 ms | 13 |
| Netherlands | 49 ms | 7 |

Throughput was not the limit: 21 MB/s through the tunnel to Hetzner Nuremberg,
against 12 MB/s measured outside it a moment later. Stalls came from the
swarm and the missing port, not the exit. More servers in the pool make a bad
server cheaper, since a reconnect has more to choose from: `SERVER_COUNTRIES`
takes a comma-separated list (`GLUETUN_SERVER_COUNTRIES` in `compose/.env`),
set on 2026-10-03 to the five countries above other than the Netherlands plus
Germany (49 ms, 8 servers) and Belgium (43 ms, 3), each checked to forward a
port that day.

## One-time installation on the server

```bash
sudo install -m 644 vpnguard/systemd/batlab-vpnguard.service \
  vpnguard/systemd/batlab-vpnguard.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-vpnguard.timer
```
