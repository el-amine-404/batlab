# Migration to two hosts: lab2 serves, lab1 keeps DNS

Status: **phase 1 in progress**. Written 2026-09-30 from the live state of lab1;
phase 0 done 2026-10-01.

## Target

| | lab1 | lab2 |
| --- | --- | --- |
| Machine | Asus K55N, AMD A8-4500M, 15 GB, no battery | Fujitsu LIFEBOOK U748, i5-8350U, 31 GB, battery at 32% health |
| LAN address | `192.168.1.3` (unchanged) | `192.168.1.201` (new, static, outside the DHCP range) |
| Role | DNS and DHCP for the house | everything else |
| Stacks | `adguardhome`, `unbound`, `adguardhome-sync` | all other stacks, plus a second `adguardhome` and `unbound` |
| Data disk | none after the move | the 4 TB USB disk |
| Host units | watchdog heartbeat, smartd | every `batlab-*` unit, samba, smartd, restic |

The internet depends on lab1 alone only for DHCP. DNS answers from both
hosts, so either one can be off without the house losing name resolution.

lab1 keeps `192.168.1.3` because every device on the network was told that
address as its DNS server, and it is hardcoded in `adh.caddy` and the Homepage
AdGuard tile; all of those stay correct without edits.

## Facts this plan rests on

Checked on lab1 on 2026-09-30:

- AdGuard is the network's **DHCP server**: range `.4`-`.200`, 24 h leases, 2
  static leases (`.2` DVR, `.3` lab1), 19 active leases. It hands out only its
  own address as DNS.
- AdGuard's web UI is on port 3000, DNS on 53, upstream `127.0.0.1:5335`
  (Unbound, loopback only), one rewrite: `*.homelab.lan -> 192.168.1.3`.
- `192.168.1.2` is taken (DVR). `.201` answered no ping.
- `/mnt/docker-volumes` fits easily on lab2's 256 GB SSD (lab1's root holds
  66 GB including images).
- Every unit file assumes user `potato` and `/home/potato/batlab`.
- `compose/jellyfin/docker-compose.yml` hardcodes lab1's render group `992` and
  `/dev/dri/card0`.
- `watchdog/scripts/heartbeat.sh` always checks the data disk; on a host
  without it every heartbeat would fail.
- Uptime Kuma checks services by container name, so it must run on lab2.
- The updater only touches containers that exist on its host, so it runs on
  both hosts unchanged.
- Offsite backups go to Google Drive through rclone (`restic/README.md`).

Cannot be known until lab2 is in hand, so each has a test below: whether the
JMS578 enclosure is stable on lab2's Intel USB 3, lab2's render group id and
DRM card name, and whether Quick Sync works inside the Jellyfin container.

## Phase 0: repository changes, before touching hardware

Done 2026-10-01. What differs from the list below: lab1's `stacks.txt` waits for
phase 3 (until the cutover lab1 still runs every stack), the doc moves in item 6
wait for the cutover too, and there is no `CLAUDE.md` to follow or update.

1. **Per-host stack list.** `hosts/<profile>/stacks.txt`, one stack per line.
   The Makefile uses it for `STACKS` when present, otherwise every stack, so
   nothing changes on lab1 until its file exists. lab1 gets `adguardhome`,
   `unbound`, `adguardhome-sync`; lab2 gets every stack.
2. **GPU as host knobs.** In the Jellyfin stack replace the two device lines
   and `"992"` with `/dev/dri:/dev/dri` and `${RENDER_GID:?set in
   hosts/<profile>/compose.env}`; add `RENDER_GID=992` to lab1's
   `compose.env` and a row to its README.
3. **Heartbeat without a data disk.** An empty `WATCHDOG_DATA_ROOT` skips the
   disk check; lab1 sets `WATCHDOG_DNS_SERVER=127.0.0.1` in phase 3, and the
   heartbeat then fails unless AdGuard returns an address.
4. **`adguardhome-sync` stack**, following every step of "Adding a stack" in
   `CLAUDE.md`. Origin `http://192.168.1.3:3000`, replica
   `http://192.168.1.201:3000`, credentials from `.env`. DHCP must not sync:
   the server config and static leases features are off, or the replica would
   start a second DHCP server. Both instances share the `ADGUARD_USER` /
   `ADGUARD_PASS` login from `.env`, plus `ADGUARD_ORIGIN_IP` and
   `ADGUARD_REPLICA_IP`. Image and variables checked against v0.9.3's README.
