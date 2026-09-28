# UWB Ranging Project

Semester-project setup for DW3000 ranging, an STM32/ESP32 link, and a small Python live dashboard.

## New Mac setup

Requirements: macOS on Apple Silicon and [Homebrew](https://brew.sh).

Clone and set up the project:

```bash
git clone https://github.com/dorian-bs/UWB.git
cd UWB
bash scripts/setup_macos.sh
make build
make test
```

The setup is safe to rerun. It installs:

- CMake, Ninja, OpenOCD, and Python 3.13 through Homebrew when missing.
- Arm GNU Toolchain 14.3.rel1 under `~/.local/`.
- ESP-IDF v5.5.5 and ESP32-S3 tools under `~/esp/` and `~/.espressif/`.
- Host Python dependencies in the repository `.venv/`.

Use `make check` at any time to diagnose an incomplete setup.

## Try the dashboard

```bash
make demo
```

Open <http://127.0.0.1:8000>. This uses generated data and requires no hardware.

## Configure and flash hardware

Configure the ESP32 network without storing credentials in Git:

```bash
make esp-config
make esp-flash
```

Build both STM32 images with `make build`. Then connect the appropriate board and run:

```bash
make stm-tx-flash
make stm-rx-flash
```

Only one STM32 should be connected when running each flash command. The TX board computes and publishes range measurements.

Start the dashboard with the ESP32 IP address:

```bash
bash scripts/run_dashboard.sh --source tcp --tcp-host ESP32_IP
```

For implementation progress and limitations, see [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).