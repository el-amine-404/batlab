# Standalone tool targets; do not require the homelab's secrets/network/profile.
REPAIR_ROOT ?= $(HOME)/video-repair/cases
CASE ?= default
export REPAIR_CASE_DIR := $(abspath $(REPAIR_ROOT)/$(CASE))
export REPAIR_UID := $(shell id -u)
export REPAIR_GID := $(shell id -g)
export BROKEN REFERENCE CANDIDATE FPS MAX_REFERENCES REPAIR_TIMEOUT
REPAIR_DC = docker compose -f "$(REPO_ROOT)/compose/untrunc/docker-compose.yml"

.PHONY: untrunc-init untrunc-build untrunc-config untrunc-inventory untrunc-repair untrunc-auto untrunc-verify untrunc-reframe untrunc-help
untrunc-help:
	@echo 'Saved workflows: untrunc-case-scan / untrunc-case-suspects / untrunc-case-prepare / untrunc-case-run'
	@echo 'Whole folder:    untrunc-case-scan, then untrunc-case-suspects (preview), then untrunc-case-batch [CASE_ARGS="--limit 5"]'
	@echo 'REPAIR_CONFIG=$(HOME)/.config/batlab/case.json is the default replay config.'
	@echo 'Optional recipe-based cleanup: untrunc-case-clean ALLOW_TRIM=1 (requires a private recipe)'
	@echo 'Local agent: untrunc-agent-install / untrunc-agent-setup / untrunc-agent-model / untrunc-agent'
	@echo 'CASE=name REPAIR_ROOT=/path select an external case directory.'
	@echo 'untrunc-init      Create input/, references/, work/ for a case'
	@echo 'untrunc-build     Build pinned Untrunc plus FFmpeg/Python tools'
	@echo 'untrunc-inventory Probe input and reference files'
	@echo 'untrunc-repair    BROKEN=file.mp4 REFERENCE=good.mp4 (one attempt)'
	@echo 'untrunc-auto      BROKEN=file.mp4 [FPS=30] [MAX_REFERENCES=3]'
	@echo 'untrunc-verify    CANDIDATE=attempt/file.mp4 (relative to work/)'
	@echo 'untrunc-reframe   CANDIDATE=attempt/file.mp4 FPS=30 (H.264 only)'
	@echo 'All attempts are separate. No automatic trimming or source replacement.'

untrunc-init:
	@mkdir -p "$(REPAIR_CASE_DIR)/input" "$(REPAIR_CASE_DIR)/references" "$(REPAIR_CASE_DIR)/work" "$(REPAIR_CASE_DIR)/recipes"
	@printf 'Case directory: %s\n' "$(REPAIR_CASE_DIR)"

untrunc-build:
	$(REPAIR_DC) build untrunc

untrunc-config:
	$(REPAIR_DC) config --quiet

untrunc-inventory untrunc-repair untrunc-auto untrunc-verify untrunc-reframe:
	$(REPAIR_DC) run --rm --no-deps untrunc $(patsubst untrunc-%,%,$@)

REPAIR_CONFIG ?= $(HOME)/.config/batlab/case.json
AGENT_CONFIG ?= untrunc/agent/local.json
CASE_ARGS ?=
export SCAN_ROOT SCAN_MODE SCAN_HOST_ROOT
.PHONY: untrunc-case-prepare untrunc-case-scan untrunc-case-suspects untrunc-case-batch untrunc-case-run untrunc-agent-setup untrunc-agent-model untrunc-agent
untrunc-case-prepare untrunc-case-scan untrunc-case-suspects untrunc-case-batch untrunc-case-run untrunc-case-clean:
	python3 "$(REPO_ROOT)/untrunc/scripts/case.py" $(patsubst untrunc-case-%,%,$@) --config "$(REPAIR_CONFIG)" $(CASE_ARGS)
untrunc-agent-setup untrunc-agent-model untrunc-agent:
	python3 "$(REPO_ROOT)/untrunc/scripts/case.py" $(patsubst untrunc-%,%,$@) --config "$(AGENT_CONFIG)"

.PHONY: untrunc-case-clean
export ALLOW_TRIM

.PHONY: untrunc-agent-install untrunc-test
untrunc-agent-install:
	bash "$(REPO_ROOT)/untrunc/agent/install.sh"
untrunc-test:
	python3 -m unittest discover -s "$(REPO_ROOT)/untrunc/tests" -v
