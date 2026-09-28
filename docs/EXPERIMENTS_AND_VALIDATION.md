# Experiments and Validation

## Measurement protocol

Each session manifest should include:

- Session ID, UTC start/end, operator, and free-form notes.
- Firmware commit/build ID for both nodes and ESP32 bridge.
- Device IDs, roles, antenna IDs/orientations, mounting, and antenna delays.
- Channel, preamble, data rate, PRF, STS configuration, TX power, and sample target rate.
- Environment ID and a sketch/photo reference stored outside Git if sensitive.
- Ground-truth instrument, resolution, uncertainty, coordinate frame, and synchronization method.
- Planned scenario sequence and any deviations.
- Temperature/power conditions when available.

Do not infer missing metadata later if it can be recorded at acquisition time.

## Initial experiment matrix

Run repeat sessions rather than one very long mixed recording.

### Static LOS characterization

- Several distances spanning the intended operating range.
- Multiple antenna orientations at each distance.
- At least three independently restarted sessions per condition.
- Enough samples to estimate tails and autocorrelation, not only mean/stddev.

Purpose: antenna-delay calibration, distance-dependent bias, variance, outliers, and temporal stability.

### Static NLOS characterization

- Repeat selected distances with controlled obstructions: human body, wood/drywall, metal, and other relevant lab materials.
- Record obstruction identity, thickness, position, and whether first-path blockage is complete.
- Include matched LOS runs before and after NLOS runs.

Purpose: material-specific bias/variance and radio-diagnostic sensitivity.

### LOS/NLOS transitions

- Introduce and remove an obstruction at independently timestamped events.
- Vary transition speed and dwell time.
- Repeat both LOS-to-NLOS and NLOS-to-LOS directions.

Purpose: temporal coherence, change-point accuracy, HMM transition behavior, and detection delay.

### Dynamic geometry

- Move one node along repeatable trajectories with independent position/distance reference.
- Include constant-speed, stop/start, approaching, and receding segments.
- Separate motion-induced geometry change from obstruction change where possible.

Purpose: distinguish model lag and geometry error from radio noise.

### Generalization sessions

- Change room/layout, node mounting, day, and selected obstruction placement.
- Keep these sessions sealed from model selection.

Purpose: measure robustness to deployment shift.

## Ground truth and priors

The UI geometry entry is useful immediately, but its scientific role depends on provenance:

- A measured distance with instrument uncertainty may be used as ground truth.
- A tape mark, robot odometry estimate, or manual position entry should carry its own uncertainty.
- An unverified operator estimate is a prior, not ground truth.
- Prior revisions during a session must be timestamped and never retroactively overwrite source records.

For dynamic experiments, synchronize external ground truth through a shared trigger where possible. Otherwise estimate and report temporal alignment uncertainty.

## Data quality checks

Run automatically when a session closes:

- Required manifest fields present and units valid.
- Unique `(device_id, boot_id, sequence)` sample identity.
- Sequence gaps, duplicates, out-of-order frames, and boot changes summarized.
- Device time monotonic within each boot.
- Range bounds and non-finite values flagged, never silently removed.
- Expected sample count compared with valid and invalid samples.
- Prior and annotation intervals resolve unambiguously.
- Hashes generated for archival files.

Exclusion rules must be declared before model evaluation and reported as counts by reason.

## Dataset splits

Adjacent UWB samples are correlated. Never use a random row-level split for headline results.

Recommended hierarchy:

- **Training:** complete sessions used to fit model parameters.
- **Validation:** different complete sessions used for features, thresholds, and hyperparameters.
- **Test:** unopened complete sessions, preferably including an unseen environment/day.

Where data allows, report grouped cross-validation by session or environment in addition to the frozen test set.

## Baselines

### Ranging

1. No correction: corrected range equals raw range.
2. Global Gaussian: one training-set bias and variance.
3. Distance-binned Gaussian: bias/variance by distance bin, with bins fixed from training data.
4. Causal rolling robust estimate: median and MAD over a fixed past-only window.

### LOS/NLOS

1. Majority class.
2. Threshold on causal residual or rolling variance.
3. Simple change-point detector.
4. Logistic regression on a small documented causal feature set.

Advanced methods must beat relevant simple baselines on independent sessions to justify added complexity.

## Candidate models

| Model | Best use | Main caution |
| --- | --- | --- |
| AR/ARIMA/state space | Short-term residual dynamics | Stationarity and changing geometry |
| HMM | Persistent latent LOS/NLOS regimes | State-label interpretation and dwell assumptions |
| Gaussian process | Bias/uncertainty versus geometry or diagnostics | Cubic scaling without sparse approximation |
| Change-point methods | Transition detection with few labels | Threshold sensitivity and detection delay |
| Tree/linear classifier | Interpretable diagnostic baseline | Feature leakage from future windows |
| Temporal neural network | Complex nonlinear sequence patterns | Data volume, calibration, and overfitting |

Start with interpretable models. Add complexity based on residual diagnostics and learning curves, not because a method is fashionable.

## Metrics

### Range and bias model

- Signed bias and bias by condition/distance.
- MAE and RMSE.
- Median and 95th/99th percentile absolute error.
- Negative log likelihood for probabilistic models.
- Prediction interval coverage and interval width.
- Residual autocorrelation after correction.

### LOS/NLOS detection

- Per-class precision, recall, and F1.
- Balanced accuracy and confusion matrix.
- AUROC and especially AUPRC under class imbalance.
- Detection delay for transitions.
- False transitions/alarms per minute.
- Probability calibration when probabilities are emitted.

Report bootstrap confidence intervals grouped by session, not by treating every correlated sample as independent.

## Live analysis versus final analysis

Live plots may use bounded causal windows and provisional labels. Final results must run from closed session files with a fixed configuration. The UI should clearly mark provisional model output and must not write predictions back into raw telemetry.

## Useful follow-on features

- Synchronized camera/event button for stronger obstruction labels, with an explicit privacy policy.
- Robot odometry or motion-capture adapter for dynamic ground truth.
- CIR snapshot capture around selected events rather than continuously, to limit bandwidth.
- Automated calibration wizard that estimates antenna delay from dedicated calibration sessions.
- Data-quality dashboard comparing devices, firmware versions, and environments.
- Active-learning queue that surfaces uncertain LOS/NLOS intervals for human review.
- Out-of-distribution warning when live features leave the training envelope.
- Optional downstream Kalman/particle-filter benchmark to quantify localization impact.

The highest-value near-term additions are event annotations, replay, transport-loss counters, and radio diagnostics. They improve both engineering reliability and the validity of every later model comparison.