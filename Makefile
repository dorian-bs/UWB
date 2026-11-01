PY := .venv/bin/python

.PHONY: help setup check verify lint fmt test demo replay build \
        esp-config esp-build esp-flash \
        stm-tx-flash stm-rx-flash stm-tx-dfu-flash stm-rx-dfu-flash stm-dfu-flash

help:
	@echo "No hardware needed:"
	@echo "  make verify         Lint and test everything (run this before finishing a change)"
	@echo "  make test           Run host tests"
	@echo "  make lint           Check Python style and common mistakes"
	@echo "  make fmt            Auto-format and auto-fix Python"
	@echo "  make demo           Start the dashboard with generated data"
	@echo "  make replay         Start the dashboard replaying the sample session"
	@echo "  make check          Check the local development environment"
	@echo "  make setup          Install development tools and Python dependencies"
	@echo ""
	@echo "Needs toolchains or boards:"
	@echo "  make build          Build both STM32 roles and the ESP32 firmware"
	@echo "  make esp-config     Configure ESP32 Wi-Fi and project settings"
	@echo "  make esp-flash      Flash and monitor the connected ESP32"
	@echo "  make stm-tx-flash   Flash the connected STM32 as initiator"
	@echo "  make stm-rx-flash   Flash the connected STM32 as responder"
	@echo "  make stm-tx-dfu-flash Flash STM32 TX over USB DFU bootloader"
	@echo "  make stm-rx-dfu-flash Flash STM32 RX over USB DFU bootloader"
	@echo "  make stm-dfu-flash  Flash both STM32 boards (TX + RX) over USB DFU at once"

setup:
	bash scripts/setup_macos.sh

check:
	bash scripts/check_setup.sh

verify: lint test

lint:
	$(PY) -m ruff check host
	$(PY) -m ruff format --check host

fmt:
	$(PY) -m ruff format host
	$(PY) -m ruff check --fix host

test:
	$(PY) -m pytest host/tests

demo:
	bash scripts/run_dashboard.sh --source demo

replay:
	bash scripts/run_dashboard.sh --source replay --replay-file data/fixtures/sample_session.ndjson

build:
	bash scripts/build_firmware.sh

esp-config:
	bash scripts/esp32.sh menuconfig

esp-build:
	bash scripts/esp32.sh build

esp-flash:
	bash scripts/esp32.sh flash monitor

stm-tx-flash:
	cd epuck-stm32-main && bash scripts/stm32_flash.sh TxDebug

stm-rx-flash:
	cd epuck-stm32-main && bash scripts/stm32_flash.sh RxDebug

stm-tx-dfu-flash:
	cd epuck-stm32-main && bash scripts/stm32_flash_usb_dfu.sh TxDebug

stm-dfu-flash:
	cd epuck-stm32-main && bash scripts/stm32_flash_usb_dfu_both.sh

stm-rx-dfu-flash:
	cd epuck-stm32-main && bash scripts/stm32_flash_usb_dfu.sh RxDebug
