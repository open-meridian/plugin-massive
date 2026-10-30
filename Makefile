SHELL := /bin/bash

# A pre-push hook runs with GIT_DIR naming this repository; nothing here runs
# git on the host, and this keeps it that way should something start to.
unexport GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR \
         GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_PREFIX

.PHONY: help ci-local ci-local-deep ci-remote test lint check image e2e preview fmt install-hooks

DOCKER      := DOCKER_BUILDKIT=1 docker
PY_VERSION  := 3.12
CHECK       := meridian-massive-check
IMAGE       ?= massive:local
# The SDK pyproject.toml pins, and its base image. Where the sibling
# meridian-python checkout carries exactly that version -- one not yet
# published, being tried -- it is built from there (SDK_REPO): the tests' and
# the check's containers install it from source, and the plugin's image is
# built on a base made from it. Otherwise both come from PyPI and ghcr.io.
SDK_VERSION := $(shell sed -n 's/.*"open-meridian==\([0-9][0-9.]*\)".*/\1/p' pyproject.toml)
SDK_REPO    ?= $(if $(shell grep -s '^version = "$(SDK_VERSION)"$$' ../meridian-python/pyproject.toml),../meridian-python,)
SDK_CONTEXT := $(if $(SDK_REPO),--build-context sdk=$(SDK_REPO),)
# The released base is the Dockerfile's, by tag and digest.
BASE        ?= $(if $(SDK_REPO),plugin-python:$(SDK_VERSION)-local,$(shell sed -n 's/^ARG BASE=//p' Dockerfile))
PLUGIN_CHECK := meridian-massive-plugin-check
# The meridian check.yaml holds the plugin to, read from there so the two
# cannot drift.
MERIDIAN_VERSION := $(shell sed -n 's/^ *MERIDIAN_VERSION: *\([0-9][0-9.]*\).*/\1/p' .github/workflows/check.yaml)
# A meridian built locally (a directory holding its binary), for a release
# not yet published: `make check MERIDIAN_BIN=../cli-build`. Empty, the
# release check.yaml pins is installed.
MERIDIAN_BIN ?=
CLI_CONTEXT := $(if $(MERIDIAN_BIN),--build-context cli=$(MERIDIAN_BIN),)

# The released runtime `make e2e` proves the plugin against, by tag and
# digest, and core's plugin harness published with it, by the same commit's tag and
# its digest, since a harness and a runtime of different commits may
# disagree about the dashboard's pages. Moved together by a deliberate
# commit, when this plugin chooses. `make e2e RUNTIME_IMAGE=...:latest
# HARNESS_IMAGE=...:latest` tries a newer core; e2e-latest.yaml does that
# weekly.
# Core 8def82a (merged as bcd96cb), contract v28, as published with chart 0.1.313.
RUNTIME_IMAGE ?= ghcr.io/open-meridian/meridian-runtime:8def82a@sha256:f0565be718b588bb96fabf52a5b16a60500db985b44f480edb73b8005d8e931f
HARNESS_IMAGE ?= ghcr.io/open-meridian/meridian-harness:8def82a@sha256:763b1d04466df14ef41e17497133a4e292f30df1631494a4cc36cff03326deff
# The harness's two plugins: this one, as instance massive, holding data as
# pyproject.toml declares; and the reader, the e2e's stand-in for what reads
# prices, trades, quotes and chains (e2e/reader.py), from the same image,
# holding `custody` beside `reporting` and `signal` so it can name the
# instruments it asks about as a custodian's book would.
# The declaration [tool.meridian] names (contract v25): `make e2e` prints it
# from the image under test, as `meridian plugin upload` reads it, and the
# harness holds the plugin's registration to it (its README, `declaration`).
# Each stand-in's the same, its directory mounted as it runs.
DECLARATION_REF := $(shell sed -n 's/^declaration *= *"\(.*\)"/\1/p' pyproject.toml)
DECLARE := docker run --rm --network none --entrypoint meridian-declaration
PLUGINS := [{"instance": "massive", "image": "$(IMAGE)", "roles": ["data"], "declaration": "declaration.json"}, {"instance": "reader", "image": "$(IMAGE)", "roles": ["custody", "reporting", "signal"], "declaration": "reader.json"}]
# E2E_MINTS: the FIGIs the e2e's reader states of SPY, BRK.B and TQQQ
# (e2e/reader.py), each invented for the e2e. The harness's stand-in platform
# is told it holds them, so each listing is the platform's INS- record, which
# the lake wants (contract v24, W10.6).
E2E_MINTS := BBG00E2ESPY1,BBG00E2EBRKB,BBG00E2ETQQQ

# The kit's built files, where `make preview SHOTS=...` serves them beside
# the pages for the browser: a meridian-ui checkout's dist/.
KIT_DIST    ?= ../meridian-ui/dist
# The browser: headless Chromium driven by Playwright, as core's browser
# runs use it, at the same version (its Python package installed at run).
BROWSER     ?= mcr.microsoft.com/playwright/python:v1.63.0-noble
SHOTS       ?=