5. **`hosts/lab2/`**: `compose.env`, `README.md` registry, `smartd.conf`,
   `modprobe.d/usb-quirks.conf` (the JMS578 enclosure moves with the disk),
   `netdata/cpu-temperature.conf` (sensor `coretemp`, Tjmax 100 C: warn 90,
   critical 95), `netdata/go.d-docker.conf` (try enabled, watch dockerd CPU),
   `stacks.txt`. Starting capacity values, adjusted after a week of Netdata:

   ```
   OLLAMA_CPUS=6            OLLAMA_MEM_LIMIT=12gb    OLLAMA_MAX_LOADED_MODELS=1
   IMMICH_ML_MEM_LIMIT=4gb  IMMICH_SERVER_MEM_LIMIT=4gb
   RENDER_GID=<from phase 1, step 7>
   ```

   Every other limit starts from lab1's value.
6. **Docs**: `docs/samba.md` to `.201`,
   `CLAUDE.md` server section to two hosts, `hosts/lab1/README.md` machine
   block to its DNS role.

## Phase 1: install lab2 (lab1 keeps serving)

1. Debian 13 on the internal SSD. First user **`potato`, uid and gid 1000**:
   the data disk's files are owned by 1000, and every unit runs as `potato`.
2. Static address `192.168.1.201/24`, gateway `192.168.1.1`, nameservers
   `127.0.0.1 192.168.1.3` (`docs/install/04-static-ip.md`). Use the onboard
   I219-LM gigabit port, not the USB adapter.
3. BIOS: battery charge limit around 80% if offered; power on after AC loss
   if offered. Check the battery is not swollen.
4. `docs/install/`: sudo, SSH, Docker, git, make.
5. Security layer: `docs/install/07-security.md` (SSH hardening, ufw,
   fail2ban, unattended upgrades, ClamAV), with lab2's DNS-replica firewall
   rule. Done 2026-10-01; lab1's firewall was found off and turned on the same
   day with the DHCP-host rules.
6. Clone the repo to `/home/potato/batlab`. Copy lab1's `compose/.env` and set
   `HOST_PROFILE=lab2`, `HOST_IP=192.168.1.201`.
7. Record the GPU facts, then fill `RENDER_GID` in `hosts/lab2/compose.env`:

   ```bash
   getent group render | cut -d: -f3
   ls /dev/dri
   ```

8. `make network` and `make config`: the whole set must render cleanly before
   the cutover.

## Phase 2: cutover (downtime for everything except DNS)

DNS and DHCP are untouched in this phase, so the house keeps its internet.

1. On lab1: a manual restic backup, and confirm it in `restic snapshots`.
2. On lab1: stop every stack except `adguardhome` and `unbound`. Stop and
   disable the data-disk units: mediascan (watch, sweep, deep), import-audit,
   restic backup and check, updater, data guard. Stop samba.
3. Copy the application state onto the data disk, which moves anyway. Both
   ends run as root on a local disk, so owners (Postgres runs as uid 999),
   hardlinks, ACLs and xattrs survive; a copy over SSH would run as `potato`
   on the receiving side and could not:

   ```bash
   sudo rsync -aHAX --numeric-ids --info=progress2 \
     --exclude adguardhome --exclude unbound \
     /mnt/docker-volumes/ /mnt/storage/data/.migration/docker-volumes/
   ```

   lab1's copy stays in place: it is the rollback.
4. Unmount the data disk on lab1, move it to lab2, add the same fstab line (by
   UUID) and seal the mountpoint as in `watchdog/README.md`.
