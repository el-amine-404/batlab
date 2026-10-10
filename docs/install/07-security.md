# 7. Security

SSH hardening, firewall, fail2ban, security updates and ClamAV, set by hand.
This replaces batdots' `homelab` profile, which also changed the shell prompt
and installed tools a server does not need. Keep an SSH session open until step
1 is tested from a second terminal.

## 1. SSH

Keys only, no root login. `sshd -t` checks the file before the restart, so a
typo cannot lock you out:

```bash
sudo tee /etc/ssh/sshd_config.d/10-hardening.conf >/dev/null <<'EOF'
PermitRootLogin no
KbdInteractiveAuthentication no
X11Forwarding no
MaxAuthTries 3
LoginGraceTime 30
PubkeyAuthentication yes
PasswordAuthentication no
EOF
sudo sshd -t && sudo systemctl restart ssh
```

From the laptop, `ssh lab2` must log in and `ssh -o PubkeyAuthentication=no
lab2` must answer `Permission denied (publickey)`.

## 2. Firewall

Docker needs forwarded traffic accepted, or containers lose their network.
Ports published by containers (Caddy's 80 and 443) bypass ufw and need no rule.

```bash
sudo apt install ufw
sudo sed -i 's/^DEFAULT_FORWARD_POLICY=.*/DEFAULT_FORWARD_POLICY="ACCEPT"/' /etc/default/ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from 192.168.1.0/24 to any port 22 proto tcp     # ssh
sudo ufw allow from 192.168.1.0/24 to any port 3000 proto tcp   # AdGuard UI, and adguardhome-sync
sudo ufw allow from 192.168.1.0/24 to any port 445 proto tcp    # samba
sudo ufw allow from 192.168.1.0/24 to any port 137:138 proto udp
sudo ufw allow in from 172.19.0.0/16                            # containers -> host: Netdata, AdGuard
```

DNS, by role. A host that serves DNS but not DHCP:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 53
```

A host serving DHCP (lab1 and lab2, each on half the range) answers devices
that have no address yet, so neither rule can filter by source:

```bash
sudo ufw allow 53
sudo ufw allow 67/udp
```

Then turn it on. The Docker restart stops and starts every container and takes
a minute or two; let it finish, Ctrl+C only stops the waiting:

```bash
sudo ufw enable
sudo systemctl restart docker
```

On each DHCP host, check that a phone reconnecting to Wi-Fi still gets an
address. `sudo ufw disable` undoes everything at once.

## 3. fail2ban

```bash
sudo apt install fail2ban
sudo tee /etc/fail2ban/jail.d/homelab.local >/dev/null <<'EOF'
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5
backend  = systemd

[sshd]
enabled = true
port    = 22
EOF
sudo systemctl restart fail2ban
```

Wait a few seconds for its socket, then check; asking right away fails with
"Is fail2ban running?":

```bash
sudo fail2ban-client status sshd
```

## 4. Security updates

Debian security fixes install by themselves; nothing reboots on its own.

```bash
sudo apt install unattended-upgrades
sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
sudo tee /etc/apt/apt.conf.d/52homelab-unattended-upgrades >/dev/null <<'EOF'
Unattended-Upgrade::Origins-Pattern {
    "origin=Debian,codename=${distro_codename},label=Debian-Security";
    "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";
};
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
EOF
```

## 5. ClamAV

`mediascan` scans every download through clamd:

```bash
sudo apt install clamav-daemon clamav-freshclam
```

## Optional

Audit tools lab1 has and nothing in batlab depends on:

```bash
sudo apt install lynis rkhunter chkrootkit auditd
```
