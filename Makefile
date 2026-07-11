# -----------------------------------------------------------------------------
# Homelab
# -----------------------------------------------------------------------------

ENV ?= compose/.env
NET ?= home_server

DC := docker compose --env-file $(ENV)

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

.PHONY: help list setup network up down restart pull config logs ps

define compose-loop
@for s in $(TARGETS); do \
	echo "==> $(1) $$s"; \
	$(DC) -f compose/$$s/docker-compose.yml $(1) $(2) || exit 1; \
done
endef

help:
	@echo ""
	@echo "Maa qall wa dall"
	@echo ""
	@echo "  make list"
	@echo "  make setup"
	@echo "  make up"
	@echo "  make down"
	@echo "  make restart"
	@echo "  make pull"
	@echo "  make config"
	@echo "  make logs"
	@echo "  make check-env"
	@echo "  make network"
	@echo "  make pull"
	@echo "  make ps"
	@echo "  make status"
	@echo "  make update"
	@echo "  make shell"
	@echo "  make validate"
	@echo "  make clean"
	@echo "  make new"
	@echo ""

confirm:
	@printf "Are you sure? [y/N] "; \
	read ans; \
	[ "$$ans" = "y" ] || [ "$$ans" = "Y" ]

list:
	@printf "%s\n" $(STACKS)

check-env:
	@test -f $(ENV) || (echo "Missing $(ENV)" && exit 1)

network:
	@docker network inspect $(NET) >/dev/null 2>&1 || \
		docker network create $(NET)

up: check-env network
	$(call compose-loop,up,-d)

down: check-env
	$(call compose-loop,down)

restart: check-env
	$(call compose-loop,restart)

pull: check-env
	$(call compose-loop,pull)

config: check-env
	$(call compose-loop,config,-q)

logs: check-env
	@test -n "$(STACK)" || (echo "Use STACK=<name>" && exit 1)
	$(DC) -f compose/$(STACK)/docker-compose.yml logs -f --tail=200

ps:
	@docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

setup:
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
		$(VOLUMES_ROOT)/adguardhome/data \
		$(VOLUMES_ROOT)/caddy/data \
		$(VOLUMES_ROOT)/caddy/config \
		$(VOLUMES_ROOT)/filebrowser \
		$(VOLUMES_ROOT)/homepage/icons \
		$(VOLUMES_ROOT)/homepage/images \
		$(VOLUMES_ROOT)/homepage/logs \
		$(VOLUMES_ROOT)/jellyfin/data \
		$(VOLUMES_ROOT)/jellyfin/cache \
		$(VOLUMES_ROOT)/jellyfin/log \
		$(VOLUMES_ROOT)/netdata/lib \
		$(VOLUMES_ROOT)/netdata/cache \
		$(VOLUMES_ROOT)/portainer \
		$(VOLUMES_ROOT)/uptime-kuma/data \
		$(VOLUMES_ROOT)/bazarr \
		$(VOLUMES_ROOT)/lidarr \
		$(VOLUMES_ROOT)/prowlarr \
		$(VOLUMES_ROOT)/qbittorrent \
		$(VOLUMES_ROOT)/radarr \
		$(VOLUMES_ROOT)/readarr \
		$(VOLUMES_ROOT)/recyclarr \
		$(VOLUMES_ROOT)/sonarr

	@echo "==> Setting ownership..."
	sudo chown -R $(PUID):$(PGID) \
		"$(REPO_ROOT)" \
		$(SITES_ROOT) \
		$(MEDIA_ROOT) \
		$(VOLUMES_ROOT)

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