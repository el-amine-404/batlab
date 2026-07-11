# Static IP

Give the server a static IP and reserve it on your router (DHCP reservation).
For Debian, edit `/etc/network/interfaces` (find the interface with `ip addr`):

```txt
auto enp5s0
iface enp5s0 inet static
  address 192.168.1.3
  netmask 255.255.255.0
  gateway 192.168.1.1
  dns-nameservers 127.0.0.1
```

Then `sudo systemctl restart networking`.