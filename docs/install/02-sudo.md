# 2. sudo

Skip if `id` already lists `sudo`. Otherwise, on the server:

```bash
su --login
apt update && apt install sudo
adduser potato sudo
exit
```

Log out and back in for the group to apply.

Add `potato` to `adm` as well, so `journalctl` shows the system's logs without
`sudo`:

```bash
sudo usermod -aG adm potato
```
