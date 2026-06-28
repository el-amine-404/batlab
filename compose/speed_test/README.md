# SPEED TEST

- [https://www.speedtest.net/apps/cli](https://www.speedtest.net/apps/cli)

This container is used to test the network speed inside my homelab
I need to find a better solution but for now i will go with the OKLA speed test cli

```bash
cd /srv/docker-compose/speed_test/
```

```bash
docker compose run --rm speedtest
```
