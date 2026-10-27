# Antenna-delay calibration assistant design

Date: 2026-10-27
Status: Approved design draft (pre-implementation)

## Goal

Help operators understand and run the existing firmware antenna-delay calibration procedure, then support it in the dashboard with a UI-only assistant that computes and explains a calibration suggestion.

## Context and existing behavior

Calibration is currently firmware compile-time assisted in [qorvo_port.c](/Users/dorianbesson/EDOC/UWB/epuck-stm32-main/ThirdParty/qorvo_dw3/platform/qorvo_port.c):

- `QORVO_CAL_ANT_DELAY_MODE` enables sample collection and summary output.
- `QORVO_CAL_KNOWN_DISTANCE_M` is the known reference distance.
- `QORVO_CAL_SAMPLE_TARGET` controls how many samples are collected before summary.
- Summary guidance already states:
  - mean too high => increase both TX/RX antenna delays on both boards
  - mean too low => decrease both TX/RX antenna delays on both boards

This process is not currently host-driven. The dashboard does not apply calibration to hardware automatically.

## Non-goals

- No automatic write/apply of calibration values to boards.
- No new firmware command protocol for calibration mode.
- No backend storage/API for calibration sessions in this phase.
- No changes to raw telemetry schema (`type=range`, `v=1`).

## User workflow

### Immediate (existing) calibration runbook

1. Set a fixed, accurately measured LOS distance between nodes.
2. Enable firmware calibration mode and set known distance + sample target in [qorvo_port.c](/Users/dorianbesson/EDOC/UWB/epuck-stm32-main/ThirdParty/qorvo_dw3/platform/qorvo_port.c).
3. Flash both boards and run acquisition.
4. Read calibration summary logs.
5. Adjust both `QORVO_TX_ANT_DLY_DTU` and `QORVO_RX_ANT_DLY_DTU` equally on both boards.
6. Reflash and repeat until mean error is acceptable.
7. Disable calibration mode for normal operation.

### New dashboard assistant workflow

1. Open **Antenna-delay calibration assistant** in the dashboard.
2. Enter known distance and target sample count.
3. Start capture while setup remains fixed LOS.
4. Review computed stats and suggestion.
5. Manually edit firmware constants and reflash boards.
6. Repeat capture as needed.

## UI design

Add a new control panel in [index.html](/Users/dorianbesson/EDOC/UWB/host/uwb_lab/static/index.html), near **Geometry prior**, with:

- Inputs:
  - known distance (m)
  - target samples
  - tolerance (m, default `0.01`)
  - DTU-per-cm hint (editable text/number helper for operator workflow)
- Actions:
  - Start capture
  - Stop capture
  - Reset
- Outputs:
  - captured sample count
  - skipped sample count
  - corrected stats: mean, stddev, min, max, mean error
  - raw stats (reference only): mean, stddev, min, max
  - recommendation text
  - safety note: suggestion only; firmware edit/reflash required

## Data and computation design

All assistant state is client-side in [app.js](/Users/dorianbesson/EDOC/UWB/host/uwb_lab/static/app.js).

- Data source: existing websocket stream (`/ws`) sample messages.
- Sample inclusion:
  - include only valid samples with finite `corrected_m`
  - collect `raw_m` only for reference stats
  - increment skipped counter for non-finite/missing needed values during capture
- Computation:
  - `error_m = mean(corrected_m) - known_distance_m`
  - recommendation:
    - if `abs(error_m) <= tolerance`: no delay change suggested
    - if `error_m > tolerance`: suggest increasing TX/RX antenna delays on both boards
    - if `error_m < -tolerance`: suggest decreasing TX/RX antenna delays on both boards

No changes to host `Prior`, residual pipeline, recording files, or telemetry parsing.

## Error handling and UX rules

- Invalid input (distance, sample target, tolerance): block start with explicit inline message.
- Fewer than 2 captured corrected samples on stop/target: show “not enough data”.
- Reset clears only assistant state; must not affect acquisition, recording, prior, experiments, or radio settings.
- Panel remains informative in demo/replay, but includes a caution that physical fixed-LOS calibration validity requires real hardware.

## Testing strategy

Add/extend frontend Node tests for:

- accumulator behavior across streamed samples
- skipped-sample counting
- recommendation direction for positive/negative/within-tolerance error
- minimum-sample guard
- reset behavior

Validation commands:

1. Run the smallest targeted JS/UI tests covering assistant logic.
2. Run `make test` to verify host test suite still passes.

## Documentation updates

Update:

- [host/README.md](/Users/dorianbesson/EDOC/UWB/host/README.md) with assistant usage and limits.
- [docs/IMPLEMENTATION_STATUS.md](/Users/dorianbesson/EDOC/UWB/docs/IMPLEMENTATION_STATUS.md) to reflect added calibration assistant capability.

## Acceptance criteria

- Dashboard includes a functional calibration assistant panel.
- Suggestions are deterministic from captured corrected data and configured tolerance.
- No backend API changes required.
- No regression to existing acquisition, prior, experiment, radio, or recording behavior.