help:
	@echo "  make ci-local       every gate: lint, tests, plugin check, the plugin's image, and e2e (the pre-push gate)"
	@echo "  make ci-remote      what ci.yaml runs: lint and tests"
	@echo "  make test           the tests, in a container"
	@echo "  make lint           ruff and mypy, strict"
	@echo "  make check          what check.yaml runs: meridian plugin check --verified --run-tests, in a container"
	@echo "  make image          build the plugin's image, as upload would, and check it"
	@echo "  make e2e            the plugin, on sample data, on the plugin harness of the runtime it pins (e2e/run.py)"
	@echo "  make preview        write preview/: each page on sample data, linking the kit;"
	@echo "                      SHOTS=<dir> also checks each in a browser with the kit, at 375, 582 and 1280 px wide, and keeps a screenshot"
	@echo "  make fmt            apply the formatter"
	@echo "  make install-hooks  point git at hooks/ so push fires ci-local"

# Local green is the completion signal; CI is confirmation. Every CI job is a
# target reachable from here.
ci-local: ci-remote check image e2e
	@echo
	@echo "ci-local: GREEN"

ci-local-deep: ci-local

ci-remote: lint test
	@echo
	@echo "ci-remote: GREEN"

lint:
	@$(DOCKER) build $(SDK_CONTEXT) -f Dockerfile.check --target lint . >/dev/null 2>&1 \
		|| { echo "lint FAILED; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build $(SDK_CONTEXT) -f Dockerfile.check --target lint --progress=plain ." >&2; exit 1; }
	@echo "lint OK: ruff and mypy (strict) clean"

test:
	@$(DOCKER) build $(SDK_CONTEXT) -f Dockerfile.check --target test -t $(CHECK) . >/dev/null 2>&1 \
		|| { echo "test FAILED to build; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build $(SDK_CONTEXT) -f Dockerfile.check --target test --progress=plain ." >&2; exit 1; }
	@docker run --rm $(CHECK) python -m pytest -q -rs >.test.log 2>&1 \
		|| { echo "test FAILED. The last 40 lines, and the whole of it in .test.log:" >&2; \
		     tail -40 .test.log >&2; exit 1; }
	@echo "test OK: $$(tail -1 .test.log)"

# check.yaml's job, in a container: the meridian it pins, installed as it is
# there, holds the plugin to the framework's rules and runs its tests. The
# repository is mounted read-only, and the check writes nothing to it.
check:
	@[ -n "$(MERIDIAN_VERSION)" ] \
		|| { echo "check FAILED: .github/workflows/check.yaml pins no MERIDIAN_VERSION" >&2; exit 1; }
	@$(DOCKER) build $(SDK_CONTEXT) -f Dockerfile.check --target check $(CLI_CONTEXT) --build-arg MERIDIAN_VERSION=$(MERIDIAN_VERSION) \
		-t $(PLUGIN_CHECK) . >/dev/null 2>&1 \
		|| { echo "check FAILED to build; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build $(SDK_CONTEXT) -f Dockerfile.check --target check $(CLI_CONTEXT) --build-arg MERIDIAN_VERSION=$(MERIDIAN_VERSION) --progress=plain ." >&2; exit 1; }
	@out="$$(docker run --rm -v "$(CURDIR)":/w:ro $(PLUGIN_CHECK) meridian plugin check --verified --run-tests 2>&1)" \
		|| { echo "check FAILED:" >&2; echo "$$out" >&2; exit 1; }
	@echo "check OK: meridian $(MERIDIAN_VERSION) plugin check --verified --run-tests, every rule holds"

# The image `meridian plugin upload` builds from this Dockerfile, checked for
# what a plugin's image must be: it starts as 65532, imports itself, its pages
# and its recorded answers, and carries nothing of this repository but the
# package.
image:
	@if [ -n "$(SDK_REPO)" ]; then \
		$(MAKE) --no-print-directory -C "$(SDK_REPO)" base-image BASE_IMAGE=$(BASE) >/dev/null \
			|| { echo "image FAILED: the base for open-meridian $(SDK_VERSION) was not built from $(SDK_REPO)" >&2; exit 1; }; \
	fi
	@$(DOCKER) build --build-arg BASE=$(BASE) -t $(IMAGE) . >/dev/null 2>&1 \
		|| { echo "image FAILED; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build --build-arg BASE=$(BASE) --progress=plain ." >&2; exit 1; }
	@[ "$$(docker inspect -f '{{.Config.User}}' $(IMAGE))" = "65532" ] \
		|| { echo "image FAILED: it does not run as 65532" >&2; exit 1; }
	@docker run --rm --entrypoint python $(IMAGE) -c \
		"import meridian, massive.__main__, massive.page; from massive.synthetic import exchange; assert exchange()['daily']" \
		|| { echo "image FAILED: it does not import the SDK, itself and its synthetic exchange" >&2; exit 1; }
	@[ "$$(docker run --rm --entrypoint sh $(IMAGE) -c 'ls -A /plugin')" = "docs" ] \
		|| { echo "image FAILED: /plugin holds files beyond docs/; only the installed package and its documentation belong" >&2; exit 1; }
	@echo "image OK: $(IMAGE) on $(BASE), $$(docker image inspect -f '{{.Size}}' $(IMAGE) | awk '{printf "%.0f MB", $$1/1e6}')"

