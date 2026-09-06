SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
BASE_URL ?= http://127.0.0.1:8088
MEMORY_SERVICE_IMAGE ?= comp-agent-memory-service:v1-candidate
RUN_ID ?=
.PHONY: init build test up up-offline contract smoke eval-init baseline-init data eval-locomo eval-memops report down clean-run bundle experiment
init:
	git submodule update --init --recursive
	node -e 'if(Number(process.versions.node.split(".")[0])!==24)throw Error("Use Node 24.18.0: nvm use")'
	cd service && npm ci
	cd eval && npm ci
build:
	cd service && npm run build
	cd eval && npm run build
	docker build -t "$(MEMORY_SERVICE_IMAGE)" service
	docker build -t comp-agent-memory-eval:dev eval
test:
	cd service && npm run build && npm test
	cd eval && npm run build && npm test
	python3 -m unittest discover -s scripts/probes -p 'test_experiment_*.py'
	python3 -m unittest discover -s scripts/probes -p 'test_delivery_gates.py'
eval-init:
	python3 -m venv eval/.venv
	eval/.venv/bin/python -m pip install -r eval/python/requirements.lock
baseline-init:
	cd service/baseline && npm ci && node prepare-u1.mjs
	cd service/baseline && node --import tsx --test ingestion-guard.test.ts
	cd service/baseline && MEM0_TELEMETRY=false MEM0_DIR=.data/config node --import tsx parity.ts
data:
	cd eval && npm run build && python3 scripts/download-data.py
experiment:
	python3 scripts/run-experiment.py --campaign "$(CAMPAIGN)" --profile "$(PROFILE)" --port "$(PORT)" $(EXPERIMENT_ARGS)
up:
	docker compose up -d --wait --wait-timeout 90
up-offline:
	MEMORY_CONFIG_FILE=configs/release-offline.env MEMORY_VOLUME=comp-agent-memory-offline-v1 docker compose up -d --wait --wait-timeout 90
contract:
	cd eval && node dist/cli.js contract --base-url "$(BASE_URL)"
smoke: contract
	cd eval && node --env-file-if-exists=../.env dist/cli.js run --data fixtures/smoke.json --run-id "smoke-$$(date +%s)" --base-url "$(BASE_URL)" --concurrency 2
# Explicit model and benchmark mode are passed with EVAL_ARGS.
eval-locomo:
	cd eval && node --env-file-if-exists=../.env dist/cli.js run --data .data/locomo-test.json --run-id "$(or $(RUN_ID),locomo-$$(date +%s))" --base-url "$(BASE_URL)" $(EVAL_ARGS)
eval-memops:
	cd eval && node --env-file-if-exists=../.env dist/cli.js run --data .data/memops-test.json --run-id "$(or $(RUN_ID),memops-$$(date +%s))" --base-url "$(BASE_URL)" $(EVAL_ARGS)
report:
	test -n "$(RUN_ID)"
	cd eval && node dist/cli.js report --directory "artifacts/$(RUN_ID)"
down:
	docker compose down
clean-run:
	python3 scripts/clean-run.py "$(RUN_ID)"
bundle:
	python3 scripts/bundle.py
