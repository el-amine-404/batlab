# 4. Static IP

Pick an address outside the DHCP range (`.4`-`.200`) that nothing uses:
lab1 is `192.168.1.3`, lab2 is `192.168.1.201`.

Replace the interface's block in `/etc/network/interfaces` (name from
`ip -br a`):

```txt
auto enp0s31f6
iface enp0s31f6 inet static
  address 192.168.1.201/24
  gateway 192.168.1.1
```

Set DNS directly: `dns-nameservers` in that file is ignored unless the
`resolvconf` package is installed, which Debian does not do by default.

```bash
printf 'nameserver 127.0.0.1\nnameserver 192.168.1.3\n' | sudo tee /etc/resolv.conf
sudo systemctl restart networking
```

`127.0.0.1` answers once AdGuard runs on this host; until then the second
line resolves. Update `HostName` in your laptop's `~/.ssh/config` and
reconnect.

## Time

Debian's `systemd-timesyncd` is already running and asks the Debian pool for
the time. Pin nearby servers so both hosts agree, and set the time zone that
`TZ` in `compose/.env` uses, so host logs line up with container logs:

```bash
sudo install -d /etc/systemd/timesyncd.conf.d
printf '[Time]\nNTP=ma.pool.ntp.org time.cloudflare.com\nFallbackNTP=0.debian.pool.ntp.org 1.debian.pool.ntp.org\n' \
  | sudo tee /etc/systemd/timesyncd.conf.d/batlab.conf
sudo timedatectl set-timezone Africa/Casablanca
sudo systemctl restart systemd-timesyncd
timedatectl timesync-status   # Server: one of the above; then `timedatectl` shows synchronized: yes
```

After a long time offline, timesyncd backs off to polling every ~34 minutes;
the restart makes it sync at once.
