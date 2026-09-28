# UWB Project Documentation

This directory is the working source of truth for the UWB ranging and noise-modeling project. It separates verified behavior in the current firmware from proposed integration work.

## Document map

| Document | Purpose |
| --- | --- |
| [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) | Research goals, scope, terminology, and success criteria |
| [CODEBASE_LAYOUT.md](CODEBASE_LAYOUT.md) | Verified description of the current STM32 and ESP32 code |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Target system, UI behavior, telemetry schema, and dynamic-prior semantics |
| [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) | Ordered implementation phases and acceptance criteria |
| [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) | Short record of completed and remaining work |
| [EXPERIMENTS_AND_VALIDATION.md](EXPERIMENTS_AND_VALIDATION.md) | Data-collection protocol, baselines, models, and evaluation rules |
| [LITERATURE.md](LITERATURE.md) | Supervisor-provided references and reading priorities |

## Rules for future development

1. Label statements as **Current**, **Planned**, or **Decision needed** when the distinction is not obvious.
2. Update `CODEBASE_LAYOUT.md` when ownership, interfaces, build steps, or the top-level folder layout changes.
3. Update `ARCHITECTURE.md` before changing a persisted schema or device/host protocol. Schema changes must be versioned.
4. Never modify stored raw measurements when a calibration, prior, or model changes. Derived outputs must identify the input dataset, configuration, prior revision, and model version.
5. Add a replay-based test before relying on live hardware for a new parser, analysis stage, or UI behavior.
6. Keep STM32CubeMX-generated edits inside `USER CODE` sections where possible.
7. Do not commit network credentials, participant information, room access details, or machine-specific device paths.

## Recommended top-level additions

These folders are planned but should be created only when their phase starts:

```text
host/                    Python acquisition, analysis, API, and UI service
tests/                   Protocol, replay, analysis, and integration tests
data/
  README.md              Dataset storage policy; raw recordings stay untracked
  manifests/             Versioned metadata without large measurement files
configs/                 Versioned radio, experiment, prior, and model configs
models/                  Model definitions and small metadata; weights untracked
```

Large data and trained weights should live in managed external storage or Git LFS/DVC, not ordinary Git history.
