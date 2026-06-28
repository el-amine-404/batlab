# Homelab Installation Guide

Follow these steps to deploy and run the homelab services from scratch.

---

## 1. Prerequisites

### A. OS Configuration
Ensure you are running a clean installation of **Debian 12/13** (or a similar Debian/Ubuntu derivative) with `sudo` configured for your user:
```bash
su --login
apt update && apt install sudo
adduser <your-username> sudo
exit
```
*(Log out and back in to apply group changes).*

### B. Machine Management & Dotfiles Setup
Use [batdots](https://github.com/el-amine-404/batdots) to automate the system-level provisioning:
```bash
git clone https://github.com/el-amine-404/batdots.git ~/dotfiles
cd ~/dotfiles
make setup
```
*Note: Make sure to review and configure variables in `local/env.sh` (e.g., your backup paths and SSH configurations).*

---

## 2. Server Provisioning

Run the `homelab` profile on the server to install Docker Engine, set up network tools, create the required directories, and apply correct permissions:
```bash
cd ~/dotfiles
make bootstrap PROFILE=homelab
```

This will automatically trigger:
* **`homelab-dirs`:** Creates all the folder structures in `/srv/docker-compose/`, `/srv/sites/`, and `/mnt/docker-volumes/` and gives ownership to your non-root user.
* **`docker`:** Installs Docker and Docker Compose plugin and adds your user to the `docker` group.

---

## 3. Clone & Configure `batlab`

The repo is config-only: everything to deploy lives under `compose/`. The compose
files reference their configs by absolute path (`CONFIGS_ROOT=/srv/docker-compose`),
so expose `compose/` at that path with a symlink — no copying, and `git pull`
updates the live config in place.

```bash
git clone git@github.com:el-amine-404/batlab.git ~/batlab

# Expose the compose tree at the absolute path the .env expects.
sudo ln -sfn ~/batlab/compose /srv/docker-compose

cd ~/batlab
```

### Configure secrets

Create your env file from the template and fill in your private values:
```bash
cp compose/.env.example compose/.env
```
At minimum set:
* `WIREGUARD_PRIVATE_KEY`
* `IMMICH_DB_PASSWORD`
* Your server's static `HOST_IP`

`compose/.env` is gitignored and is loaded automatically by the `Makefile`
(`--env-file compose/.env`), so no per-service symlinks are needed.

---

## 4. Spin Up the Services

Use the `Makefile` from the repo root — it creates the `home_server` network and
brings up every stack with the shared env file:

```bash
make up            # all stacks
make ps            # what's running
make logs STACK=jellyfin
```

Single stack, pulls, validation:
```bash
make up STACK=arr      # just the VPN-routed download stack
make pull              # refresh all images
make config            # validate every compose file
make down              # stop everything
```

> The **`arr`** stack is one compose file defining the VPN gateway *and* every
> container routed through it (qBittorrent + Sonarr/Radarr/Lidarr/Readarr/
> Prowlarr/Bazarr/Recyclarr/FlareSolverr).

Prefer raw compose? Run it with the shared env file, e.g.
`docker compose --env-file compose/.env -f compose/caddy/docker-compose.yml up -d`.

---

## 5. Host Preparation (reference)

### Static IP
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

### Free up port 80 (Caddy needs it)
```bash
sudo systemctl disable --now apache2   # if installed
sudo ss -tulpn | grep :80              # confirm nothing else is listening
```
