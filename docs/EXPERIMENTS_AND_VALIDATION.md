# Experiments and Validation

These are research requirements, not a claim that all metadata, checks, or models are implemented.
See [status and roadmap](IMPLEMENTATION_STATUS.md) for progress.

## Measurement protocol

Record at acquisition time; do not retrospectively infer available metadata. Each manifest should include:

- Session ID, UTC start/end, operator, notes; firmware commit/build IDs for both nodes and bridge.
- Device IDs/roles, antenna IDs/orientations/mounting/delays.
- Channel, preamble, data rate, PRF, STS, TX power, target rate; available temperature/power conditions.
- Environment ID and sketch/photo reference (outside Git if sensitive); planned scenarios/deviations.
- Ground-truth instrument, resolution, uncertainty, frame, and synchronization method.

Run independent repeat sessions rather than one long mixed recording:

| Experiment | Design | Purpose |
| --- | --- | --- |
| Static LOS | Several operating-range distances, multiple orientations, **at least three independently restarted sessions per condition**, enough samples for tails/autocorrelation | Antenna-delay calibration, distance bias/variance, outliers, temporal stability |
| Static NLOS | Matched LOS before/after; selected distances with human body, wood/drywall, metal, relevant lab materials; record obstruction identity/thickness/position and complete/partial first-path blockage | Material-specific bias/variance and diagnostic sensitivity |
| LOS/NLOS transitions | Independently timestamp insertion/removal; vary speed/dwell; repeat both directions | Temporal coherence, change-point accuracy, HMM behavior, delay |
| Dynamic geometry | Repeatable motion with independent reference; constant speed, stop/start, approach/recede; separate geometry and obstruction changes | Distinguish model lag/geometry error from radio noise |
| Generalization | Change room/layout, mounting, day, obstruction placement; seal sessions from model selection | Deployment-shift robustness |

## Ground truth and priors

Measured distance with instrument uncertainty may be ground truth. Tape marks, robot odometry,
and manual positions carry their own uncertainty; an unverified estimate is only a prior.
Timestamp prior revisions without retroactively changing source records.
For motion, prefer a shared ground-truth trigger; otherwise estimate/report temporal alignment uncertainty.

## Session-close data quality

Automatically check:

- Required metadata/units; unique `(device_id, boot_id, sequence)`; gaps, duplicates, reordering, boot changes.
- Monotonic device time per boot; bounds/non-finite ranges flagged, never silently removed.
- Expected count versus valid/invalid count; unambiguous prior/annotation intervals; archival hashes.

Declare exclusion rules before evaluation; report counts by reason.

## Splits and baselines

Adjacent samples are correlated: **no random row-level split for headline results**.
Use complete training sessions for fitting, distinct validation sessions for features/thresholds/tuning,
and unopened test sessions, preferably an unseen environment/day.
Where possible add session/environment-grouped cross-validation to the frozen test set.

### Baselines

| Task | Baselines |
| --- | --- |
| Ranging | No correction (`corrected = raw`); global training-set Gaussian bias/variance; distance-binned Gaussian with training-fixed bins; causal median/MAD over fixed past-only window |
| LOS/NLOS | Majority class; causal residual/rolling-variance threshold; simple change-point detector; logistic regression on a small documented causal feature set |

Advanced methods must outperform relevant simple baselines on independent sessions.

## Candidate models

| Model | Best use | Caution |
| --- | --- | --- |
| AR/ARIMA/state space | Short-term residual dynamics | Stationarity/changing geometry |
| HMM | Persistent latent LOS/NLOS regimes | State interpretation/dwell assumptions |
| Gaussian process | Bias/uncertainty versus geometry/diagnostics | Cubic scaling without sparse approximation |
| Change point | Transition detection with few labels | Threshold sensitivity/delay |
| Tree/linear classifier | Interpretable diagnostic baseline | Future-window feature leakage |
| Temporal neural network | Complex nonlinear sequences | Data volume/calibration/overfit |

Start interpretable; justify complexity with residual diagnostics and learning curves.
Implementation order and model-provenance requirements live in the
[roadmap](IMPLEMENTATION_STATUS.md#phase-5---losnlos-and-temporal-models).

## Metrics

| Task | Required reporting |
| --- | --- |
| Range/bias | Signed bias by condition/distance; MAE/RMSE; median and 95th/99th percentile absolute error; probabilistic negative log likelihood; interval coverage/width; corrected-residual autocorrelation |
| LOS/NLOS | Per-class precision/recall/F1; balanced accuracy/confusion matrix; AUROC and especially AUPRC for imbalance; transition delay; false transitions/alarms per minute; probability calibration |

Bootstrap confidence intervals **by session**, not as if correlated samples were independent.
Live output uses bounded causal windows and provisional labels; clearly mark provisional predictions.
Final results run from closed files with fixed configuration. Never write predictions into raw telemetry.

## Follow-on features

- Camera/event-button labels with privacy policy; odometry/motion-capture dynamic ground truth.
- Event-triggered CIR snapshots to limit bandwidth; antenna-delay calibration wizard.
- Device/firmware/environment quality dashboard; uncertain-interval active-learning review.
- Out-of-distribution warnings; downstream Kalman/particle-filter localization benchmark.

Near-term value: annotations, replay, loss counters, and radio diagnostics improve both reliability
and every subsequent model comparison.
