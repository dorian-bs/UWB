# Telemetry-First Noise Characterization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add DW3000 diagnostics to range telemetry and collect higher-quality LOS/NLOS datasets that unlock richer downstream noise analysis.

**Architecture:** Keep existing ownership boundaries: STM32 emits richer telemetry, ESP32 remains transport-only, and host handles parse/store/replay/analysis. Introduce telemetry schema version 2 (v2) with additive diagnostics while preserving v1 compatibility. Validate end-to-end with parser tests, replay-path tests, firmware compile harness checks, and project verification.

**Tech Stack:** STM32 C (DW3000 API), Python 3.11+ (FastAPI host), pytest, Ruff, Make targets.

---

## File structure map (planned changes)

- Modify: `/Users/dorianbesson/EDOC/UWB/epuck-stm32-main/ThirdParty/qorvo_dw3/platform/qorvo_port.c`
  - Emit telemetry `v:2` with additive diagnostics fields.
  - Read DW3000 diagnostics at successful range report handling.
- Modify: `/Users/dorianbesson/EDOC/UWB/host/uwb_lab/core.py`
  - Parse/validate v1 and v2 range records.
  - Validate diagnostics field presence/ranges for v2.
- Modify: `/Users/dorianbesson/EDOC/UWB/host/uwb_lab/app.py`
  - Preserve v2 diagnostics in replay compaction path.
- Modify: `/Users/dorianbesson/EDOC/UWB/host/tests/test_core.py`
  - Add failing/passing parser tests for v2 and malformed diagnostics.
- Modify: `/Users/dorianbesson/EDOC/UWB/host/tests/test_radio.py`
  - Add replay-preservation test for diagnostics fields.
- Modify: `/Users/dorianbesson/EDOC/UWB/docs/ARCHITECTURE.md`
  - Document telemetry v2 schema and compatibility behavior.
- Modify: `/Users/dorianbesson/EDOC/UWB/docs/IMPLEMENTATION_STATUS.md`
  - Reflect availability/limits of diagnostics telemetry.
- Modify: `/Users/dorianbesson/EDOC/UWB/docs/EXPERIMENTS_AND_VALIDATION.md`
  - Add campaign protocol checks that depend on new diagnostics.

---

### Task 1: Lock telemetry v2 contract in host tests first

**Files:**
- Modify: `/Users/dorianbesson/EDOC/UWB/host/tests/test_core.py`
- Modify: `/Users/dorianbesson/EDOC/UWB/host/tests/test_radio.py`

- [ ] **Step 1: Write failing parser tests for v2 acceptance**

Add to `test_core.py`:

```python
VALID_LINE_V2 = json.dumps(
    {
        "type": "range",
        "v": 2,
        "board": 1,
        "peer": 2,
        "seq": 8,
        "time_ms": 1300,
        "raw_m": 2.12,
        "corrected_m": 2.10,
        "valid": True,
        "radio_revision": 7,
        "ipatov_power": 123456,
        "ipatov_f1": 1000,
        "ipatov_f2": 2000,
        "ipatov_f3": 3000,
        "ipatov_fp_index": 512,
        "ipatov_accum_count": 128,
        "cia_diag1": 42,
    }
)

def test_parses_firmware_range_message_v2(self) -> None:
    sample = parse_range_line(VALID_LINE_V2)
    self.assertIsNotNone(sample)
    self.assertEqual(sample["v"], 2)
    self.assertEqual(sample["ipatov_power"], 123456)
```

- [ ] **Step 2: Write failing parser rejection tests for malformed v2 diagnostics**

Add to `test_core.py`:

```python
def test_rejects_v2_when_diagnostics_missing_or_invalid(self) -> None:
    missing = json.loads(VALID_LINE_V2)
    del missing["ipatov_f1"]
    self.assertIsNone(parse_range_line(json.dumps(missing)))

    wrong_type = json.loads(VALID_LINE_V2)
    wrong_type["ipatov_power"] = -1
    self.assertIsNone(parse_range_line(json.dumps(wrong_type)))
```

- [ ] **Step 3: Write failing replay preservation test for diagnostics**

Add to `test_radio.py` near replay tests:

