# Druid 35.0.0 harness cluster lifecycle. On-demand only — no auto-start.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

COMPOSE := docker compose -f docker/docker-compose.yml
VENV ?= /home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/venv
PYTEST := $(VENV)/bin/pytest
HARNESS_ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

.PHONY: up down reset status logs test help

help:
	@echo "Druid 35.0.0 query harness"
	@echo "  make up      Start the cluster and wait until SQL is accepted"
	@echo "  make down    Stop and remove containers (volumes kept; RAM freed)"
	@echo "  make reset   Stop containers and delete metadata + deep storage"
	@echo "  make status  Show compose ps and health"
	@echo "  make logs    Follow container logs"
	@echo "  make test    Bring cluster up, run pytest, tear down"

up:
	mkdir -p "$(HARNESS_ROOT)data/generated"
	$(COMPOSE) up -d --build
	"$(HARNESS_ROOT)scripts/wait_healthy.sh"

down:
	$(COMPOSE) down

reset:
	$(COMPOSE) down -v
	rm -rf "$(HARNESS_ROOT)data/generated"
	mkdir -p "$(HARNESS_ROOT)data/generated"

status:
	@$(COMPOSE) ps
	@echo
	@if curl -sf --max-time 3 http://127.0.0.1:8888/status/health >/dev/null; then \
		echo "Router:      healthy (http://127.0.0.1:8888)"; \
	else \
		echo "Router:      down"; \
	fi
	@if curl -sf --max-time 3 http://127.0.0.1:8081/status/health >/dev/null; then \
		echo "Coordinator: healthy (http://127.0.0.1:8081)"; \
	else \
		echo "Coordinator: down"; \
	fi

logs:
	$(COMPOSE) logs -f --tail=200

test:
	$(MAKE) up
	set +e; \
	"$(PYTEST)" "$(HARNESS_ROOT)tests" -v; \
	status=$$?; \
	$(MAKE) down; \
	exit $$status
