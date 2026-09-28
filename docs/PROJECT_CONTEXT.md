# Project Context

## Objective

Characterize and model real-world noise in DW3000-based ultra-wideband ranging, with particular attention to bias, variance, temporal coherence, and transitions between line-of-sight (LOS) and non-line-of-sight (NLOS) conditions.

The system must support two complementary workflows:

1. **Live experiment monitoring:** observe ranging and device health, record sessions, annotate events, and update geometric prior information while an experiment is running.
2. **Reproducible offline research:** replay immutable recordings, compare noise models, detect LOS/NLOS transitions, and evaluate on independent sessions.

## Research questions

- How do range residuals vary with true distance, environment, orientation, motion, obstruction type, and radio diagnostics?
- Which model best predicts range bias and uncertainty without leaking information from the evaluation session?
- How much temporal structure exists in the residuals, and at what time scales?
- Can LOS/NLOS state or a change point be inferred from ranging history alone?
- How much does NLOS detection or bias correction improve downstream ranging and localization?

## Scope

### In scope

- Two-node double-sided two-way ranging (DS-TWR).
- Live telemetry over USB serial and/or the existing ESP32 Wi-Fi bridge.
- Timestamped recording with experiment metadata and operator annotations.
- Dynamic geometric priors entered in a browser UI.
- Baseline statistical analysis and time-series diagnostics.
- Gaussian, Gaussian-process, HMM, autoregressive, change-point, and neural approaches where justified by data volume.
- Session-level train/validation/test separation and independent-environment evaluation.

### Not initially in scope

- Full multi-anchor 2D/3D localization.
- Closed-loop robot control based on corrected range.
- Running learned models on the microcontrollers.
- Treating operator-entered geometry as measured ground truth without uncertainty.

These are possible extensions after the two-node measurement pipeline is stable.

## Terminology

- **Node:** one STM32 + DW3000 assembly. An ESP32 may provide its network bridge.
- **Initiator:** starts the DS-TWR exchange and computes the range after receiving the report.
- **Responder:** answers the ranging exchange and reports its timestamps.
- **Raw range:** range calculated from DS-TWR timestamps before empirical bias correction.
- **Corrected range:** output of a named calibration or model. It never replaces raw range.
- **Geometric prior:** expected relative geometry supplied by the operator or another trusted source, including uncertainty and a revision identifier.
- **Residual:** measured range minus expected geometric range.
- **Ground truth:** independently measured geometry with a documented instrument and uncertainty. A UI entry is a prior until independently verified.
- **Session:** one uninterrupted recording with fixed device/radio identities and a manifest.
- **Run/segment:** an interval within a session sharing scenario labels or geometry.

## Success criteria

### Engineering

- A user can connect, see live range and connection health, and start/stop a recording.
- A position or direct-distance prior update appears in residual analysis within one display update, without restarting acquisition.
- Every stored sample is traceable to device, firmware, radio configuration, host receive time, session, and prior revision.
- A recorded session can be replayed through the same parser, analysis pipeline, and UI used for live data.
- Disconnects, malformed frames, dropped sequence numbers, and clock resets are visible rather than silently ignored.

### Scientific

- Raw data, labels, split definitions, preprocessing, and metrics are reproducible.
- Models are compared against no-correction and simple Gaussian baselines.
- Evaluation sessions are separated by complete run, trajectory, and preferably environment, not by randomly shuffling adjacent samples.
- Reported uncertainty is checked for calibration as well as point accuracy.
- LOS/NLOS results include transition behavior, class imbalance, and detection delay.

## Constraints and assumptions

- The host analysis stack should remain Python-first because the research models and expected project skills are Python-oriented.
- The microcontrollers should perform deterministic radio and transport work; exploratory analysis belongs on the host.
- Host wall-clock timestamps are useful for display but are not a substitute for device monotonic timestamps.
- The current one-sample-per-second cadence is adequate for bring-up, not for studying short temporal transitions. The usable target rate must be measured before it is selected.