```python
def test_replay_preserves_v2_diagnostics(monkeypatch, tmp_path):
    from uwb_lab import app as app_module

    line = {
        "type": "range",
        "v": 2,
        "board": 1,
        "peer": 2,
        "seq": 1,
        "time_ms": 200,
        "raw_m": 2,
        "corrected_m": 2,
        "valid": True,
        "ipatov_power": 123,
        "ipatov_f1": 10,
        "ipatov_f2": 11,
        "ipatov_f3": 12,
        "ipatov_fp_index": 256,
        "ipatov_accum_count": 64,
        "cia_diag1": 99,
    }
    path = tmp_path / "replay.ndjson"
    path.write_text(json.dumps(line) + "\n")
    monkeypatch.setenv("UWB_REPLAY_FILE", str(path))

    async def scenario():
        source = app_module.replay_source()
        try:
            sample = json.loads(await anext(source))
            assert sample["ipatov_power"] == 123
            assert sample["cia_diag1"] == 99
        finally:
            await source.aclose()

    asyncio.run(scenario())
```

- [ ] **Step 4: Run targeted tests and confirm they fail**

Run:

```bash
cd /Users/dorianbesson/EDOC/UWB
pytest host/tests/test_core.py host/tests/test_radio.py -k "v2 or diagnostics or replay_preserves_v2" -q
```

Expected: FAIL (parser/replay path does not support v2 diagnostics yet).

- [ ] **Step 5: Commit failing-test checkpoint**

```bash
cd /Users/dorianbesson/EDOC/UWB
git add host/tests/test_core.py host/tests/test_radio.py
git commit -m "test: define telemetry v2 parser and replay expectations" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 2: Implement host-side v2 parser support

**Files:**
- Modify: `/Users/dorianbesson/EDOC/UWB/host/uwb_lab/core.py`
- Test: `/Users/dorianbesson/EDOC/UWB/host/tests/test_core.py`

- [ ] **Step 1: Add bounded integer helper for diagnostics**

In `core.py`, add:

```python
def _bounded_int(value: Any, name: str, lower: int, upper: int) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if not lower <= value <= upper:
        raise ValueError(f"{name} must be between {lower} and {upper}")
    return value
```

- [ ] **Step 2: Add v2 diagnostics field map and parser branch**

In `core.py`, define:

```python
V2_DIAGNOSTIC_FIELDS: dict[str, tuple[int, int]] = {
    "ipatov_power": (0, 0xFFFFFFFF),
    "ipatov_f1": (0, 0xFFFFFFFF),
    "ipatov_f2": (0, 0xFFFFFFFF),
    "ipatov_f3": (0, 0xFFFFFFFF),
    "ipatov_fp_index": (0, 0xFFFF),
    "ipatov_accum_count": (0, 0xFFFF),
    "cia_diag1": (0, 0xFFFFFFFF),
}
```

Update `parse_range_line`:

```python
if not isinstance(message, dict) or message.get("type") != "range":
    return None
version = message.get("v")
if version not in (1, 2):
    return None

sample = {
    "type": "range",
    "v": int(version),
    "board": int(message["board"]),
    "peer": int(message["peer"]),
    "seq": int(message["seq"]),
    "time_ms": int(message["time_ms"]),
    "raw_m": _finite_float(message["raw_m"], "raw_m"),
    "corrected_m": _finite_float(message["corrected_m"], "corrected_m"),
    "valid": message["valid"] is True,
    "host_time": utc_now(),
}

if sample["v"] == 2:
    for field, (lower, upper) in V2_DIAGNOSTIC_FIELDS.items():
        sample[field] = _bounded_int(message[field], field, lower, upper)
```

- [ ] **Step 3: Keep radio revision validation unchanged for v1/v2**

Retain and reuse existing block:

```python
if "radio_revision" in message:
    revision = message["radio_revision"]
    if type(revision) is not int or not 0 <= revision <= 0xFFFFFFFF:
        return None
    sample["radio_revision"] = revision
```

- [ ] **Step 4: Run targeted parser tests and confirm pass**

```bash
cd /Users/dorianbesson/EDOC/UWB
pytest host/tests/test_core.py -q
```

Expected: PASS for new v2 tests and existing v1 behavior.

- [ ] **Step 5: Commit parser implementation**

```bash
cd /Users/dorianbesson/EDOC/UWB
git add host/uwb_lab/core.py host/tests/test_core.py
git commit -m "feat(host): parse and validate telemetry v2 diagnostics" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 3: Preserve diagnostics in replay and recording pipelines

