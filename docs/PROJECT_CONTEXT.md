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

**Core scope:** two-node DS-TWR; USB serial/ESP32 Wi-Fi telemetry; timestamped
recordings; browser-entered geometry priors; repeated fixed-condition experiments;
simple noise statistics, distributions, spectra, and calibration checks.

**Possible research extensions:** timestamped LOS/NLOS annotations, a simple
threshold/classifier, or one temporal model justified by the collected data.
GPs, HMMs, autoregressive and neural methods are options, not required features.
Use whole-session splits and independent-condition evaluation if fitting models.

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

**Engineering:** connect and see range/health; start/stop recording; apply
distance/position priors without restarting acquisition; keep raw measurements
and prior revisions; replay without boards. Show disconnects, malformed frames,
and dropped-sequence counts rather than hiding them.
Firmware build/boot identity and richer clock/reset diagnostics are useful future
metadata, not capabilities already supplied by the current recordings.

**Scientific:** explain bias, spread, and outliers from repeated measured-distance
recordings; keep enough conditions and notes to reproduce the experiment.
If fitting corrections/classifiers, compare against simple baselines and separate
whole runs, not shuffled adjacent samples. Evaluate uncertainty or LOS/NLOS
transition metrics only when the corresponding model/labels exist.
Detailed protocols and metrics live in [EXPERIMENTS_AND_VALIDATION.md](EXPERIMENTS_AND_VALIDATION.md).

Keep the research stack Python-first and exploratory analysis on the host; MCUs do deterministic
radio/transport work. Host wall time is useful for display, not a substitute for device monotonic time.
The ~1 Hz bring-up cadence is insufficient for short transitions: measure a usable research rate
before selecting it.
