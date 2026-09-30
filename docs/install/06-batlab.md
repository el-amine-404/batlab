# 6. batlab

Clone to the path the systemd units expect:

```bash
git clone https://github.com/el-amine-404/batlab /home/potato/batlab
cd /home/potato/batlab
```

Secrets: copy the template and fill it, or copy `compose/.env` from an
existing server. Set `HOST_PROFILE` to this machine's `hosts/<profile>/`.

```bash
cp compose/.env.example compose/.env
editor compose/.env
```

Create the directories and network, validate, then start:

```bash
make setup
make network
make config
make up STACK=<name>
```

`STACK` is a directory in `compose/`; `SERVICE` is one container inside it:
`make up STACK=arr` starts the whole arr stack, `make restart STACK=arr
SERVICE=sonarr` only Sonarr.

Host services (backups, watchdog, media scanning, updater) are installed from
the README in each of `restic/`, `watchdog/`, `mediascan/`, `import-audit/`,
`updater/`.