**Files:**
- Modify: `/Users/dorianbesson/EDOC/UWB/host/uwb_lab/app.py`
- Test: `/Users/dorianbesson/EDOC/UWB/host/tests/test_radio.py`

- [ ] **Step 1: Add replay keep-list for v2 diagnostics**

In `app.py` (inside `replay_source`), after `compact` creation:

```python
for optional_key in (
    "radio_revision",
    "ipatov_power",
    "ipatov_f1",
    "ipatov_f2",
    "ipatov_f3",
    "ipatov_fp_index",
    "ipatov_accum_count",
    "cia_diag1",
):
    if optional_key in message:
        compact[optional_key] = message[optional_key]
```

- [ ] **Step 2: Run replay-focused tests**

```bash
cd /Users/dorianbesson/EDOC/UWB
pytest host/tests/test_radio.py -k "replay_preserves" -q
```

Expected: PASS including new diagnostics replay test.

- [ ] **Step 3: Commit replay update**

```bash
cd /Users/dorianbesson/EDOC/UWB
git add host/uwb_lab/app.py host/tests/test_radio.py
git commit -m "feat(host): preserve telemetry v2 diagnostics in replay source" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 4: Emit telemetry v2 diagnostics from firmware

**Files:**
- Modify: `/Users/dorianbesson/EDOC/UWB/epuck-stm32-main/ThirdParty/qorvo_dw3/platform/qorvo_port.c`
- Test: `/Users/dorianbesson/EDOC/UWB/host/tests/test_firmware_control.py`

- [ ] **Step 1: Add diagnostics storage to link context**

In `qorvo_link_ctx_t` in `qorvo_port.c`, add:

```c
    uint32_t last_ipatov_power;
    uint32_t last_ipatov_f1;
    uint32_t last_ipatov_f2;
    uint32_t last_ipatov_f3;
    uint16_t last_ipatov_fp_index;
    uint16_t last_ipatov_accum_count;
    uint32_t last_cia_diag1;
```

- [ ] **Step 2: Read diagnostics when REPORT is accepted on initiator**

Inside `qorvo_cb_rx_ok` in REPORT branch (before `qorvo_log_ds_twr_result(...)`), add:

```c
dwt_rxdiag_t diag = {0};
dwt_readdiagnostics(&diag);
g_link.last_ipatov_power = diag.ipatovPower;
g_link.last_ipatov_f1 = diag.ipatovF1;
g_link.last_ipatov_f2 = diag.ipatovF2;
g_link.last_ipatov_f3 = diag.ipatovF3;
g_link.last_ipatov_fp_index = diag.ipatovFpIndex;
g_link.last_ipatov_accum_count = diag.ipatovAccumCount;
g_link.last_cia_diag1 = diag.ciaDiag1;
```

- [ ] **Step 3: Bump emitted JSON from v1 to v2 with diagnostics**

In `qorvo_log_ds_twr_result(...)`, replace telemetry print with:

```c
qorvo_log_printf(
    "{\"type\":\"range\",\"v\":2,\"board\":%u,\"peer\":%u,\"seq\":%lu,\"time_ms\":%lu,"
    "\"raw_m\":%.4f,\"corrected_m\":%.4f,\"valid\":true,\"radio_revision\":%lu,"
    "\"ipatov_power\":%lu,\"ipatov_f1\":%lu,\"ipatov_f2\":%lu,\"ipatov_f3\":%lu,"
    "\"ipatov_fp_index\":%u,\"ipatov_accum_count\":%u,\"cia_diag1\":%lu}\r\n",
    g_link.board_id,
    g_link.peer_board_id,
    (unsigned long)g_link.current_exchange_counter,
    (unsigned long)HAL_GetTick(),
    distance_m,
    corrected_distance_m,
    (unsigned long)qorvo_control_state()->revision,
    (unsigned long)g_link.last_ipatov_power,
    (unsigned long)g_link.last_ipatov_f1,
    (unsigned long)g_link.last_ipatov_f2,
    (unsigned long)g_link.last_ipatov_f3,
    (unsigned int)g_link.last_ipatov_fp_index,
    (unsigned int)g_link.last_ipatov_accum_count,
    (unsigned long)g_link.last_cia_diag1);
