# Implementation Status

Last updated: 2026-09-28.

## Working now

- STM32 TX/RX roles are selected with `TxDebug` and `RxDebug` CMake presets.
- STM32 TX emits one compact JSON range record after each successful DS-TWR exchange.
- ESP32 forwards UART to TCP and TCP to UART; Wi-Fi settings are configured with `menuconfig`.
- ESP32-S3 firmware builds successfully with ESP-IDF v5.5.5.
- Python host supports demo, TCP, USB serial, and recorded-file replay sources.
- Browser UI shows live range, expected distance, residual, rolling bias/spread, and stream health.
- Direct-distance and 3D-position priors apply immediately and receive revision numbers.
- Sessions record telemetry, prior changes, and a small manifest under `data/sessions/`.
- Core parser, prior, residual, and recorder tests are present.

## Next essentials

- Test both flashed STM32 boards and the ESP32 bridge together on hardware.
- Confirm the ESP32 UART pins and discover its IP address on the lab network.
- Collect fixed-distance LOS recordings and calibrate antenna delays.
- Add timestamped LOS/NLOS event labels to the UI and recording.
- Expose useful DW3000 signal-quality diagnostics in the JSON record.
- Add a small offline analysis script for histogram, bias, standard deviation, and autocorrelation.

## Later if time permits

- Add one simple LOS/NLOS baseline, then compare an HMM or change-point method.
- Add a Gaussian-process or autoregressive noise model only after enough data exists.
- Evaluate whether correction improves a basic localization estimate.

## Known limitations

- Range telemetry is emitted only by the TX/initiator and currently has no radio diagnostics.
- Device output still mixes debug text and JSON; the host safely ignores non-JSON lines.
- The ESP32 accepts one unauthenticated TCP client and is suitable only for a trusted lab network.
- The generated ESP32 `sdkconfig` still needs local Wi-Fi credentials before flashing.
- Priors reset when the host restarts unless they were recorded in a session.

Keep this file current when a feature becomes usable or a limitation is removed.