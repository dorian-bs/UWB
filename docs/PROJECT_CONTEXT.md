# Project Context

## Objective and research questions

Characterize DW3000 UWB ranging noise: bias, variance, temporal coherence, and
line-of-sight/non-line-of-sight (LOS/NLOS) transitions. Support both live monitoring
(health, recording, annotations, dynamic geometry) and reproducible offline replay/model evaluation.

Investigate:

- How residuals vary with distance, environment, orientation, motion, obstruction, and radio diagnostics.
- Which models predict bias/uncertainty without evaluation-session leakage.
- Temporal structure and its time scales; LOS/NLOS or change-point inference from ranging history alone.
- Whether detection/correction improves ranging and downstream localization.

## Scope

**In scope:** two-node DS-TWR; USB serial/ESP32 Wi-Fi telemetry; timestamped recordings with
metadata/annotations; browser-entered geometric priors; baseline/time-series analysis;
Gaussian, GP, HMM, autoregressive, change-point, and neural methods where data justifies them;
whole-session train/validation/test splits and independent-environment evaluation.

**Not initially:** multi-anchor 2D/3D localization, closed-loop robot control, MCU learned models,
or treating operator geometry as ground truth without documented uncertainty.
These may follow a stable two-node pipeline.

## Vocabulary

| Term | Meaning |
| --- | --- |
| Node | STM32 + DW3000 assembly, optionally bridged by ESP32 |
| Initiator / responder | Starts exchange and computes range / answers and reports timestamps |
| Raw range | DS-TWR timestamp result before empirical bias correction |
| Corrected range | Named calibration/model output; never replaces raw range |
| Geometric prior | Expected geometry with uncertainty and revision ID, supplied by operator/trusted source |
| Residual | Observed range minus expected geometric range |
| Ground truth | Independently measured geometry with documented instrument/uncertainty; unverified UI input is only a prior |
| Session | Uninterrupted recording with fixed device/radio identities and a manifest |
| Run/segment | Session interval sharing scenario labels or geometry |

## Success criteria and constraints

**Engineering:** connect and see range/health; start/stop recording; apply distance/position priors
within one display update without restarting acquisition; trace every sample to device, firmware,
radio config, host receive time, session, and prior revision; replay through the live parser/analysis/UI.
Expose disconnects, malformed frames, dropped sequences, and clock resets rather than hiding them.

**Scientific:** reproduce raw data, labels, splits, preprocessing, and metrics; compare against
no-correction/simple Gaussian baselines; separate complete runs/trajectories and preferably environments,
not shuffled adjacent samples; assess uncertainty calibration and point accuracy; report transition
behavior, imbalance, and delay for LOS/NLOS.
Detailed protocols and metrics live in [EXPERIMENTS_AND_VALIDATION.md](EXPERIMENTS_AND_VALIDATION.md).

Keep the research stack Python-first and exploratory analysis on the host; MCUs do deterministic
radio/transport work. Host wall time is useful for display, not a substitute for device monotonic time.
The ~1 Hz bring-up cadence is insufficient for short transitions: measure a usable research rate
before selecting it.
