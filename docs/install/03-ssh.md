# 3. SSH

**On the server**, find its current address (the `enp...` line):

```bash
ip -br a
```

**On your laptop**, create a key once (skip if `~/.ssh/id_ed25519` exists;
press Enter at each prompt), then copy it to the server:

```bash
ssh-keygen -t ed25519
ssh-copy-id potato@<server-ip>
```

Add a short name to `~/.ssh/config`:

```
Host lab2
    HostName <server-ip>
    User potato
```

`ssh lab2` now logs in without a password.

**Turn off password logins** once the key works. Keep this session open and
test `ssh lab2` from a second terminal before closing it:

```bash
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```
