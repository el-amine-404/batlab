# Server watchdog

Two jobs, both born from the 2026-09 outage where the USB data disk dropped off
the bus, nine processes hung in uninterruptible I/O for six days, SSH stopped
answering, and nothing told anyone.

- **Heartbeat** — every five minutes `scripts/heartbeat.sh` checks that the data
  disk answers a direct read, sshd sends its banner, and journald syncs, then
  pings healthchecks.io. A failed or hung check sends `/fail` and alerts at once;
  a frozen or offline server stops pinging and alerts after the grace time. The
  watcher runs off-site, so it still works when this host cannot.
- **Data guard** — containers that bind-mount `/mnt/storage/data` run only while
  it is mounted. Without the disk they would write into the empty mountpoint on
  the root filesystem. DNS, Caddy, gluetun and everything else are unaffected.
  Each stop and start posts to Discord.

- **SMART alerts** — `scripts/smart-notify.sh` turns a smartd warning into the
  same Discord card. Debian ships smartd writing to root's local mailbox, which
  nobody reads, so a failing disk was announced to no one. Which disks are
  watched, and with what temperature limits, is a host file:
  `hosts/<profile>/smartd.conf`.

Netdata's Discord alerts, including the blocked-process alert that would have
caught the outage, are configured in `compose/netdata/alerts/`.

A disk that disappears from the bus, the 2026-09 failure, raises no SMART
warning at all: smartd can only ask disks it can still talk to. The data guard
and the heartbeat cover that case; SMART covers the disk that is still answering
while it degrades.

## One-time installation on the server

Create a healthchecks.io check named `lab1`: period 5 minutes, grace 10 minutes,
Discord integration enabled. Then install the configuration:

```bash
sudo install -d -m 700 /etc/batlab-watchdog
sudo install -m 600 watchdog/conf/watchdog.env.example /etc/batlab-watchdog/watchdog.env
sudo editor /etc/batlab-watchdog/watchdog.env
```

Seal the directory underneath the mountpoint so nothing can be written there even
before the guard runs at boot. Mount the parent without its submounts to reach
it, remove only empty leftovers, and stop if anything else is there:

```bash
under="$(mktemp -d)"
sudo mount --bind /mnt/storage "$under"
sudo find "$under/data" -mindepth 1 -depth -type d -empty -delete
sudo find "$under/data" -mindepth 1 | head
sudo chattr +i "$under/data"
sudo umount "$under" && rmdir "$under"
```

If the second `find` prints anything, it was written while the disk was absent:
move it onto the real disk before running `chattr`.

Install and enable the units:

```bash
sudo install -m 644 watchdog/systemd/*.service watchdog/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now batlab-watchdog-heartbeat.timer
sudo systemctl enable batlab-data-guard-boot.service
sudo systemctl enable --now batlab-data-guard.service
```

The unit paths assume the repository is `/home/potato/batlab`.

## Test before relying on it

```bash
sudo systemctl start batlab-watchdog-heartbeat.service
sudo journalctl -u batlab-watchdog-heartbeat.service -n 20 --no-pager
```

The `lab1` check should turn green. For the guard, with the arr stack idle:

```bash
sudo systemctl stop mnt-storage-data.mount
docker ps --format '{{.Names}}'
sudo systemctl start mnt-storage-data.mount
docker ps --format '{{.Names}}'
```

The data-disk containers should disappear and come back, with two Discord
messages from `watchdog on lab1`. Use the unit rather than a bare `umount`:
systemd stops the containers before unmounting, while `umount` just fails with
the disk busy.

## Planned maintenance

Unmounting the data disk makes the heartbeat fail. Pause the `lab1` check in
healthchecks.io first, and resume it afterwards.

## SMART alerts

```bash
sudo cp /etc/smartd.conf /etc/smartd.conf.packaged
sudo install -m 644 hosts/<profile>/smartd.conf /etc/smartd.conf
sudo install -m 755 watchdog/scripts/smart-notify.sh /etc/smartmontools/run.d/20discord
sudo systemctl restart smartmontools.service
```

`run-parts` requires a name without a dot, hence `20discord`. It runs after the
packaged `10mail`, which exits at once because the disks are configured with
`-m <nomailer>`.

Test the path from smartd to Discord without waiting for a disk to fail:

```bash
sudo env SMARTD_FAILTYPE=EmailTest SMARTD_DEVICESTRING=/dev/sdb \
  SMARTD_MESSAGE='test message' /etc/smartmontools/run.d/20discord </dev/null
```
