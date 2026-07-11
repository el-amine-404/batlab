# Homelab Installation Guide

the Makefile was created so that the installation, setup and management of the various
docker servcies is easier. contributions are welcome to make it even better

- clone and cd to the repo
```bash
git clone https://github.com/el-amine-404/batlab && cd batlabe
```
- create the required directories, symlink the configs folder to the `$CONFIGS_ROOT`, fix ownership
```bash
make setup
```
- create the docker network used by the services
```bash
make network
```
- run the compose for one stack or all the stacks
```bash
make up STACK=<name>
```

### Configure secrets

Create your env file from the template and fill in your private values for things
like: `HOST_IP`, `WIREGUARD_PRIVATE_KEY`, `IMMICH_DB_PASSWORD`, ...
```bash
cp compose/.env.example compose/.env
```
