.PHONY: help install start stop upgrade uninstall test dev lint docs

ROLE ?= terminal
PORT ?= 8888
CHANNEL ?= stable
RELOAD ?=
CELERY ?=
START_ON_INSTALL ?=
EXTRA_INSTALL_ARGS ?=
EXTRA_START_ARGS ?=
TEST_ARGS ?=

role_flag := $(if $(filter terminal,$(ROLE)),--terminal,$(if $(filter control,$(ROLE)),--control,$(if $(filter satellite,$(ROLE)),--satellite,$(if $(filter watchtower,$(ROLE)),--watchtower,))))
channel_flag := $(if $(filter latest,$(CHANNEL)),--latest,$(if $(filter stable,$(CHANNEL)),--stable,$(if $(filter fixed,$(CHANNEL)),--fixed,)))
port_arg := $(if $(PORT),--port $(PORT),)
reload_flag := $(if $(filter true,$(RELOAD)),--reload,)
celery_flag := $(if $(filter true,$(CELERY)),--celery,$(if $(filter false,$(CELERY)),--no-celery,))
start_flag := $(if $(filter true,$(START_ON_INSTALL)),--start,$(if $(filter false,$(START_ON_INSTALL)),--no-start,))

help:
	@printf "Available targets:\n"
	@printf "  make install [ROLE=terminal|control|satellite|watchtower] [PORT=8888] [START_ON_INSTALL=true|false] [CELERY=true|false] [CHANNEL=stable|latest|fixed] [EXTRA_INSTALL_ARGS=...]\\n"
	@printf "  make start [PORT=8888] [RELOAD=true] [CELERY=true|false] [EXTRA_START_ARGS=...]\\n"
	@printf "  make stop\\n"
	@printf "  make upgrade [CHANNEL=stable|latest|fixed]\\n"
	@printf "  make uninstall\\n"
	@printf "  make test [TEST_ARGS=...]\\n"
	@printf "  make lint\\n"
	@printf "  make docs\\n"
  @printf "  make dev [ROLE=terminal|control|satellite|watchtower] [PORT=8888] [RELOAD=true] [CELERY=true|false] [CHANNEL=stable|latest|fixed] [TEST_ARGS=...] [EXTRA_INSTALL_ARGS=...] [EXTRA_START_ARGS=...]\\n"

install:
	./install.sh $(role_flag) $(port_arg) $(start_flag) $(celery_flag) $(channel_flag) $(EXTRA_INSTALL_ARGS)

start:
	./start.sh $(port_arg) $(reload_flag) $(celery_flag) $(EXTRA_START_ARGS)

stop:
	./stop.sh

upgrade:
	./upgrade.sh $(channel_flag)

uninstall:
	./uninstall.sh

test:
	pytest $(TEST_ARGS)

lint:
	black --check .

docs:
	mkdocs build --strict

dev:
  $(MAKE) install
  $(MAKE) start
  pytest $(if $(TEST_ARGS),$(TEST_ARGS),-k smoke)
