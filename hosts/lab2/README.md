# lab2

Every setting in this repository that exists because of this machine's
hardware, and nothing else. lab2 runs every stack except `adguardhome-sync`
(`stacks.txt`). lab1 keeps the primary DNS; both hosts serve DHCP on their
own half of the range, see `docs/migration-lab2.md`.

## The machine

```
CPU    Intel Core i5-8350U (2017), 4 cores / 8 threads, 1.7-3.6 GHz, Tjmax 100 C
GPU    Intel UHD 620 (Quick Sync), /dev/dri/card0 and renderD128, render gid 992
RAM    31 GB
POWER  laptop battery at 32% health: rides out a short mains blip, not an outage
DISK   256 GB SanDisk SATA SSD (root); the 4 TB data disk moves from lab1
NIC    Intel I219-LM, 1 Gbit, enp0s31f6, static 192.168.1.201
```

## Registry

| Setting | Where | lab2 value | Why | Revisit |
| --- | --- | --- | --- | --- |
| Container CPU and memory limits | `compose.env` | lab1's, with Ollama at 6 CPUs / 12 GB and Immich ML at 4 GB | twice lab1's RAM and threads | after a week of Netdata |
| Render group for Jellyfin | `compose.env`, `RENDER_GID` | `992` | Debian assigns the gid at install | on reinstall |
| Stacks on this host | `stacks.txt` | all but `adguardhome-sync` | lab1 holds the AdGuard settings and pushes them here | when a stack moves |
| DHCP range | `AdGuardHome.yaml`, `dhcp` | `.101`-`.200`, 7-day leases, DNS `.201` then `.3` (each host lists itself first) | lab1 serves `.4`-`.100`; ranges must never overlap | if a third DHCP host joins |
| CPU temperature alert | `netdata/cpu-temperature.conf` | warn above 90 C, critical above 95 C on `coretemp` | Tjmax 100 C, thin chassis | after the first heavy week |
| Netdata docker collector | `netdata/go.d-docker.conf` | on, every 10 s | trial: on lab1 it cost most of two cores | watch `app.dockerd` CPU; turn off with `jobs: []` |
| Deep media pass limits | `systemd/batlab-mediascan-deep.service.d/limits.conf` | 2 threads, stop above 90 C | decode heats a thin chassis | loosen after a week |
| Data disk without UAS | `modprobe.d/usb-quirks.conf` | `usb-storage quirks=152d:0578:u` | the JMS578 enclosure moves with the disk; known to reset under UAS | after the USB 3 test in `docs/migration-lab2.md` |
| Data disk port | physical | blue USB 3 port, `usb-storage` | passed 2026-10-01 at 5000M (100 GB written at 172 MB/s). It then reset on 2026-10-03, re-enumerated ~8 times, and from 2026-10-10 12:15 every read timed out (`ASC=0x44`, 383 I/O errors in a week); lab2 has no USB 2 port | a different enclosure (ASMedia bridge) would allow USB 3 again |
| lab1 stand-in | `systemd/batlab-lab1-standin.service` | lab2 also holds `192.168.1.3` while lab1 is off (since 2026-10-10) | leases list `.3` as first DNS; the TV's video player never tries the second | **before powering lab1 on:** `sudo systemctl disable --now batlab-lab1-standin` |
| Wi-Fi backup | `network/wlp2s0`, `sysctl.d/90-wifi-backup.conf`, `setup-wifi-backup.sh` | Intel 8265 on `a_5` at `192.168.1.202`, routes at metric 600; `WATCHDOG_WIRED_IFACE=enp0s31f6` fails the heartbeat without the cable | on 2026-10-05 the cable was out for five days and the alerts went with it; over Wi-Fi they still leave, and SSH works on `.202`. Not a failover for the house: clients use `.201` | if lab2 moves away from the router, check `journalctl -u batlab-wifi-log` for signal |
| Wi-Fi link log | `systemd/batlab-wifi-log.{service,timer}`, `watchdog/scripts/wifi-log.sh` | one line a minute: SSID, band, signal, bitrates | a record to compare against when a device has Wi-Fi trouble | - |
| USB 2 only | `systemd/batlab-usb2-only.service`, `watchdog/scripts/usb2-only.sh` | every root-hub USB 3 port disabled at boot, so devices connect at 480M (~35 MB/s) | the JMS578 bridge hangs under USB 3; installed by `recover-data-disk.sh --usb2` | drop with a new enclosure: `systemctl disable batlab-usb2-only` |
| SMART monitoring | `smartd.conf` | root SSD and data disk by serial | USB names move between reboots | check both with `smartctl -a` once |
| Battery | BIOS | charge limit ~80% if offered, power on after AC loss | a worn battery kept at 100% swells | check for swelling now and then |

## Installing on lab2

`compose.env`, `stacks.txt` and the Netdata files are picked up automatically
once `HOST_PROFILE=lab2` is set. The rest is installed by hand:

```bash
sudo install -d /etc/systemd/system/batlab-mediascan-deep.service.d
sudo install -m 644 hosts/lab2/systemd/batlab-mediascan-deep.service.d/limits.conf \
  /etc/systemd/system/batlab-mediascan-deep.service.d/
sudo systemctl daemon-reload
sudo install -m 644 hosts/lab2/modprobe.d/usb-quirks.conf /etc/modprobe.d/
sudo update-initramfs -u   # the USB drivers load from the initramfs; without this the quirk is ignored
sudo apt install smartmontools
sudo cp /etc/smartd.conf /etc/smartd.conf.packaged
sudo install -m 644 hosts/lab2/smartd.conf /etc/smartd.conf
sudo systemctl restart smartmontools.service
```
