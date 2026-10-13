# UWB Project Documentation

Start with the [root README](../README.md) for installation/hardware commands,
the [host README](../host/README.md) for host-only usage, or [CLAUDE.md](../CLAUDE.md) for agent orientation.

| Document | Canonical contents |
| --- | --- |
| [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) | Research goals, scope, vocabulary, success criteria |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Current codebase/ownership, wire contract, target UI/prior/storage/control design |
| [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) | Working features, limitations, integration history, phased roadmap and acceptance criteria |
| [EXPERIMENTS_AND_VALIDATION.md](EXPERIMENTS_AND_VALIDATION.md) | Collection protocol, data quality, splits, baselines, models, metrics |
| [LITERATURE.md](LITERATURE.md) | Supplied bibliography, reading priorities, extraction checklist |
| [LAB_NOTES.example.md](LAB_NOTES.example.md) | Template for ignored, machine-specific lab notes |

## Maintenance rules

- Distinguish **Current**, **Planned**, and **Decision needed**; update status when behavior changes.
- Keep ownership/interfaces and persisted/wire schemas in architecture; version schema changes.
- Preserve raw observations. Derived outputs identify dataset, configuration, prior, and model revisions.
- Test new parser/analysis/UI behavior with replay before relying on hardware; protect CubeMX `USER CODE` regions.
- Never commit credentials, participant information, room access details, or machine-specific device paths.

The former codebase layout is merged into architecture; the implementation summary and integration
plan are merged into status. Add new files only for a distinct subject, not a second copy of these facts.
