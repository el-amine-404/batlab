# lab2

Every setting in this repository that exists because of this machine's
hardware, and nothing else. lab2 runs every stack except `adguardhome-sync`
(`stacks.txt`); lab1 keeps DHCP and the primary DNS, see
`docs/migration-lab2.md`.

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
| CPU temperature alert | `netdata/cpu-temperature.conf` | warn above 90 C, critical above 95 C on `coretemp` | Tjmax 100 C, thin chassis | after the first heavy week |
| Netdata docker collector | `netdata/go.d-docker.conf` | on, every 10 s | trial: on lab1 it cost most of two cores | watch `app.dockerd` CPU; turn off with `jobs: []` |
| Deep media pass limits | `systemd/batlab-mediascan-deep.service.d/limits.conf` | 2 threads, stop above 90 C | decode heats a thin chassis | loosen after a week |
| Data disk without UAS | `modprobe.d/usb-quirks.conf` | `usb-storage quirks=152d:0578:u` | the JMS578 enclosure moves with the disk; known to reset under UAS | after the USB 3 test in `docs/migration-lab2.md` |
| Data disk port | physical | blue USB 3 port, `usb-storage` at 5000M | passed 2026-10-01 with no reset or I/O error in the kernel log: 100 GB written at 172 MB/s, falling to ~36 MB/s once the SMR cache fills; under UAS the same test wrote at 147 MB/s and read at ~200 MB/s | move to USB 2 if the kernel log ever shows a reset |
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
