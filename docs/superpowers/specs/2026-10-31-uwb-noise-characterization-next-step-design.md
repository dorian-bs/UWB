# UWB noise characterization — next step design (telemetry-first)

Date: 2026-10-31  
Status: Draft approved in chat (pending final user review)

## 1) Goal

Collect higher-quality and richer UWB ranging data so later noise analysis can separate geometry effects from radio/propagation effects with better confidence.

Primary immediate objective:
- Expand telemetry at the firmware/host contract boundary.
- Then run disciplined static LOS and static NLOS campaigns using the expanded record.

## 2) Scope and boundaries

In scope:
- STM32 telemetry extension for per-exchange diagnostics.
- Host parser/recording/replay/test/documentation updates for the new schema version.
- A repeatable acquisition protocol for:
  - static LOS distance sweeps
  - static NLOS material sweeps

Out of scope:
- Moving analysis/modeling onto microcontrollers.
- ESP32 bridge behavior changes.
- Advanced model implementation in this phase (focus is data quality + observability first).

Ownership remains:
- STM32: ranging + telemetry emission
- ESP32: transport only
- Host: parsing, storage, metadata, analysis

## 3) Telemetry contract direction

Current contract is compact range telemetry version 1.  
This phase introduces version 2 for richer observables.

Design constraints:
- Preserve existing core fields (`type`, `board`, `peer`, `seq`, `time_ms`, `raw_m`, `corrected_m`, `valid`) semantics.
- Add diagnostics as additive fields in v2.
- Update firmware + host + fixtures + docs in one coordinated change.
- Keep raw measurement immutability (`raw_m` never overwritten by corrections).

Initial diagnostic candidates to include in v2 (exact names finalized during implementation):
- received signal strength indicator(s)
- first-path power/quality metrics
- PHY/status flags relevant to confidence assessment
- optional CIR quality summaries (if feasible without heavy payload/cost)

## 4) Data flow and error handling

Flow:
1. STM32 emits compact JSON line per successful exchange (v2).
2. Host parser validates + ingests line.
3. Recorder persists to session NDJSON with metadata context.
4. Replay and analysis consume saved records.

Error handling policy:
- No silent fallback to success-shaped defaults for malformed or unsupported records.
- Invalid lines are explicitly counted/flagged through existing host health counters.
- Unsupported telemetry versions are reported explicitly and handled via parser rules.
- Diagnostics missing from otherwise valid records are surfaced in quality metrics.

## 5) Acquisition protocol for this phase

### 5.1 Static LOS distance sweep
- Use multiple representative distances in operating range.
- At each condition: at least 3 independently restarted sessions.
- Keep orientation/mounting controlled and documented.

### 5.2 Static NLOS material sweep
- Use selected materials/obstructions relevant to target environment.
- Pair with matching LOS reference captures before/after where possible.
- Record obstruction identity/placement consistently.

### 5.3 Per-session metadata minimum
- session/scenario identifiers
- measured distance + uncertainty
- LOS/NLOS label and obstruction/material descriptor
- orientation/mounting notes
- active radio settings snapshot
- operator notes on anomalies

## 6) Validation and acceptance

Pre-hardware validation:
- `make verify` passes before hardware campaign.
- Parser/fixture tests include valid + malformed v2 samples.
- Replay + analysis ingestion remain stable under the updated contract.

Campaign acceptance criteria:
- ≥3 independently restarted sessions per condition for LOS/NLOS sweeps.
- Diagnostics present in >95% of accepted range records.
- Low and explainable malformed-line rate.
- No unexplained major recording gaps in selected runs.

Deliverable of this phase:
- A multi-session, high-quality dataset with richer observables, ready for deeper distributional/spectral/model-comparison analysis in the next phase.

## 7) Risks and mitigations

Risk: schema change causes parser/fixture drift.  
Mitigation: coordinated firmware-host-fixture-doc update in one change and contract tests.

Risk: richer telemetry increases debug/transport load.  
Mitigation: keep payload compact, prioritize high-value diagnostics first, validate stability on real sessions.

Risk: inconsistent lab execution reduces comparability.  
Mitigation: strict run template with required metadata fields and restart-based independence.
