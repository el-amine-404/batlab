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
