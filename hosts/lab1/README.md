# lab1

Every setting in this repository that exists because of this machine's
hardware, and nothing else. On a new host, copy this directory to
`hosts/<name>/`, walk the table, set `HOST_PROFILE=<name>` in `compose/.env`, and
the shared code needs no changes.

## The machine

```
CPU    AMD A8-4500M (2012), 4 cores @ 1.9 GHz, AVX but no AVX2
GPU    Radeon HD 7640G integrated, unusable for compute or transcode
RAM    15 GB
POWER  laptop with no battery: any mains blip is an instant power-off
DISK   207 GB root SSD; 4 TB SMR data disk in a USB enclosure (JMicron JMS578)
NIC    100 Mbit
```

Measured: `qwen3:4b` at 1.09 tokens/s; a 10 s 1080p x265 sample decode takes
31 s and moves the CPU from 79 to 87 C; sustained load holds 82-91 C and on
2026-09-13 ended in an unlogged power-off.

## Registry

| Setting | Where | lab1 value | Why | On stronger hardware |
| --- | --- | --- | --- | --- |
| Container CPU and memory limits | `compose.env`, read by 7 stacks | see file | 4 cores, 15 GB shared by ~20 containers | Raise or remove; values are required, so pick per host |
| Ollama resident models and parallel requests | `compose.env` | `1` and `1` | vision model alone is ~6 GB | Raise with RAM |
| Ollama and Paperless AI models, request timeout | `compose/.env` | `qwen3:4b`, 600 s | 1.09 tokens/s | Larger models, shorter timeout |
| Paperless LLM OCR | `compose/.env` | off | 10-25 min per page | Turn on, see `.env.example` |
| CPU temperature alert | `netdata/cpu-temperature.conf` | critical above 85 C on `k10temp` | powered off from sustained heat | Sensor name differs per CPU (`coretemp` on Intel); keep an alert, adjust the line |
| Netdata docker collector | `netdata/go.d-docker.conf` | off | each poll walks containerd on Docker 29; held two cores at 75% and the CPU at 85 C | Try it enabled and watch `app.dockerd` CPU |
| Deep media pass limits | `systemd/batlab-mediascan-deep.service.d/limits.conf` | 1 core, stop above 85 C | decode heats the chassis past its limit | Drop the file, or loosen it |
| Photo organiser and import temperature guard | command-line flags | `organize-media.py plan --max-cpu-temp 85 --cpu-temp-sensor k10temp`; `import-drive.sh <drive> --max-cpu-temp 88 --cpu-temp-sensor k10temp` | metadata reading and transfers run for hours | Drop the flags |
| Cleanuparr schedules | Cleanuparr database, see `cleanuparr.md` | queue 10 min, malware 5 min, seeker 30 min | CPU and heat | Defaults are fine |
| Data disk on USB 2.0 port | physical | USB 2.0 port, not the blue ones | the JMS578 bridge resets under sustained writes over USB 3 | Use SATA or a proper enclosure |
| SMART monitoring | `smartd.conf` | three disks by serial; data disk warns at 50 C, SSDs at 55 C | USB bridges rename disks between reboots, and the enclosures refuse offline tests | Copy the file, put your own disks' `/dev/disk/by-id` names in it |
| No battery | physical | healthchecks.io heartbeat catches outages | mains blips power it off | A UPS; keep the heartbeat |

Not listed because they are right on any machine: background jobs at low
priority, the data disk guard, the heartbeat, backup pings, Blocklist Sync, and
the quiet boot CPU alert in `compose/netdata/alerts/cpu.conf`.

## Installing on lab1

`compose.env` and the Netdata files are picked up automatically once
`HOST_PROFILE=lab1` is set. The systemd drop-in is installed by hand:

```bash
sudo install -d /etc/systemd/system/batlab-mediascan-deep.service.d
sudo install -m 644 hosts/lab1/systemd/batlab-mediascan-deep.service.d/limits.conf \
  /etc/systemd/system/batlab-mediascan-deep.service.d/
sudo systemctl daemon-reload
```
