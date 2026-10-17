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

## Radio and sampling parameter reference

The **Radio / sampling** panel changes physical DW3000 settings on both USB-connected
STM32 boards. It does not directly set noise, bias, or a simulated signal amplitude.
Those are measured outcomes of the radio configuration, geometry, propagation, and
receiver behavior. See the [host guide](../host/README.md#live-radio-settings-two-usb-connected-boards)
for connection and firmware requirements, and the
[control contract](ARCHITECTURE.md#usb-radio-control-implemented-protocol-version-1)
for command framing and acknowledgements.

The effects below are engineering expectations, **not results measured on these boards**.
A configuration can improve packet reception without improving distance accuracy.
Performance and safe/regulatory RF settings must be validated on the actual hardware.

### Supported settings and defaults

| UI parameter | Unit / encoding | Supported values | Boot behavior |
| --- | --- | --- | --- |
| Requested ranging rate | Exchanges attempted per second, Hz | 0.1-10 | 1 Hz / 1000 ms initiator poll period |
| Channel | UWB channel number | 5 or 9 | 9 |
| Preamble | Synchronization symbols per transmitted packet | 64, 128, 256, 512, 1024 | 128 |
| Data rate | Packet payload bit rate | 850 kbps or 6.8 Mbps | 6.8 Mbps |
| TX power register | Raw unsigned 32-bit DW3000 encoding, displayed in hexadecimal | `0x00000000`-`0xFFFFFFFF`; this numeric range is not an approved RF-power range | Read from the board's actual register; no universal calibrated power is assumed |

The requested TX power value is applied to **both** boards. There is no independent
initiator/responder power control in the current UI. Settings are volatile: rebooting
a board restores its firmware defaults, not the last browser form values.

### Requested ranging rate and observed sample rate

The requested rate controls when the initiator starts a new double-sided two-way
ranging (DS-TWR) exchange. It is not an ADC sampling frequency or the packet bit rate.
An exchange contains multiple packets and produces a distance only after a successful
completion; the responder does not generate an independent stream of range samples.

The host converts the requested rate to an integer-millisecond period:

```text
period_ms = round(1000 / requested_hz)
nominal_hz = 1000 / period_ms
```

Examples: 0.1 Hz gives 10000 ms, 1 Hz gives 1000 ms, 5 Hz gives 200 ms, and
10 Hz gives 100 ms. A 3 Hz request rounds to 333 ms, approximately 3.003 Hz.
New polls only start when the exchange state permits them, so this period does not
guarantee a completed measurement at every deadline. The 10 Hz UI limit is a command
guard, not a certified maximum throughput.

Higher rates resolve faster motion and transitions, and collect more observations
per unit time. They also increase exchange activity, airtime, logging, and transport
load. They do not inherently reduce single-measurement error. More samples need not
mean proportionally more independent information: neighboring errors may be correlated.

**Observed rate** counts successful valid range records arriving at the host, using
up to the latest 100 arrival times within 30 seconds:

```text
observed_hz = (number_of_recent_arrivals - 1) / (last_arrival - first_arrival)
```

Before enough arrivals exist, the UI shows `--`. Stale arrivals eventually show zero;
the staleness threshold depends on the recent cadence so slow requested rates are
not immediately treated as stopped. Dual-USB telemetry retains its arrival timestamps
through buffering: draining a queue is not interpreted as a burst of new measurements.
This is still a host-arrival estimate, not an exact measurement of radio scheduling.
USB buffering, failed exchanges, and dropped input can affect it. Compare it with
requested cadence, errors, and loss counters rather than treating the difference as
an exact packet-loss percentage.

### Channel

Channel selects the radio frequency band. Channel 5 is centered at approximately
6.49 GHz and channel 9 at approximately 7.99 GHz. Both radios must use compatible
channel settings to communicate.

Changing channel can change attenuation, obstruction penetration, multipath,
interference exposure, antenna response, and RF calibration behavior. At otherwise
comparable conditions, lower frequency generally has lower free-space path loss, but
that alone does not determine which channel works better in a room or with a particular
antenna. Neither channel should be labelled universally "more accurate."

Possible observations include a shift in mean range, a different spread, more or fewer
outliers, and changes in successful sample rate. Keep geometry, mounting, orientation,
and obstacles fixed when comparing channels. Channel changes require hardware RF and
calibration validation; the UI does not automatically recalibrate antenna delays.

### Preamble length

The preamble is a known symbol sequence preceding a packet. The receiver uses it for
packet detection and synchronization. The number shown in the UI is a symbol count,
not bytes, metres, or a sample-averaging window.

Longer preambles generally give the receiver more opportunity to acquire a weak signal,
at the cost of longer airtime and greater energy per packet under comparable conditions.
Because DS-TWR exchanges several packets, this overhead occurs more than once per
measurement. Shorter preambles reduce overhead but may be less robust on marginal links.

The firmware automatically selects the receiver's preamble acquisition chunk (PAC):

| Preamble symbols | PAC symbols |
| --- | --- |
| 64 | 4 |
| 128 | 8 |
| 256 | 16 |
| 512 or 1024 | 32 |

It also adjusts the start-of-frame detection timeout. These are dependent receiver
settings, not extra UI controls.

A longer preamble may improve exchange success and change range repeatability.
It is not equivalent to averaging several distance measurements, and it does not
guarantee that the receiver identifies an unobstructed direct path. Multipath and
non-line-of-sight (NLOS) bias can remain even when reception is very reliable.

### Data rate

Data rate is how fast packet payload bits are transmitted: 850 kbps or 6.8 Mbps.
It is separate from the number of complete distance measurements per second.
Selecting 6.8 Mbps does not mean 6.8 million distance samples per second.

Lower-rate operation generally favors sensitivity/robustness, with longer packet airtime.
Higher-rate operation shortens payload airtime and can leave more time for other activity.
The preamble, protocol header, reply delays, and software overhead remain, so increasing
payload rate does not increase ranging rate by the same factor.

The firmware also chooses a compatible start-of-frame delimiter (SFD): a 16-symbol
DW delimiter at 850 kbps, or the 8-symbol IEEE 802.15.4a delimiter at 6.8 Mbps.
Both boards are updated together. The distance calculation still uses DS-TWR timestamps;
a bit-rate change is not a direct rescaling of metres.

Look for changes in reception success, outlier frequency, bias, and spread. A lower
data rate is not automatically a more accurate distance measurement.

### TX power register

This field is the raw DW3000 `TX_POWER` register, entered as `0x` followed by up to
eight hexadecimal digits. The host sends its unsigned integer value to the firmware.
For example, `0xA0A0A0A0` is a register encoding, **not** a claim of a particular
transmitted power, and it is not a recommended value for every board.

The register contains packed hardware power-control fields rather than one linear
amplitude number. A numerically larger hexadecimal value does not reliably mean
greater RF power. Converting it to calibrated output power requires the relevant
DW3000 documentation, module/antenna characteristics, channel, and measurements.
The UI does not provide a dBm conversion or certify regulatory compliance.

In a weak link, increasing actual received signal strength can improve detection,
reduce failed exchanges, and potentially reduce range spread. Too much received
power can saturate the receiver or introduce power-dependent range bias. A reliable
packet link can therefore still produce systematically wrong distances.

Do not sweep arbitrary register values to search for the largest signal. Use documented,
lab-approved settings within applicable emission limits. TX power changes do not move
the boards or change the speed of light; any resulting range shift is measurement behavior.

### Interpreting the displayed measurements

| Trace / readout | Meaning | Interpretation during a settings comparison |
| --- | --- | --- |
| Raw range | Distance calculated from the exchange timestamps before a range correction | Preserve it as acquired; compare its distribution between configurations |
| Corrected range | Firmware-supplied distance after its configured correction behavior | May equal raw range when no correction is active; the host does not invent a correction |
| Expected distance | Current direct-distance or position-based geometry prior | A reference supplied by the operator, not a radio measurement |
| Residual | `corrected_m - expected_m` | Positive means overestimation relative to the prior; negative means underestimation |
| Rolling bias | Mean of the latest 20 residuals | Tracks systematic signed error; can also reflect a wrong prior or physical movement |
| Rolling spread | Population standard deviation of the latest 20 residuals | Tracks repeatability, not accuracy or a calibrated confidence interval |

Changing a radio parameter does not rewrite earlier samples, update the geometry prior,
or automatically remove bias. A range shift while the boards remain stationary may
indicate configuration-dependent error. Under motion, a shift can instead be real distance
change or a stale prior. Low spread with a large residual is precise but inaccurate.

The rolling window is **20 samples**, not a fixed time interval: its duration is roughly
20 seconds at 1 successful sample/s, 4 seconds at 5/s, or 2 seconds at 10/s.
Changing rate therefore changes the time scale represented by these readouts.
Radio changes do not reset the residual window, so the first subsequent rolling values
mix configurations until 20 new residuals have arrived. Prior changes reset that window.

Similarly, **Last N samples** changes the displayed sample count, not acquisition rate.
At 180 samples, the visible duration is roughly three minutes at 1 Hz or 18 seconds
at 10 Hz. Plot height, lines/points, trace selection, and axis limits only change presentation.
**Pause view** freezes the plots/readouts; it does not stop acquisition or recording.

### Applying settings and keeping comparison data

Application briefly pauses ranging: the host reads both states, pauses both, applies
and verifies matching settings, then resumes the responder before the initiator.
The interruption is expected and is not evidence of poorer RF performance.
On failure, rollback attempts restore each reachable board; a pair that cannot be
confirmed compatible is not deliberately resumed. An unreachable board's state is
unknown, not assumed paused.

Recordings retain configuration snapshots/outcomes in `radio.ndjson`, and new firmware
range records carry `radio_revision`. Use these to separate configuration intervals;
do not relabel old measurements using the final settings. Revisions are local to a
board boot, so record restarted boards as separate sessions until boot IDs are implemented.
The current firmware publishes successful ranges, not a complete failed-attempt log;
absence of a sample alone does not identify which packet or component failed.

For an interpretable first comparison:

1. Fix the boards, antennas, orientation, and environment; measure separation and set
   a prior with appropriate uncertainty.
2. Record a baseline at 1 Hz, channel 9, preamble 128, and 6.8 Mbps, keeping the
   board's read-back TX power unless a documented lab setting is required.
3. Change one parameter at a time. For example, compare preambles 128, 256, and 512
   before separately varying requested rate.
4. Record equal-duration intervals, with an explicitly defined settling exclusion;
   wait for at least 20 new residuals before interpreting the live rolling readouts.
   Repeat conditions in alternating or randomized order to separate configuration
   effects from drift.
5. Compare residual bias, spread, absolute-error tails, outliers, achieved rate, and
   loss/error counters. Report both duration and sample count; unequal rates produce
   unequal counts. Inspect temporal correlation rather than assuming independent samples.
6. Repeat independent sessions and matched LOS/NLOS conditions. Keep calibration
   data separate from evaluation data, and do not generalize a single room/distance result.

An increase in packet success, a smoother plotted line, or a smaller rolling spread
alone is insufficient evidence that a setting improves ranging accuracy.

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
