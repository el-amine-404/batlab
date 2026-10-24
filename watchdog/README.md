# Server watchdog

Two jobs, both born from the 2026-09 outage where the USB data disk dropped off
the bus, nine processes hung in uninterruptible I/O for six days, SSH stopped
answering, and nothing told anyone.

- **Heartbeat** — every five minutes `scripts/heartbeat.sh` checks that the data
  disk answers a direct read, sshd sends its banner, and journald syncs, then
  pings healthchecks.io. A failed or hung check sends `/fail` and alerts at once;
  a frozen or offline server stops pinging and alerts after the grace time. The
  watcher runs off-site, so it still works when this host cannot. The heartbeat
  also posts its own Discord card when it starts failing and when it passes
  again, so a failure reaches the channel even if the healthchecks.io check has
  no integration. On 2026-10-10 it failed every run for ten hours and nobody
  heard of it.
  On a host serving the network's DNS, set `WATCHDOG_DNS_SERVER` and the
  heartbeat also asks it for a name and fails without an address back. On a
  host without the data disk, leave `WATCHDOG_DATA_ROOT` empty: the disk check is
  skipped, and the data guard is not installed there.
- **Data guard** — containers that bind-mount `/mnt/storage/data` run only while
  it is mounted. Without the disk they would write into the empty mountpoint on
  the root filesystem. DNS, Caddy, gluetun and everything else are unaffected.
  Each stop and start posts to Discord. A disk that hangs while still mounted
  never unmounts, so the heartbeat stands in: after two failed reads in a row
  (`WATCHDOG_DISK_HUNG_AFTER`) it runs `data-guard.sh hung`, and when reads
  work again without a remount it runs `data-guard.sh start`.

- **SMART alerts** — `scripts/smart-notify.sh` turns a smartd warning into the
  same Discord card. Debian ships smartd writing to root's local mailbox, which
  nobody reads, so a failing disk was announced to no one. Which disks are
  watched, and with what temperature limits, is a host file:
  `hosts/<profile>/smartd.conf`.
- **Memory alerts** — Netdata warns when a limited container stays near its
  cgroup limit. `oom_watch.py` checks the kernel journal every minute and sends
  a failure card naming the container and killed process after any container or
  host OOM, even when the machine as a whole still has free RAM.

Netdata's Discord alerts, including the blocked-process alert that would have
caught the outage, are configured in `compose/netdata/alerts/`.

Every card goes through Apprise (`compose/apprise`) to the alerts channel, as
"watchdog on <host>", and straight to `WATCHDOG_DISCORD_WEBHOOK` when Apprise
can't be reached: with the data disk gone or Docker down, that's the usual
case (`lib/batlab-common/batlab_common/notify.py`).

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
sudo systemctl enable --now batlab-oom-watch.timer
sudo systemctl enable batlab-data-guard-boot.service
sudo systemctl enable --now batlab-data-guard.service
```

The unit paths assume the repository is `/home/potato/batlab`.

## Test before relying on it

```bash
sudo systemctl start batlab-watchdog-heartbeat.service
sudo journalctl -u batlab-watchdog-heartbeat.service -n 20 --no-pager
```

Test the OOM notification route without consuming memory or killing anything:

```bash
sudo watchdog/scripts/oom_watch.py --test-notification
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

## After the disk drops off

When the disk returns to the bus it gets a new device name (`sdb1` becomes
`sdd1`), but the dead mount stays at `/mnt/storage/data` and reading it gives
`Input/output error`. systemd still sees the mount as active under the old name,
so `systemctl start mnt-storage-data.mount` waits for the old device and fails
with "A dependency job … failed". Clear the dead mount first; Samba holds it open:

```bash
sudo systemctl stop smbd nmbd
sudo umount /mnt/storage/data || sudo umount -l /mnt/storage/data
sudo systemctl daemon-reload
sudo systemctl start mnt-storage-data.mount
sudo systemctl start smbd nmbd batlab-mediascan-watch.service batlab-watchdog-heartbeat.service
```