5. **USB 3 test, before anything writes to the disk for real.** First give
   lab2 the UAS quirk lab1 runs with (`hosts/lab2/modprobe.d/usb-quirks.conf`,
   copied from lab1's) and reboot. Plug into a blue USB 3 port, confirm `lsusb -t` shows it at 5000M, then in one
   terminal watch the kernel and in another write past the SMR cache:

   ```bash
   sudo journalctl -kf | grep -iE 'reset|uas|xhci|i/o error'
   dd if=/dev/zero of=/mnt/storage/data/usb3-test bs=1M count=100000 oflag=direct status=progress
   dd if=/mnt/storage/data/usb3-test of=/dev/null bs=1M iflag=direct status=progress
   rm /mnt/storage/data/usb3-test
   ```

   Pass: no resets or I/O errors across 100 GB each way. If it resets, fall
   back to a USB 2 port, and record the outcome in lab2's README either way. If USB 3 passes, the qBittorrent completion recheck costs minutes, not
   hours; keep it on.
6. On lab2, restore the state and remove the transfer copy, then `make
   setup`, `make up` and `make status`. Check each new container's logs
   (`cap_drop` surprises, see `CLAUDE.md`):

   ```bash
   sudo rsync -aHAX --numeric-ids --info=progress2 \
     /mnt/storage/data/.migration/docker-volumes/ /mnt/docker-volumes/
   sudo rm -rf /mnt/storage/data/.migration
   ```
7. Install the host units from each README: watchdog (new healthchecks.io
   check `lab2`), restic (same repository, root rclone config copied from
   lab1), mediascan, import-audit, updater, samba, and SMART alerts:
   `hosts/lab2/smartd.conf` plus the `20discord` hook, exactly as in
   `watchdog/README.md`. lab1 never got that last step: its smartd still runs
   Debian's stock config and mails root, so no disk warning ever reached
   Discord.
8. Jellyfin: Dashboard > Playback > Transcoding, Intel QuickSync, enable
   HEVC and HEVC 10-bit decoding and VPP tone mapping, low-power encoding off.
   Force a transcode of an HEVC 10-bit episode and confirm in Netdata that the
   CPU stays low.

## Phase 3: DNS on both hosts

1. On lab2: `make up STACK=adguardhome` and `STACK=unbound`. In the first-run
   wizard set the **web UI to port 3000**: the default 80 collides with Caddy.
   Use the same login as lab1's AdGuard (`ADGUARD_USER` / `ADGUARD_PASS`).
   Leave DHCP off.
2. On lab1: add `ADGUARD_ORIGIN_IP=192.168.1.3` and
   `ADGUARD_REPLICA_IP=192.168.1.201` to `compose/.env`, run `make setup` (it
   links `.env` into the new stack), then `make up STACK=adguardhome-sync`;
   confirm the replica received
   the filter lists, user rules and the rewrite.
3. On lab1's AdGuard: change the rewrite to `*.homelab.lan -> 192.168.1.201`.
   Every `*.homelab.lan` site now reaches lab2's Caddy; `adh.homelab.lan`
   still proxies to lab1 on 3000.
4. DHCP on lab1: hand out both DNS servers and lengthen leases, so a dead lab1
   only stops new devices from joining for days, not hours. Stop AdGuard,
   edit `AdGuardHome.yaml`, start it:

   ```yaml
   dhcp:
     dhcpv4:
       lease_duration: 604800
       options:
         - "6 ips 192.168.1.3,192.168.1.201"
   ```

   Verify on a client after it renews (`nmcli dev show`, `ipconfig /all`):
   both addresses listed. The router's own DHCP stays off.
5. On lab1: disable samba and the data guard, keep the heartbeat with an
   empty `WATCHDOG_DATA_ROOT` and `WATCHDOG_DNS_SERVER=127.0.0.1`. Its
   healthchecks.io check (`lab1`) now watches DNS. Add `hosts/lab1/stacks.txt`
   (`adguardhome`, `unbound`, `adguardhome-sync`), point `docs/samba.md` at
   `.201`, and rewrite the machine block of `hosts/lab1/README.md` for its DNS
   role.
6. Uptime Kuma (on lab2): add DNS monitors against `192.168.1.3` and
   `192.168.1.201`.

## Verification

Everything below must pass before the old state on lab1 is deleted.

- `make status` on lab2: every stack healthy.
- Sonarr, Radarr, Prowlarr, qBittorrent through gluetun; a test grab imports
  as a hardlink (link count 2).
- Jellyfin plays with a hardware transcode.
- Immich, Paperless (open a document), Navidrome, Seerr, Homepage tiles.
- Restic: a manual backup from lab2 succeeds, and `restic check` passes.
- Healthchecks.io: both `lab1` and `lab2` green.
- Scheduled jobs on lab2, `systemctl list-timers`: `batlab-restic-backup`,
  `batlab-restic-check`, `batlab-updater`, `batlab-import-audit`,
  `batlab-mediascan-sweep`, `batlab-mediascan-deep`, `batlab-watchdog-
  heartbeat`, plus `lynis` and `chkrootkit` from the batdots profile. Services:
  `batlab-mediascan-watch`, `batlab-data-guard`, `smartd`, `fail2ban`,
  `clamav-daemon`, `clamav-freshclam`, `unattended-upgrades`. On lab1 only
  `batlab-watchdog-heartbeat` and `batlab-updater` remain.
- **Failover drill**: power lab1 off; a phone still browses and
  `*.homelab.lan` still resolves. Power it back on. Then stop lab2's
  AdGuard; the same holds.

## Rollback

Until lab1's old volumes are deleted, rollback is: stop lab2, move the disk
back, point the rewrite back to `192.168.1.3`, `make up` on lab1. Anything
changed on lab2 since the cutover is lost, so decide within the first days.

## After two stable weeks

- Delete the migrated volumes from lab1's `/mnt/docker-volumes`.
- Update `CLAUDE.md` (hardware limits, SSH aliases: `my-homelab` to lab2,
  a new alias for lab1) and the registry in `hosts/lab1/README.md`.