```

- [ ] **Step 4: Run firmware compile harness checks**

```bash
cd /Users/dorianbesson/EDOC/UWB
pytest host/tests/test_firmware_control.py -q
```

Expected: PASS (`qorvo_port.c` still compiles in native syntax check path).

- [ ] **Step 5: Commit firmware telemetry update**

```bash
cd /Users/dorianbesson/EDOC/UWB
git add epuck-stm32-main/ThirdParty/qorvo_dw3/platform/qorvo_port.c host/tests/test_firmware_control.py
git commit -m "feat(firmware): emit telemetry v2 with DW3000 diagnostics" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 5: Update contracts and experiment guidance docs

**Files:**
- Modify: `/Users/dorianbesson/EDOC/UWB/docs/ARCHITECTURE.md`
- Modify: `/Users/dorianbesson/EDOC/UWB/docs/IMPLEMENTATION_STATUS.md`
- Modify: `/Users/dorianbesson/EDOC/UWB/docs/EXPERIMENTS_AND_VALIDATION.md`

- [ ] **Step 1: Document telemetry v2 schema**

In `ARCHITECTURE.md`, replace v1 sample block with a v2 sample and add compatibility paragraph:

```json
{"type":"range","v":2,"board":1,"peer":2,"seq":42,"time_ms":41000,"raw_m":2.1234,"corrected_m":2.1012,"valid":true,"radio_revision":5,"ipatov_power":123456,"ipatov_f1":1000,"ipatov_f2":2000,"ipatov_f3":3000,"ipatov_fp_index":512,"ipatov_accum_count":128,"cia_diag1":42}
```

Add text: host accepts v1 and v2; v2 diagnostics are required when `v=2`.

- [ ] **Step 2: Update implementation status**

In `IMPLEMENTATION_STATUS.md`, add that compact telemetry now includes diagnostics (v2), and note any remaining limitations (no full CIR snapshot yet, diagnostics tied to successful exchanges only).

- [ ] **Step 3: Add acquisition quality checks to experiments doc**

In `EXPERIMENTS_AND_VALIDATION.md`, add explicit run acceptance bullets:

```markdown
- Reject runs where diagnostics coverage in accepted samples is below 95%.
- Report malformed-line rate per run and investigate outliers before model fitting.
- Keep at least three independently restarted sessions per condition.
```

- [ ] **Step 4: Commit documentation updates**

```bash
cd /Users/dorianbesson/EDOC/UWB
git add docs/ARCHITECTURE.md docs/IMPLEMENTATION_STATUS.md docs/EXPERIMENTS_AND_VALIDATION.md
git commit -m "docs: describe telemetry v2 diagnostics and data-quality protocol" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 6: Full verification and campaign readiness check

**Files:**
- Verify all files changed in Tasks 1-5

- [ ] **Step 1: Run focused host test subset**

```bash
cd /Users/dorianbesson/EDOC/UWB
pytest host/tests/test_core.py host/tests/test_radio.py host/tests/test_firmware_control.py -q
```

Expected: PASS.

- [ ] **Step 2: Run repository verification target**

```bash
cd /Users/dorianbesson/EDOC/UWB
make verify
```

Expected: lint and tests pass; dashboard smoke remains green.

- [ ] **Step 3: Capture rollout notes for lab run**

Record in local untracked `LAB_NOTES.md`:

```markdown
- Firmware flashed: telemetry v2 build hash for both boards
- Planned LOS distances and NLOS materials
- Target per-condition session count (>=3 independent restarts)
- Diagnostics coverage check target (>95%)
```

- [ ] **Step 4: Commit final integration checkpoint**

```bash
cd /Users/dorianbesson/EDOC/UWB
git add epuck-stm32-main/ThirdParty/qorvo_dw3/platform/qorvo_port.c host/uwb_lab/core.py host/uwb_lab/app.py host/tests/test_core.py host/tests/test_radio.py docs/ARCHITECTURE.md docs/IMPLEMENTATION_STATUS.md docs/EXPERIMENTS_AND_VALIDATION.md
git commit -m "feat: telemetry-v2 diagnostics pipeline for noise characterization" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

