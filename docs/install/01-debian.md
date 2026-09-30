# 1. Debian

Install Debian stable. In the installer:

- **Root password: leave it empty.** The installer then gives your user sudo,
  and step 2 can be skipped.
- **First user: `potato`.** The first user gets uid 1000, which owns every
  file on the data disk and in `/mnt/docker-volumes`, and the systemd units
  run as `potato` from `/home/potato/batlab`.
- **Software selection: tick `SSH server`, untick every desktop.**
- Plug in the Ethernet cable before starting.

After the first boot, check:

```bash
id    # uid=1000(potato) gid=1000(potato) ... sudo
```
