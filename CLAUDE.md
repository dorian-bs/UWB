# CLAUDE.md — agent brief for the UWB project

Read this first. It is the entry point for any agent working in this repository.
Everything here is verified against the workspace; deeper background lives in `docs/`.

## What this is

A university semester project that measures and models noise in DW3000 ultra-wideband (UWB)
ranging. Two nodes do double-sided two-way ranging (DS-TWR); a Python host records, visualises
and (eventually) models the residuals.

```
DW3000 ──SPI──> STM32F405 ──UART3/115200──> ESP32-S3 ──TCP:3333──> Python host ──> browser UI
                    └──USB CDC────────────────────────────────────────┘           └─> data/sessions/*.ndjson
```

This is coursework, not production. Prefer the smallest change that works and is easy to
iterate on. Do not add abstraction layers, dependency injection, plugin systems, or CI
pipelines unless asked.

## Repo map

| Path | What it is | Who edits it |
| --- | --- | --- |
| `host/uwb_lab/` | FastAPI service: acquisition, priors, recording, replay, UI | Edit freely |
| `host/uwb_lab/static/` | Dependency-free browser UI (plain HTML/CSS/JS, no build step) | Edit freely |
| `host/tests/` | Fast, hardware-free tests | Add tests here |
| `epuck-stm32-main/ThirdParty/qorvo_dw3/platform/qorvo_port.c` | Radio config, DS-TWR state machine, range maths, telemetry output | Main firmware file |
| `epuck-stm32-main/Core/Src/main.c` | CubeMX-generated; project logic only inside `USER CODE` blocks | Edit carefully |
| `epuck-esp32-main/main/main.c` | UART↔TCP bridge, Wi-Fi station | Transport only |
| `scripts/` | Setup, build, flash, run wrappers | Keep thin |
| `docs/` | Project context, architecture, plan, status | Keep current |
| `archive/` | Old firmware copies. **Ignored by Git. Never edit or cite.** | — |

## Working without hardware (the normal case)

An agent almost never has the boards attached. Everything below runs on the laptop alone:

```bash
make verify     # lint + tests + a real HTTP smoke test of the dashboard — run this before finishing
make test       # tests only
make demo       # live dashboard on synthetic data, http://127.0.0.1:8000
make replay     # dashboard replaying data/fixtures/sample_session.ndjson
```

`make demo` and `make replay` block the terminal — run them in the background or expect to stop them.

The Python interpreter is `.venv/bin/python` at the repo root; `host/` is installed into it as an
editable package, so edits under `host/uwb_lab/` take effect with no reinstall.

**Do not run `make build`, `make esp-*`, or any `*-flash` target unless the user asks.** Firmware
builds need the Arm toolchain plus ESP-IDF and take minutes; flashing needs boards on USB.

If `make verify` fails because the environment is missing, run `make check` to see what, and
`make setup` (macOS + Homebrew only) to install it.

## The one interface that crosses every boundary

The STM32 emits one compact JSON line per successful exchange, mixed into human-readable debug
logs. The host ignores any line it cannot parse.

```json
{"type":"range","v":1,"board":1,"peer":2,"seq":42,"time_ms":41000,"raw_m":2.1234,"corrected_m":2.1012,"valid":true}
```

Emitted at `epuck-stm32-main/ThirdParty/qorvo_dw3/platform/qorvo_port.c:1155`.
Parsed by `parse_range_line()` in `host/uwb_lab/core.py`.

**Changing this record means editing both sides in the same change, bumping `v`, updating
`docs/ARCHITECTURE.md`, and updating the test fixtures.** Adding a field the parser does not know
about is safe (it is ignored); removing or renaming one is not.

## Conventions that matter here

- **Ownership boundary.** STM32 = radio timing and raw measurement. ESP32 = dumb transport, no
  science. Host = recording, priors, corrections, analysis, UI. Don't move analysis onto a MCU.
- **Raw data is immutable.** `raw_m` is never overwritten by a correction. Corrections produce new
  fields or new files and must name the config/prior revision they came from.
- **Replay over hardware.** New parsers, analysis stages, or UI behaviour get a test that runs
  against a recorded or synthetic line, not against a board.
- **CubeMX code.** Keep STM32 edits inside `/* USER CODE BEGIN ... END */` markers so regenerating
  the `.ioc` does not wipe them.
- **No secrets in Git.** Wi-Fi credentials live in the ESP32 `sdkconfig` (untracked, set via
  `make esp-config`). Lab IP addresses and `/dev/tty.*` paths are machine-specific: they belong in
  the gitignored `LAB_NOTES.md` (template: `LAB_NOTES.example.md`), never in `README.md`, `docs/`,
  or source. If a task needs a real board address, read `LAB_NOTES.md` — do not copy it anywhere
  tracked.
- Python targets 3.11+, uses `from __future__ import annotations` and modern type hints. Formatting
  and linting are `ruff` (`make fmt`, `make lint`); line length 110.

## Known rough edges (don't "fix" these by accident)

- Ranging runs at ~1 Hz. That is a bring-up value, not a research target — raising it is a real
  task with its own measurement, not a constant to tweak.
- The TX/initiator board is the only one that emits range telemetry.
- The JSON record carries no radio diagnostics (RSSI, first-path power, CIR) yet — adding them is a
  known planned task, see `docs/IMPLEMENTATION_STATUS.md`.
- The ESP32 bridge accepts a single unauthenticated TCP client. Fine for a trusted lab network,
  deliberately not hardened.
- Priors are in-memory and reset when the host restarts.
- `host/uwb_lab/app.py` holds module-level `runtime` state. Tests must not assume a clean instance
  between modules.

## Deeper docs

| File | Read it when |
| --- | --- |
| `docs/PROJECT_CONTEXT.md` | You need the research goal, scope, or vocabulary |
| `docs/CODEBASE_LAYOUT.md` | You need verified detail on the firmware |
| `docs/ARCHITECTURE.md` | You are touching a schema, protocol, or the prior semantics |
| `docs/IMPLEMENTATION_STATUS.md` | You want to know what works and what is next |
| `docs/INTEGRATION_PLAN.md` | You are picking up a planned phase |
| `docs/EXPERIMENTS_AND_VALIDATION.md` | You are doing analysis, splits, or evaluation |
| `README.md` | Hardware bring-up, flashing, serial ports, monitor commands |

When a feature becomes usable or a limitation disappears, update
`docs/IMPLEMENTATION_STATUS.md` — it is the file that goes stale first.
