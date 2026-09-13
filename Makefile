# -----------------------------------------------------------------------------
# Homelab
# -----------------------------------------------------------------------------

ENV ?= compose/.env
NET ?= home_server
# Pinned so the subnet survives a Docker reinstall: several services declare
# static addresses in it, and an unpinned network gets whatever pool is free.
NET_SUBNET ?= 172.19.0.0/16
NET_GATEWAY ?= 172.19.0.1

# Capacity limits sized for one machine live in hosts/<profile>/compose.env and
# override nothing in $(ENV); compose refuses to render without them.
HOST_PROFILE ?= $(shell sed -n 's/^HOST_PROFILE=//p' $(ENV) 2>/dev/null)
HOST_ENV := hosts/$(HOST_PROFILE)/compose.env

# Image versions are tracked in git and bumped by Renovate; secrets stay in $(ENV).
VERSIONS_ENV := compose/versions.env

DC := docker compose --env-file $(VERSIONS_ENV) --env-file $(ENV) --env-file $(HOST_ENV)

# Auto-discover stacks
STACKS := $(sort $(patsubst compose/%/docker-compose.yml,%,$(wildcard compose/*/docker-compose.yml)))
TARGETS := $(if $(STACK),$(STACK),$(STACKS))

# Storage
# Repository root (directory containing this Makefile)
REPO_ROOT := $(realpath $(dir $(lastword $(MAKEFILE_LIST))))

VOLUMES_ROOT ?= /mnt/docker-volumes
MEDIA_ROOT   ?= /mnt/storage
DATA_ROOT    ?= $(MEDIA_ROOT)/data
CONFIGS_ROOT ?= /srv/docker-compose
SITES_ROOT   ?= /srv/sites

PUID := $(shell id -u)
PGID := $(shell id -g)

.PHONY: help list setup network up down restart pull config logs ps check-stack check-all-confirm

define compose-loop
@for s in $(TARGETS); do \
	if [ -n "$(SERVICE)" ]; then \
		if [ "$(1)" = "down" ]; then \
			echo "==> rm -s -f $$s (service: $(SERVICE))"; \
			$(DC) -f compose/$$s/docker-compose.yml rm -s -f $(SERVICE) || exit 1; \
		else \
			echo "==> $(1) $$s (service: $(SERVICE))"; \
			$(DC) -f compose/$$s/docker-compose.yml $(1) $(2) $(SERVICE) || exit 1; \
		fi; \
	else \
		echo "==> $(1) $$s"; \
		$(DC) -f compose/$$s/docker-compose.yml $(1) $(2) || exit 1; \
	fi; \
done
endef

help:
	@echo ""
	@echo "Maa qall wa dall"
	@echo ""
	@echo "  make list       - List all discovered stacks"
	@echo "  make setup      - Create base directories and symlinks"
	@echo "  make up         - Start stacks (STACK=<name> [SERVICE=<name>])"
	@echo "  make down       - Stop and remove stacks (STACK=<name> [SERVICE=<name>])"
	@echo "  make restart    - Restart stacks (STACK=<name> [SERVICE=<name>])"
	@echo "  make pull       - Pull new images (STACK=<name> [SERVICE=<name>])"
	@echo "  make config     - Validate Compose file (STACK=<name> [SERVICE=<name>])"
	@echo "  make logs       - View container logs (STACK=<name> [SERVICE=<name>])"
	@echo "  make check-env  - Check if the environment file exists"
	@echo "  make network    - Create the Docker network if it doesn't exist"
	@echo "  make ps         - List running containers"
	@echo "  make status     - Show status of all stacks or a specific stack (STACK=<name>)"
	@echo "  make update     - Pull and recreate stacks (STACK=<name>)"
	@echo "  make shell      - Open a shell in a container (STACK=<name> [SERVICE=<name>])"
	@echo "  make validate   - Validate that all compose directories have a docker-compose.yml"
	@echo "  make clean      - Prune unused docker images, containers, and volumes"
	@echo "  make new        - Create a new stack template (STACK=<name>)"
	@echo ""

confirm:
	@printf "Are you sure? [y/N] "; \
	read ans; \
	[ "$$ans" = "y" ] || [ "$$ans" = "Y" ]

list:
	@printf "%s\n" $(STACKS)

check-env:
	@test -f $(ENV) || (echo "Missing $(ENV)" && exit 1)
	@test -n "$(HOST_PROFILE)" || (echo "HOST_PROFILE is not set in $(ENV)" && exit 1)
	@test -f $(HOST_ENV) || (echo "Missing $(HOST_ENV)" && exit 1)
	@test -f $(VERSIONS_ENV) || (echo "Missing $(VERSIONS_ENV)" && exit 1)

check-all-confirm:
	@if [ -z "$(STACK)" ]; then \
		printf "No STACK specified. This will apply to ALL stacks. Are you sure? [y/N] "; \
		read ans; \
		[ "$$ans" = "y" ] || [ "$$ans" = "Y" ] || { echo "Aborted."; exit 1; }; \
	fi

check-stack: check-env
	@if [ -n "$(STACK)" ]; then \
		if [ ! -f "compose/$(STACK)/docker-compose.yml" ]; then \
			echo "Error: Stack '$(STACK)' does not exist."; \
			exit 1; \
		fi; \
		if [ -n "$(SERVICE)" ]; then \
			services="$$($(DC) -f compose/$(STACK)/docker-compose.yml config --services 2>/dev/null)"; \
			if ! echo "$$services" | grep -qx "$(SERVICE)"; then \
				echo "Error: Service '$(SERVICE)' does not exist in stack '$(STACK)'."; \
				if [ -n "$$services" ]; then \
					echo "Available services in '$(STACK)':"; \
					echo "$$services" | sed 's/^/  - /'; \
				fi; \
				exit 1; \
			fi; \
		fi; \
	elif [ -n "$(SERVICE)" ]; then \
		echo "Error: You must specify STACK when specifying SERVICE."; \
		exit 1; \
	fi

network:
	@docker network inspect $(NET) >/dev/null 2>&1 || \
		docker network create --subnet $(NET_SUBNET) --gateway $(NET_GATEWAY) $(NET)
	@actual=$$(docker network inspect $(NET) --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null); \
	if [ "$$actual" != "$(NET_SUBNET)" ]; then \
		echo "Error: network '$(NET)' has subnet $$actual, expected $(NET_SUBNET)."; \
		echo "  Static addresses in the compose files will not resolve against it."; \
		echo "  Fix: make down && docker network rm $(NET) && make up"; \
		exit 1; \
	fi

up: network check-stack check-all-confirm
	$(call compose-loop,up,-d)

down: check-stack check-all-confirm
	$(call compose-loop,down)

restart: check-stack check-all-confirm
	$(call compose-loop,restart)

pull: check-stack check-all-confirm
	$(call compose-loop,pull)

config: check-stack check-all-confirm
	$(call compose-loop,config,-q)

logs: check-stack
	@test -n "$(STACK)" || (echo "Error: Use STACK=<name> for logs" && exit 1)
	$(DC) -f compose/$(STACK)/docker-compose.yml logs -f --tail=200 $(SERVICE)

ps:
	@docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

setup: check-env
	@echo "==> Creating base directories..."
	sudo mkdir -p \
		/srv \
		$(SITES_ROOT) \
		$(VOLUMES_ROOT) \
		$(DATA_ROOT)

	@echo "==> Linking compose directory..."
	sudo ln -sfnT "$(REPO_ROOT)/compose" "$(CONFIGS_ROOT)"

	@echo "==> Creating service directories..."
	sudo mkdir -p \
		$(SITES_ROOT)/caddy/site \
		$(VOLUMES_ROOT)/adguardhome/conf \
		$(VOLUMES_ROOT)/adguardhome/data \
		$(VOLUMES_ROOT)/caddy/data \
		$(VOLUMES_ROOT)/caddy/config \
		$(VOLUMES_ROOT)/diun/data \
		$(VOLUMES_ROOT)/dozzle \
		$(VOLUMES_ROOT)/filebrowser \
		$(VOLUMES_ROOT)/homepage/icons \
		$(VOLUMES_ROOT)/homepage/images \
		$(VOLUMES_ROOT)/homepage/logs \
		$(VOLUMES_ROOT)/immich/postgres \
		$(VOLUMES_ROOT)/immich/redis \
		$(VOLUMES_ROOT)/immich/model-cache \
		$(VOLUMES_ROOT)/jellyfin/conf \
		$(VOLUMES_ROOT)/jellyfin/data \
		$(VOLUMES_ROOT)/jellyfin/cache \
		$(VOLUMES_ROOT)/jellyfin/log \
		$(VOLUMES_ROOT)/netdata/conf \
		$(VOLUMES_ROOT)/netdata/lib \
		$(VOLUMES_ROOT)/netdata/cache \
		$(VOLUMES_ROOT)/navidrome/data \
		$(VOLUMES_ROOT)/portainer \
		$(VOLUMES_ROOT)/uptime-kuma/data \
		$(VOLUMES_ROOT)/bazarr \
		$(VOLUMES_ROOT)/cleanuparr \
		$(VOLUMES_ROOT)/lidarr \
		$(VOLUMES_ROOT)/prowlarr \
		$(VOLUMES_ROOT)/qbittorrent \
		$(VOLUMES_ROOT)/radarr \
		$(VOLUMES_ROOT)/readarr \
		$(VOLUMES_ROOT)/recyclarr \
		$(VOLUMES_ROOT)/sonarr \
		$(VOLUMES_ROOT)/seerr \
		$(VOLUMES_ROOT)/paperless/data \
		$(VOLUMES_ROOT)/paperless/db \
		$(VOLUMES_ROOT)/paperless/redis \
		$(VOLUMES_ROOT)/ollama \
		$(VOLUMES_ROOT)/open-webui

	@echo "==> Creating data directories..."
	sudo mkdir -p \
		$(DATA_ROOT)/immich \
		$(DATA_ROOT)/documents/media \
		$(DATA_ROOT)/documents/export \
		$(DATA_ROOT)/documents/consume

	@echo "==> Setting ownership..."
	sudo chown $(PUID):$(PGID) \
		$(SITES_ROOT)
	sudo chown -R $(PUID):$(PGID) \
		$(SITES_ROOT)/caddy/site \
		$(VOLUMES_ROOT)/caddy/data \
		$(VOLUMES_ROOT)/caddy/config \
		$(VOLUMES_ROOT)/diun/data \
		$(VOLUMES_ROOT)/filebrowser \
		$(VOLUMES_ROOT)/homepage/icons \
		$(VOLUMES_ROOT)/homepage/images \
		$(VOLUMES_ROOT)/homepage/logs \
		$(VOLUMES_ROOT)/jellyfin/conf \
		$(VOLUMES_ROOT)/jellyfin/data \
		$(VOLUMES_ROOT)/jellyfin/cache \
		$(VOLUMES_ROOT)/jellyfin/log \
		$(VOLUMES_ROOT)/navidrome/data \
		$(VOLUMES_ROOT)/bazarr \
		$(VOLUMES_ROOT)/cleanuparr \
		$(VOLUMES_ROOT)/lidarr \
		$(VOLUMES_ROOT)/prowlarr \
		$(VOLUMES_ROOT)/qbittorrent \
		$(VOLUMES_ROOT)/radarr \
		$(VOLUMES_ROOT)/readarr \
		$(VOLUMES_ROOT)/recyclarr \
		$(VOLUMES_ROOT)/sonarr \
		$(VOLUMES_ROOT)/seerr \
		$(VOLUMES_ROOT)/paperless/data \
		$(VOLUMES_ROOT)/paperless/redis \
		$(DATA_ROOT)/documents/media \
		$(DATA_ROOT)/documents/export \
		$(DATA_ROOT)/documents/consume

	@echo "==> Symlinking .env to all stacks..."
	@for d in compose/*; do \
		if [ -d "$$d" ]; then \
			ln -sf ../.env "$$d/.env"; \
		fi; \
	done

	@echo
	@echo "Setup complete."

status:
ifeq ($(STACK),)
	@printf "%-15s %-12s %s\n" "STACK" "STATUS" "DETAILS"
	@printf "%-15s %-12s %s\n" "---------------" "------------" "----------------"

	@for s in $(STACKS); do \
		services="$$($(DC) -f compose/$$s/docker-compose.yml config --services)"; \
		total=$$(printf "%s\n" "$$services" | wc -l); \
		running=0; \
		healthy=0; \
		unhealthy=0; \
		created=0; \
		for svc in $$services; do \
			cid=$$($(DC) -f compose/$$s/docker-compose.yml ps -q $$svc); \
			[ -z "$$cid" ] && continue; \
			created=$$((created+1)); \
			state=$$(docker inspect -f '{{.State.Status}}' $$cid 2>/dev/null); \
			health=$$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' $$cid 2>/dev/null); \
			if [ "$$state" = "running" ]; then \
				running=$$((running+1)); \
				if [ -z "$$health" ]; then \
					healthy=$$((healthy+1)); \
				elif [ "$$health" = "healthy" ]; then \
					healthy=$$((healthy+1)); \
				elif [ "$$health" = "unhealthy" ]; then \
					unhealthy=$$((unhealthy+1)); \
				fi; \
			fi; \
		done; \
		if [ "$$created" -eq 0 ]; then \
			status="not-created"; \
		elif [ "$$running" -eq 0 ]; then \
			status="stopped"; \
		elif [ "$$unhealthy" -gt 0 ]; then \
			status="unhealthy"; \
		elif [ "$$running" -lt "$$total" ]; then \
			status="degraded"; \
		elif [ "$$healthy" -eq "$$total" ]; then \
			status="healthy"; \
		else \
			status="running"; \
		fi; \
		printf "%-15s %-12s %d/%d running\n" \
			$$s $$status $$running $$total; \
	done

	@echo
	@echo "Hint: Use STACK=<stack> in case there is more than one service."

else

	@printf "Stack: %s\n\n" "$(STACK)"
	@printf "%-20s %s\n" "SERVICE" "STATUS"
	@printf "%-20s %s\n" "--------------------" "------------"

	@services="$$($(DC) -f compose/$(STACK)/docker-compose.yml config --services)"; \
	total=$$(printf "%s\n" "$$services" | wc -l); \
	running=0; \
	for svc in $$services; do \
		cid=$$($(DC) -f compose/$(STACK)/docker-compose.yml ps -q $$svc); \
		if [ -z "$$cid" ]; then \
			status="not-created"; \
		else \
			state=$$(docker inspect -f '{{.State.Status}}' $$cid 2>/dev/null); \
			health=$$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' $$cid 2>/dev/null); \
			if [ "$$state" != "running" ]; then \
				status="stopped"; \
			elif [ "$$health" = "healthy" ]; then \
				status="healthy"; \
				running=$$((running+1)); \
			elif [ "$$health" = "unhealthy" ]; then \
				status="unhealthy"; \
				running=$$((running+1)); \
			else \
				status="running"; \
				running=$$((running+1)); \
			fi; \
		fi; \
		printf "%-20s %s\n" $$svc $$status; \
	done

	@echo
	@printf "Summary: %d/%d services running\n" $$running $$total

endif

update: pull
	$(call compose-loop,up,-d)

shell:
	@test -n "$(STACK)" || { \
		echo "Usage: make shell STACK=<stack> [SERVICE=<service>]"; \
		echo; \
		echo "Available stacks:"; \
		printf "  %s\n" $(STACKS); \
		exit 1; \
	}

	@services="$$($(DC) -f compose/$(STACK)/docker-compose.yml config --services)"; \
	count=$$(printf "%s\n" "$$services" | wc -l); \
	service="$(SERVICE)"; \
\
	if [ -z "$$service" ]; then \
		if [ "$$count" -eq 1 ]; then \
			service="$$services"; \
		else \
			echo "Stack '$(STACK)' contains multiple services:"; \
			echo; \
			printf "  %s\n" $$services; \
			echo; \
			echo "Choose one with:"; \
			echo "  make shell STACK=$(STACK) SERVICE=<service>"; \
			exit 1; \
		fi; \
	fi; \
\
	if ! $(DC) -f compose/$(STACK)/docker-compose.yml ps --status running | grep -q "$$service"; then \
		echo "Service '$$service' is not running."; \
		echo; \
		echo "Start it with:"; \
		echo "  make up STACK=$(STACK)"; \
		exit 1; \
	fi; \
\
	if $(DC) -f compose/$(STACK)/docker-compose.yml exec "$$service" sh -c 'command -v bash >/dev/null'; then \
		exec="bash"; \
	else \
		exec="sh"; \
	fi; \
\
	echo "Opening $$exec in $(STACK)/$$service..."; \
	$(DC) -f compose/$(STACK)/docker-compose.yml exec "$$service" $$exec

validate:
	@for d in compose/*; do \
		test -f $$d/docker-compose.yml || echo "Missing compose: $$d"; \
	done

clean: confirm
	docker image prune -f
	docker container prune -f
	docker volume prune

new:
	@test -n "$(STACK)" || { \
		echo "Usage: make new STACK=<service>"; \
		exit 1; \
	}

	@test ! -d compose/$(STACK) || { \
		echo "Stack already exists."; \
		exit 1; \
	}

	@mkdir -p compose/$(STACK)/conf
	@ln -s ../.env compose/$(STACK)/.env

	@SERVICE_UPPER=$$(echo "$(STACK)" | tr '[:lower:]-' '[:upper:]_'); \
	sed \
		-e "s/__SERVICE__/$(STACK)/g" \
		-e "s/__SERVICE_UPPER__/$$SERVICE_UPPER/g" \
		templates/docker-compose.yml.tpl \
		> compose/$(STACK)/docker-compose.yml

	@echo "Created compose/$(STACK)"
