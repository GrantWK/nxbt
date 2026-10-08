.PHONY: all install-deps install test docker

VENV   := .venv
PYTHON := $(VENV)/bin/python

all: install

# System libraries used at runtime (libusb for Bumble USB, libcap for
# capability handling, bluez for the BlueZ backend) plus a compiler for
# dependencies that have no pre-built wheel on the current architecture.
install-deps:
	@SUDO=; if [ "$$(id -u)" -ne 0 ]; then SUDO=sudo; fi; \
	if [ -f /etc/alpine-release ]; then \
		$$SUDO apk update && \
		$$SUDO apk add --no-cache python3 python3-dev py3-pip gcc musl-dev libcap libusb bluez; \
	elif [ -f /etc/debian_version ]; then \
		$$SUDO apt update && \
		$$SUDO apt install -y python3 python3-dev python3-venv gcc libcap2 libusb-1.0-0 bluez; \
	else \
		echo "Unsupported OS for install-deps. Install Python 3.10+, libusb, libcap and bluez manually."; \
	fi

$(PYTHON):
	python3 -m venv $(VENV)

install: $(PYTHON)
	$(PYTHON) -m pip install .

test: $(PYTHON)
	$(PYTHON) -m pip install -e ".[dev]"
	$(PYTHON) -m pytest

docker:
	docker build -t nxbt:gnu -f docker/gnu/Dockerfile .
	docker build -t nxbt:musl -f docker/musl/Dockerfile .