`umount -l` is safe here: nothing can write through a device that is gone. The
mount runs fsck first, then the data guard starts the containers it stopped.

When the disk is still on the bus but hangs (reads time out after 180 s with
`Sense Key : Hardware Error`, processes stuck in `D` state), only a power cycle
of the enclosure frees it. `scripts/recover-data-disk.sh` walks through that:
it pauses Samba, mediascan and every `batlab-*` timer, unmounts, asks you to
unplug and replug the enclosure, checks SMART, runs fsck through the mount,
reads 1 GB as a test and resumes everything. `--usb2` first moves every USB port
to USB 2 (`scripts/usb2-only.sh`) and makes that permanent with the host's
`batlab-usb2-only.service`:

```bash
ssh -t lab2 sudo ~/batlab/watchdog/scripts/recover-data-disk.sh --usb2
```

## Bringing the disk back without a hand

On 2026-10-17 lab2 went down at 21:48 with nothing in the journal and booted two
minutes later without its data disk: the JMS578 enclosure enumerated at USB 3,
`usb2-only.sh` disabled USB 3 as it does at every boot, and the disk never came
back on the port's USB 2 side. That had happened after the boots of 10-01 and
10-03 too, and each time only a replug brought it back.

Two things now do the replug in software, where a root hub lets them:

- `usb2-only.sh` power-cycles the USB 2 side of every port it pushes a device
  off, so the disk reconnects at 480M at boot instead of hanging between the two.
- `scripts/revive-data-disk.sh`, every two minutes (`batlab-data-disk-revive.timer`),
  does nothing while the disk is mounted and answering. Otherwise it clears a
  dead mount (gone, or hung per the heartbeat's failed reads), mounts a disk that
  is present (fsck first; the data guard then starts its containers), and if the
  disk is off the bus power-cycles the USB 2 side of its connector
  (`WATCHDOG_DATA_USB_PORT`). As a last resort it lets the disk back on at USB 3
  (`WATCHDOG_REVIVE_USB3_FALLBACK=1`) and says so: a disk that may hang later
  still serves more than one off the bus. Each revival posts to the alerts
  channel with how it was done; a disk none of this brings back is announced
  once as needing a hand and tried again every `WATCHDOG_REVIVE_RETRY_MINUTES`.

A lab2 that freezes outright reboots itself: systemd pings the laptop's iTCO
hardware watchdog (`hosts/lab2/systemd/system.conf.d/watchdog.conf`), and a
kernel panic reboots after 10 s (`hosts/lab2/sysctl.d/90-batlab-panic.conf`).

Install on lab2:

```bash
cd ~/batlab
sudo install -m 755 watchdog/scripts/usb2-only.sh /usr/local/sbin/batlab-usb2-only
sudo install -m 644 watchdog/systemd/batlab-data-disk-revive.service \
  watchdog/systemd/batlab-data-disk-revive.timer /etc/systemd/system/
sudo install -d /etc/systemd/system.conf.d
sudo install -m 644 hosts/lab2/systemd/system.conf.d/watchdog.conf /etc/systemd/system.conf.d/
sudo install -m 644 hosts/lab2/sysctl.d/90-batlab-panic.conf /etc/sysctl.d/
sudo sysctl --system
echo WATCHDOG_DATA_USB_PORT=usb1-port1 | sudo tee -a /etc/batlab-watchdog/watchdog.env
sudo systemctl daemon-reexec
sudo systemctl enable --now batlab-data-disk-revive.timer
```

## Planned maintenance

Unmounting the data disk makes the heartbeat fail, and within two minutes
`revive-data-disk.sh` mounts it again. Stop its timer first
(`sudo systemctl stop batlab-data-disk-revive.timer`) and pause the
healthchecks.io check; start and resume both afterwards.

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
