# batlab -- homelab stack management.
#
# Each stack is a folder under compose/ with its own docker-compose.yml. This
# Makefile wraps `docker compose` so you don't hand-type per-stack -f paths.
# The shared env file (compose/.env) is passed via --env-file, so you can run
# these from the repo root without per-service .env symlinks.
#
#   make up                 # start every stack (creates the network first)
#   make down               # stop every stack
#   make pull               # pull all images
#   make config             # validate every compose file
#   make ps                 # list running containers
#   make logs STACK=jellyfin
#   make up STACK=arr       # act on a single stack
#
# Override the stack list or env path if needed:
#   make up STACKS="caddy homepage"   ENV=compose/.env

ENV    ?= compose/.env
STACKS ?= caddy unbound adguardhome homepage jellyfin arr portainer dozzle netdata uptime_kuma filebrowser gotenberg
NET    ?= home_server
DC      = docker compose --env-file $(ENV)

# When STACK=<name> is given, target just that one; otherwise the full list.
TARGETS = $(if $(STACK),$(STACK),$(STACKS))

.PHONY: help up down restart pull config ps network check-env

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

check-env:
	@test -f $(ENV) || { echo "Missing $(ENV) -- copy compose/.env.example to compose/.env first."; exit 1; }

network: ## Create the shared docker network if absent
	@docker network inspect $(NET) >/dev/null 2>&1 || docker network create $(NET)

up: check-env network ## Start stack(s) (all, or STACK=<name>)
	@for s in $(TARGETS); do echo ">> up $$s"; $(DC) -f compose/$$s/docker-compose.yml up -d || exit 1; done

down: check-env ## Stop stack(s)
	@for s in $(TARGETS); do echo ">> down $$s"; $(DC) -f compose/$$s/docker-compose.yml down; done

restart: check-env ## Restart stack(s)
	@for s in $(TARGETS); do echo ">> restart $$s"; $(DC) -f compose/$$s/docker-compose.yml restart; done

pull: check-env ## Pull images for stack(s)
	@for s in $(TARGETS); do echo ">> pull $$s"; $(DC) -f compose/$$s/docker-compose.yml pull; done

config: check-env ## Validate compose file(s)
	@for s in $(TARGETS); do printf ">> config %-12s " $$s; $(DC) -f compose/$$s/docker-compose.yml config -q && echo OK || exit 1; done

logs: check-env ## Follow logs (use STACK=<name>)
	@test -n "$(STACK)" || { echo "Set STACK=<name>, e.g. make logs STACK=jellyfin"; exit 1; }
	$(DC) -f compose/$(STACK)/docker-compose.yml logs -f --tail=200

ps: ## Show running homelab containers
	@docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