# The plugin, in synthetic mode and with no key, on the plugin harness
# published with the runtime it pins (the harness's README, in
# HARNESS_IMAGE), beside the e2e's reader: e2e/run.py says each step. Its own
# project and no published port; nothing outlives it.
e2e: image
	@rm -rf .e2e && mkdir -p .e2e
	@# A tag alone, such as latest, can move: take what the registry has now.
	@for image in $(RUNTIME_IMAGE) $(HARNESS_IMAGE); do \
		case "$$image" in *@sha256:*) ;; *) docker pull -q "$$image" >/dev/null 2>&1 || true;; esac; \
	done
	@id="$$(docker create $(HARNESS_IMAGE) none 2>>.e2e/components.log)" \
		&& docker cp "$$id:/harness" .e2e/harness >/dev/null \
		&& docker rm "$$id" >/dev/null \
		|| { echo "e2e FAILED: no plugin harness at /harness in $(HARNESS_IMAGE)" >&2; exit 1; }
	@$(DECLARE) $(IMAGE) $(DECLARATION_REF) >.e2e/harness/declaration.json 2>>.e2e/components.log \
		|| { rm -rf .e2e/harness; echo "e2e FAILED: meridian-declaration did not print $(DECLARATION_REF) (declaration.json)" >&2; exit 1; }
	@$(DECLARE) -v "$(CURDIR)/e2e":/e2e:ro -e PYTHONPATH=/e2e $(IMAGE) reader:DECLARATION >.e2e/harness/reader.json 2>>.e2e/components.log \
		|| { rm -rf .e2e/harness; echo "e2e FAILED: meridian-declaration did not print reader:DECLARATION (reader.json)" >&2; exit 1; }
	@printf '%s\n' '$(PLUGINS)' | docker run --rm -i -v "$(CURDIR)/.e2e/harness":/harness:ro python:3.12-alpine \
		python /harness/harness.py compose >.e2e/harness/plugins.yaml 2>>.e2e/components.log \
		|| { echo "e2e FAILED: the harness did not write its plugins" >&2; exit 1; }
	@# The harness's copy is removed however the run ends: `meridian plugin
	@# check` reads every source file under this directory.
	@trap 'rm -rf .e2e/harness' EXIT; \
		MERIDIAN_RUNTIME_IMAGE=$(RUNTIME_IMAGE) E2E_DIR="$(CURDIR)/e2e" \
		HARNESS_PLATFORM_MINTS=$(E2E_MINTS) python3 e2e/run.py

preview:
	@$(DOCKER) build $(SDK_CONTEXT) -f Dockerfile.check --target test -t $(CHECK) . >/dev/null 2>&1 \
		|| { echo "preview FAILED to build" >&2; exit 1; }
	@mkdir -p preview
	@for page in connection datasets connection-no-key datasets-no-key; do \
		docker run --rm $(CHECK) python -m massive.preview $$page >preview/$$page.html || exit 1; \
	done
	@echo "preview: preview/connection.html, preview/datasets.html, and the -no-key pages"
	@if [ -n "$(SHOTS)" ]; then \
		[ -d "$(KIT_DIST)" ] || { echo "preview FAILED: no kit at $(KIT_DIST); set KIT_DIST to a meridian-ui checkout's dist/" >&2; exit 1; }; \
		mkdir -p "$(SHOTS)"; \
		docker run --rm --init --shm-size=1g -v "$(CURDIR)/preview":/pages:ro -v "$(abspath $(KIT_DIST))":/kit:ro \
			-v "$(CURDIR)/tools":/tools:ro -v "$(abspath $(SHOTS))":/out $(BROWSER) \
			sh -c 'pip install -q --break-system-packages playwright==1.63.0 >/dev/null 2>&1 && python3 /tools/browser.py' || exit 1; \
	fi

# Applied in a container and written back, because the host has no toolchain.
fmt:
	@docker run --rm -v "$(CURDIR)":/w -w /w python:$(PY_VERSION)-slim \
		sh -c 'pip install -q ruff >/dev/null 2>&1; python -m ruff check --fix src tests >/dev/null; python -m ruff format src tests'
	@echo "fmt: applied"

install-hooks:
	@git config core.hooksPath hooks
	@echo "hooks installed: git push now runs 'make ci-local' first"
