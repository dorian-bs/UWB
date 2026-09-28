.PHONY: help setup check build test demo esp-config esp-build esp-flash stm-tx-flash stm-rx-flash

help:
	@echo "make setup          Install development tools and Python dependencies"
	@echo "make check          Check the local development environment"
	@echo "make build          Build both STM32 roles and the ESP32 firmware"
	@echo "make test           Run host tests"
	@echo "make demo           Start the dashboard with generated data"
	@echo "make esp-config     Configure ESP32 Wi-Fi and project settings"
	@echo "make esp-flash      Flash and monitor the connected ESP32"
	@echo "make stm-tx-flash   Flash the connected STM32 as initiator"
	@echo "make stm-rx-flash   Flash the connected STM32 as responder"

setup:
	bash scripts/setup_macos.sh

check:
	bash scripts/check_setup.sh

build:
	bash scripts/build_firmware.sh

test:
	.venv/bin/python -m pytest host/tests

demo:
	bash scripts/run_dashboard.sh --source demo

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
