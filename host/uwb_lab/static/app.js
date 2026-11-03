const SERIES = [
  { key: "raw_m", family: "distance", color: "#8fa7b8", dash: [] },
  { key: "corrected_m", family: "distance", color: "#4fb3d1", dash: [] },
  { key: "expected_m", family: "distance", color: "#e0a73a", dash: [7, 4] },
  { key: "residual_m", family: "error", color: "#ff6b72", dash: [] },
  { key: "rolling_bias_m", family: "error", color: "#b08fe0", dash: [] },
  { key: "rolling_std_m", family: "error", color: "#3ecf9f", dash: [] },
  { key: "rx_power_dbm", family: "power", color: "#6cb4f0", dash: [] },
  { key: "fp_power_dbm", family: "power", color: "#f0b45a", dash: [] },
  { key: "fp_gap_db", family: "power", color: "#ff8a7a", dash: [] },
];
const DEFAULTS = {
  count: 180, height: 300, style: "lines", secondary: false, controls: true,
  series: ["corrected_m", "expected_m", "residual_m"],
  distance: { auto: true, min: 0, max: 4 },
  error: { auto: true, min: -0.1, max: 0.1 },
  power: { auto: true, min: -100, max: -60 },
  secondPlot: {
    count: 180, height: 300, style: "lines",
    series: ["residual_m"],
    distance: { auto: true, min: 0, max: 4 },
    error: { auto: true, min: -0.1, max: 0.1 },
  power: { auto: true, min: -100, max: -60 },
  },
};
const STORAGE_KEY = "uwb-lab-display-v1";

function validateView(value, label) {
  if (!value || !Number.isInteger(value.count) || value.count < 10 || value.count > 2000 ||
      !Number.isInteger(value.height) || value.height < 180 || value.height > 600 ||
      !["lines", "points", "both"].includes(value.style)) {
    throw new Error(`${label}: invalid display settings. Samples: 10-2000; height: 180-600 px.`);
  }
  return { count: value.count, height: value.height, style: value.style };
}

function validatePreferences(value) {
  if (!value || typeof value.secondary !== "boolean" || typeof value.controls !== "boolean") {
    throw new Error("Invalid display settings.");
  }
  // Preferences saved before independent plots have no secondPlot field; plot 2 then inherits plot 1's view.
  const { count, height, style, ...defaultAxes } = DEFAULTS.secondPlot;
  const second = value.secondPlot === undefined ? defaultAxes : value.secondPlot;
  return {
    ...validateView(value, "Plot 1"),
    secondary: value.secondary, controls: value.controls,
    ...validatePlotPreferences(value, "Plot 1"),
    secondPlot: {
      ...validateView({
        count: second?.count ?? value.count, height: second?.height ?? value.height,
        style: second?.style ?? value.style,
      }, "Plot 2"),
      ...validatePlotPreferences(second, "Plot 2"),
    },
  };
}

function validatePlotPreferences(value, label) {
  if (!value || !Array.isArray(value.series) || value.series.some((key) => !SERIES.some((s) => s.key === key))) {
    throw new Error(`${label}: invalid trace selection.`);
  }
  // Preferences saved before the dB axis was adjustable have no power field.
  if (value && value.power === undefined) value = { ...value, power: { ...DEFAULTS.power } };
  for (const family of ["distance", "error", "power"]) {
    const axis = value[family];
    if (!axis || typeof axis.auto !== "boolean" || !Number.isFinite(axis.min) ||
        !Number.isFinite(axis.max) || axis.min >= axis.max || !Number.isFinite(axis.max - axis.min)) {
      throw new Error(`${label}: ${family} limits must be finite, with minimum below maximum.`);
    }
  }
  return {
    series: [...new Set(value.series)],
    distance: { ...value.distance }, error: { ...value.error }, power: { ...value.power },
  };
}

function axisBounds(values, axis, includeZero = false) {
  if (!axis.auto) return [axis.min, axis.max];
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return includeZero ? [-0.01, 0.01] : [0, 1];
  let min = Math.min(...finite);
  let max = Math.max(...finite);
  if (includeZero) { min = Math.min(min, 0); max = Math.max(max, 0); }
  const margin = Math.max((max - min) * 0.1, includeZero ? 0.01 : 0.03);
  return [min - margin, max + margin];
}

function getPlotData(samples, settings) {
  const selected = SERIES.filter((s) => settings.series.includes(s.key));
  const bounds = {};
  for (const family of ["distance", "error", "power"]) {
    bounds[family] = axisBounds(
      selected.filter((s) => s.family === family).flatMap((s) => samples.map((sample) => sample[s.key])),
      settings[family], family === "error",
    );
  }
  return { selected, bounds };
}

function createViewState(limit = 2000) {
  let history = [];
  let frozen = null;
  return {
    add(sample) { history.push(sample); if (history.length > limit) history.shift(); },
    snapshot(samples) { history = samples.slice(-limit); },
    pause() { if (frozen === null) frozen = history.slice(); },
    resume() { frozen = null; },
    paused() { return frozen !== null; },
    visible(count) { return (frozen ?? history).slice(-count); },
    buffered() { return history.length; },
  };
}

function createCalibrationState() {
  return {
    running: false,
    knownDistanceM: null,
    targetSamples: 0,
    toleranceM: 0.01,
    captured: 0,
    skipped: 0,
    corrected: [],
    raw: [],
  };
}

function calibrationStart(state, { knownDistanceM, targetSamples, toleranceM = 0.01 }) {
  Object.assign(state, createCalibrationState(), {
    running: true,
    knownDistanceM,
    targetSamples,
    toleranceM,
  });
  return state;
}

function calibrationConsumeSample(state, sample) {
  if (!state?.running) return state;
  const corrected = sample?.corrected_m;
  if (sample?.valid !== true || !Number.isFinite(corrected)) {
    state.skipped += 1;
    return state;
  }
  state.corrected.push(corrected);
  if (Number.isFinite(sample?.raw_m)) state.raw.push(sample.raw_m);
  state.captured += 1;
  return state;
}

function calibrationDirection({ errorM, toleranceM }) {
  if (!Number.isFinite(errorM) || !Number.isFinite(toleranceM) || toleranceM < 0) return "none";
  if (Math.abs(errorM) <= toleranceM) return "none";
  return errorM > 0 ? "increase" : "decrease";
}

function calibrationSummary(state) {
  const summarize = (values) => {
    const count = values.length;
    if (!count) return { count: 0, mean: Number.NaN, std: Number.NaN, min: Number.NaN, max: Number.NaN };
    const mean = values.reduce((sum, value) => sum + value, 0) / count;
    const variance = values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / count;
    return { count, mean, std: Math.sqrt(variance), min: Math.min(...values), max: Math.max(...values) };
  };
  const corrected = summarize(state?.corrected ?? []);
  const raw = summarize(state?.raw ?? []);
  if (corrected.count < 2) {
    return {
      ready: false,
      message: `Not enough data: ${corrected.count} sample(s), need 2.`,
      direction: "none",
      errorM: Number.NaN,
      corrected,
      raw,
    };
  }
  const errorM = corrected.mean - state.knownDistanceM;
  const direction = calibrationDirection({ errorM, toleranceM: state.toleranceM });
  return {
    ready: true,
    message: direction === "none"
      ? `Within ±${state.toleranceM.toFixed(3)} m; no change.`
      : `${direction === "increase" ? "Increase" : "Decrease"} antenna delays on both boards.`,
    direction,
    errorM,
    corrected,
    raw,
  };
}

function calibrationShouldStop(state) {
  return Boolean(
    state?.running &&
    Number.isInteger(state?.targetSamples) &&
    state.targetSamples > 0 &&
    state.captured >= state.targetSamples
  );
}

function calibrationReset(state) {
  Object.assign(state, createCalibrationState());
  return { dtuPerCm: 0, errorMessage: "" };
}

function calibrationInputs(fields) {
  const knownDistanceM = Number(fields.known_distance_m);
  const targetSamples = Number(fields.target_samples);
  const toleranceM = Number(fields.tolerance_m);
  const dtuPerCmRaw = String(fields.dtu_per_cm ?? "").trim();
  const dtuPerCm = dtuPerCmRaw === "" ? 0 : Number(dtuPerCmRaw);
  if (!Number.isFinite(knownDistanceM) || knownDistanceM <= 0) {
    throw new Error("Known distance must be a positive finite value in meters.");
  }
  if (!Number.isInteger(targetSamples) || targetSamples < 2) {
    throw new Error("Target samples must be an integer greater than or equal to 2.");
  }
  if (!Number.isFinite(toleranceM) || toleranceM < 0) {
    throw new Error("Tolerance must be a finite value greater than or equal to 0.");
  }
  if (!Number.isFinite(dtuPerCm) || dtuPerCm < 0) {
    throw new Error("DTU-per-cm hint must be blank or a finite value greater than or equal to 0.");
  }
  return { knownDistanceM, targetSamples, toleranceM, dtuPerCm };
}

function resolveCalibrationStatusMessage({ statusNote = "", stickyNote = "", hasActivity, recommendation }) {
  if (statusNote) return statusNote;
  if (stickyNote) return stickyNote;
  if (hasActivity) return recommendation;
  return "Start a capture.";
}

// Selectable TX_POWER register values, nominally 3 dB apart (see TX_POWER_PRESETS in radio.py).
const TX_POWER_PRESETS = [0xFEFEFEFE, 0x9E9E9E9E, 0x85858585, 0x4A4A4A4A, 0x3D3D3D3D, 0x48484848, 0x30303030];

function radioPayload(fields) {
  const payload = {};
  for (const key of ["sampling_hz", "channel", "preamble", "data_rate"]) {
    if (String(fields[key] ?? "").trim() === "") throw new Error("All radio settings are required.");
    payload[key] = Number(fields[key]);
  }
  const power = String(fields.tx_power ?? "").trim();
  payload.tx_power = /^0x[0-9a-f]{1,8}$/i.test(power) ? Number(power) : NaN;
  if (!TX_POWER_PRESETS.includes(payload.tx_power)) throw new Error("Choose one of the TX power presets.");
  if (!Number.isFinite(payload.sampling_hz) || payload.sampling_hz < 0.1 || payload.sampling_hz > 10 ||
      ![5, 9].includes(payload.channel) || ![64, 128, 256, 512, 1024].includes(payload.preamble) ||
      ![850, 6800].includes(payload.data_rate)) {
    throw new Error("Choose supported radio settings; requested rate must be 0.1-10 Hz.");
  }
  return payload;
}

function radioAvailable(radio) {
  return Boolean(radio?.supported && !radio.busy &&
    radio.boards?.tx?.connected && radio.boards?.rx?.connected &&
    radio.boards.tx.state?.role === "tx" && radio.boards.rx.state?.role === "rx" &&
    radio.boards.tx.state.board !== radio.boards.rx.state.board);
}

function hardwareCalibrationView(status, capture) {
  const blocked = (message) => ({ canStart: false, canApply: false, message });
  if (status?.source !== "dual-serial") return blocked("Hardware apply requires dual-serial mode with both USB boards.");
  if (!status.connected || !radioAvailable(status.radio) || !status.radio.ready) {
    return blocked("Waiting for both USB boards to be ready; hardware apply unavailable.");
  }
  const boards = status.radio.boards;
  if (["tx", "rx"].some((role) =>
    ["tx_ant_dly", "rx_ant_dly"].some((key) => !Number.isInteger(boards[role].state[key])))) {
    return blocked("Update firmware on both boards to enable antenna-delay readback and apply.");
  }
  if (status.experiment?.active) return blocked("Calibration is locked throughout an experiment.");
  const result = { canStart: !capture?.active, canApply: false, message: "Start a capture." };
  if (!capture?.id) return result;
  if (capture.phase === "applied") return {...result, message: "Delays applied. Capture again to validate."};
  if (capture.error) return {...result, message: capture.error};
  if (capture.phase !== "ready") return {...result, message: `Calibration: ${capture.phase}.`};
  if (["tx", "rx"].some((role) => Object.entries(capture.before[role]).some(([key, value]) =>
    boards[role].state[key] !== value))) return {...result, message: "Board settings changed; capture again."};
  if (!capture.delta_dtu) return {...result, message: "No change needed."};
  const targets = ["tx", "rx"].map((role) => {
    const old = capture.before[role], next = capture.proposed[role];
    return `${role.toUpperCase()} #${old.board}: ${old.tx_ant_dly}/${old.rx_ant_dly} → ${next.tx_ant_dly}/${next.rx_ant_dly}`;
  });
  return {...result, canApply: true,
    message: `${capture.delta_dtu > 0 ? "+" : ""}${capture.delta_dtu} DTU on all delays. ${targets.join("; ")}.`};
}

function startDashboard() {
  const byId = (id) => document.getElementById(id);
  const format = (value) => formatAnalysisValue(value, "m");
  const view = createViewState();
  let preferences = validatePreferences(DEFAULTS);
  let recording = false;
  let streamOpen = false;
  let radioInitialized = false;
  let radioSubmitting = false;
  let calibrationDtuPerCm = 0;
  let calibrationStickyNote = "";
  let latestStatus = null;
  let hardwareCalibration = null;
  let dismissedCalibrationId = null;
  let calibrationSubmitting = false;
  const calibration = createCalibrationState();
  const showError = (id, message) => {
    byId(id).textContent = message;
    byId(id).hidden = !message;
  };
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored !== null) preferences = validatePreferences(JSON.parse(stored));
  } catch (error) {
    showError("display-warning", `Display preferences could not be loaded: ${error.message}. Using defaults.`);
  }
  function savePreferences() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences)); }
    catch (error) { showError("display-warning", `Display settings work, but could not be saved: ${error.message}`); }
  }
  function syncControls() {
    byId("secondary-plot").checked = preferences.secondary;
    for (const [prefix, settings] of [["", preferences], ["second-", preferences.secondPlot]]) {
      byId(`${prefix}display-count`).value = settings.count;
      byId(`${prefix}plot-height`).value = settings.height;
      byId(`${prefix}plot-style`).value = settings.style;
      document.querySelectorAll(`#${prefix}series-controls input`).forEach((input) => {
        input.checked = settings.series.includes(input.value);
      });
      for (const family of ["distance", "error", "power"]) {
        byId(`${prefix}${family}-auto`).checked = settings[family].auto;
        for (const bound of ["min", "max"]) {
          byId(`${prefix}${family}-${bound}`).value = settings[family][bound];
          byId(`${prefix}${family}-${bound}`).disabled = settings[family].auto;
        }
      }
    }
    byId("experiment-controls").hidden = !preferences.controls;
    document.querySelector(".workspace").classList.toggle("controls-hidden", !preferences.controls);
    byId("toggle-controls").setAttribute("aria-expanded", String(preferences.controls));
    byId("toggle-controls").textContent = preferences.controls ? "\u203a" : "\u2039";
    byId("toggle-controls").title = byId("toggle-controls").ariaLabel = `${preferences.controls ? "Hide" : "Show"} panel`;
  }
  function updatePreferences() {
    const candidate = {
      ...preferences, secondary: byId("secondary-plot").checked,
    };
    try {
      preferences = validatePreferences(candidate);
      showError("settings-error", "");
      savePreferences();
      drawAll();
    } catch (error) { showError("settings-error", error.message); }
  }
  function updatePlotPreferences(prefix) {
    const previous = prefix ? preferences.secondPlot : preferences;
    const candidate = {
      count: Number(byId(`${prefix}display-count`).value),
      height: Number(byId(`${prefix}plot-height`).value),
      style: byId(`${prefix}plot-style`).value,
      series: [...document.querySelectorAll(`#${prefix}series-controls input:checked`)].map((input) => input.value),
    };
    for (const family of ["distance", "error", "power"]) {
      const auto = byId(`${prefix}${family}-auto`).checked;
      candidate[family] = { auto };
      for (const bound of ["min", "max"]) {
        const input = byId(`${prefix}${family}-${bound}`);
        input.disabled = auto;
        candidate[family][bound] = auto ? previous[family][bound] : input.value === "" ? NaN : Number(input.value);
      }
    }
    try {
      const label = prefix ? "Plot 2" : "Plot 1";
      const plot = { ...validateView(candidate, label), ...validatePlotPreferences(candidate, label) };
      preferences = prefix ? { ...preferences, secondPlot: plot } : { ...preferences, ...plot };
      showError(`${prefix}plot-error`, "");
      savePreferences();
      drawAll();
    } catch (error) { showError(`${prefix}plot-error`, error.message); }
  }
  const formatCalibrationValue = (value) => Number.isFinite(value) ? value.toFixed(4) : "--";
  const formatCalibrationRange = (stats) => Number.isFinite(stats.min) && Number.isFinite(stats.max)
    ? `${stats.min.toFixed(4)} / ${stats.max.toFixed(4)}`
    : "-- / --";
  function recommendationText(summary) {
    if (!summary.ready) return summary.message;
    if (!Number.isFinite(calibrationDtuPerCm) || calibrationDtuPerCm <= 0 || summary.direction === "none") {
      return summary.message;
    }
    const cm = Math.abs(summary.errorM) * 100;
    const dtu = Math.round(cm * calibrationDtuPerCm);
    return `${summary.message} Estimated offset: ${cm.toFixed(2)} cm (${dtu} DTU at ${calibrationDtuPerCm} DTU/cm).`;
  }
  function renderCalibration(statusNote = "") {
    const summary = calibrationSummary(calibration);
    byId("calibration-captured").textContent = String(calibration.captured);
    byId("calibration-skipped").textContent = String(calibration.skipped);
    byId("calibration-corrected-mean").textContent = formatCalibrationValue(summary.corrected.mean);
    byId("calibration-corrected-std").textContent = formatCalibrationValue(summary.corrected.std);
    byId("calibration-corrected-range").textContent = formatCalibrationRange(summary.corrected);
    byId("calibration-raw-mean").textContent = formatCalibrationValue(summary.raw.mean);
    byId("calibration-raw-std").textContent = formatCalibrationValue(summary.raw.std);
    byId("calibration-raw-range").textContent = formatCalibrationRange(summary.raw);
    byId("calibration-error-m").textContent = formatCalibrationValue(summary.errorM);
    byId("calibration-start").disabled = calibration.running;
    byId("calibration-stop").disabled = !calibration.running;
    byId("calibration-state").textContent = calibration.running
      ? `Capturing ${calibration.captured}/${calibration.targetSamples}`
      : calibration.captured || calibration.skipped ? "Stopped" : "Idle";
    byId("calibration-recommendation").textContent = resolveCalibrationStatusMessage({
      statusNote,
      stickyNote: calibrationStickyNote,
      hasActivity: calibration.captured || calibration.skipped || calibration.running,
      recommendation: recommendationText(summary),
    });
    const hardware = hardwareCalibrationView(latestStatus, hardwareCalibration);
    const remote = hardwareCalibration;
    if (remote) {
      byId("calibration-state").textContent = `${remote.phase}: ${remote.count}/${remote.parameters.target_samples}`;
      byId("calibration-captured").textContent = String(remote.count);
      byId("calibration-skipped").textContent = String(remote.skipped);
      for (const field of ["corrected", "raw"]) {
        const stats = remote[field] ?? {};
        byId(`calibration-${field}-mean`).textContent = formatCalibrationValue(stats.mean);
        byId(`calibration-${field}-std`).textContent = formatCalibrationValue(stats.std);
        byId(`calibration-${field}-range`).textContent = formatCalibrationRange(stats);
      }
      byId("calibration-error-m").textContent = formatCalibrationValue(remote.error_m);
      byId("calibration-recommendation").textContent = hardware.message;
    }
    const capturing = remote ? remote.active : calibration.running;
    byId("calibration-start").disabled = calibrationSubmitting || capturing ||
      (latestStatus?.source === "dual-serial" && !hardware.canStart);
    byId("calibration-stop").disabled = calibrationSubmitting || !capturing;
    byId("calibration-reset").disabled = calibrationSubmitting || remote?.phase === "applying";
    byId("calibration-form").querySelectorAll("input").forEach((input) => {
      input.disabled = capturing || calibrationSubmitting;
    });
    byId("calibration-apply").disabled = calibrationSubmitting || !streamOpen || !hardware.canApply;
    // With a remote capture the same message is already shown as the recommendation.
    const hardwareNote = calibrationSubmitting
      ? "Waiting for host confirmation..." : remote ? "" : hardware.message;
    byId("calibration-hardware").textContent = hardwareNote;
    byId("calibration-hardware").hidden = !hardwareNote;
  }

  function drawChart(canvas, samples, settings) {
    const theme = chartTheme();
    const { selected, bounds } = getPlotData(samples, settings);
    const box = canvas.getBoundingClientRect();
    const scale = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(box.width * scale));
    canvas.height = Math.max(1, Math.floor(box.height * scale));
    const ctx = canvas.getContext("2d");
    ctx.scale(scale, scale);
    const width = box.width, height = box.height;
    const hasDistance = selected.some((s) => s.family === "distance");
    const hasError = selected.some((s) => s.family === "error");
    const hasPower = selected.some((s) => s.family === "power");
    // The dB axis sits on the right, outside the error axis when both are shown.
    const pad = { top: 25, right: hasError && hasPower ? 125 : 65, bottom: 30, left: 65 };
    const powerAxisX = width - pad.right + (hasError ? 62 : 7);
    const plotWidth = Math.max(1, width - pad.left - pad.right);
    const plotHeight = height - pad.top - pad.bottom;
    const x = (index) => pad.left + index * plotWidth / Math.max(samples.length - 1, 1);
    const y = (value, family) => {
      const [min, max] = bounds[family];
      return pad.top + (max - value) * plotHeight / (max - min);
    };
    ctx.font = "11px ui-monospace, monospace";
    ctx.fillStyle = theme.text;
    ctx.textAlign = "left";
    if (hasDistance) ctx.fillText("Distance (m)", 4, 14);
    if (hasError) {
      ctx.textAlign = "right";
      ctx.fillText("Error (m)", hasPower ? width - pad.right + 55 : width - 4, 14);
    }
    if (hasPower) {
      ctx.textAlign = "right";
      ctx.fillText("Power (dB)", width - 4, 14);
    }
    for (let index = 0; index <= 4; index++) {
      const row = pad.top + index * plotHeight / 4;
      ctx.strokeStyle = theme.grid;
      ctx.beginPath(); ctx.moveTo(pad.left, row); ctx.lineTo(width - pad.right, row); ctx.stroke();
      for (const family of ["distance", "error", "power"]) {
        if ((family === "distance" && !hasDistance) || (family === "error" && !hasError) ||
            (family === "power" && !hasPower)) continue;
        const [min, max] = bounds[family];
        const left = family === "distance";
        ctx.textAlign = left ? "right" : "left";
        ctx.fillStyle = theme.text;
        const labelX = left ? pad.left - 7 : family === "power" ? powerAxisX : width - pad.right + 7;
        const value = max - (max - min) * index / 4;
        ctx.fillText(family === "power" ? value.toFixed(1) : format(value), labelX, row + 4);
      }
    }
    ctx.save();
    ctx.beginPath(); ctx.rect(pad.left, pad.top, plotWidth, plotHeight); ctx.clip();
    if (hasError && bounds.error[0] <= 0 && bounds.error[1] >= 0) {
      ctx.strokeStyle = theme.zero; ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(pad.left, y(0, "error")); ctx.lineTo(width - pad.right, y(0, "error")); ctx.stroke();
    }
    for (const series of selected) {
      const color = theme.series[series.key] || series.color;
      ctx.strokeStyle = color; ctx.fillStyle = color;
      ctx.lineWidth = 1.5; ctx.setLineDash(series.dash);
      if (settings.style !== "points") {
        ctx.beginPath();
        let drawing = false;
        samples.forEach((sample, index) => {
          const value = sample[series.key];
          if (!Number.isFinite(value)) { drawing = false; return; }
          if (drawing) ctx.lineTo(x(index), y(value, series.family));
          else { ctx.moveTo(x(index), y(value, series.family)); drawing = true; }
        });
        ctx.stroke();
      }
      if (settings.style !== "lines" || samples.length === 1) {
        samples.forEach((sample, index) => {
          if (!Number.isFinite(sample[series.key])) return;
          ctx.beginPath(); ctx.arc(x(index), y(sample[series.key], series.family), 2, 0, Math.PI * 2); ctx.fill();
        });
      }
    }
    ctx.restore();
    ctx.fillStyle = theme.text; ctx.textAlign = "center";
    ctx.fillText(`Samples (oldest to newest): ${samples.length}`, width / 2, height - 7);
    if (samples.length) {
      ctx.textAlign = "left"; ctx.fillText("1", pad.left, height - 7);
      ctx.textAlign = "right"; ctx.fillText(String(samples.length), width - pad.right, height - 7);
    }
    if (!selected.length || !samples.some((sample) => selected.some((s) => Number.isFinite(sample[s.key])))) {
      ctx.textAlign = "center";
      ctx.fillText(!selected.length ? "Select a series to display" : "Waiting for data for selected series", width / 2, height / 2);
    }
  }

  function drawAll() {
    byId("distance-chart").style.setProperty("--plot-height", `${preferences.height}px`);
    byId("residual-chart").style.setProperty("--plot-height", `${preferences.secondPlot.height}px`);
    byId("secondary-block").hidden = !preferences.secondary;
    const samples = view.visible(preferences.count);
    const last = samples.at(-1) || {};
    for (const [id, key] of [
      ["range-value", "corrected_m"], ["expected-value", "expected_m"], ["residual-value", "residual_m"],
      ["bias-value", "rolling_bias_m"], ["spread-value", "rolling_std_m"],
      ["rx-power-value", "rx_power_dbm"], ["fp-gap-value", "fp_gap_db"],
    ]) byId(id).textContent = format(last[key]);
    drawChart(byId("distance-chart"), samples, preferences);
    if (preferences.secondary) drawChart(byId("residual-chart"), view.visible(preferences.secondPlot.count), preferences.secondPlot);
    byId("view-state").textContent = view.paused() ? "Paused view - acquisition continues" : "Live view";
    byId("view-state").classList.toggle("paused", view.paused());
    byId("pause-view").textContent = view.paused() ? "Resume view" : "Pause view";
    byId("pause-view").setAttribute("aria-pressed", String(view.paused()));
    byId("window-label").textContent = `${samples.length} / ${preferences.count} samples`;
    byId("window-label").title = `${view.buffered()} / 2000 buffered. Rolling statistics use the host's last 20 residuals.`;
    renderCalibration();
  }

  async function request(path, options = {}) {
    const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || "Request failed"));
    return body;
  }
  const experimentPanel = startExperimentPanel({request, showError, refreshStatus});
  const analysisPanel = startAnalysisPanel({request, showError});
  async function refreshStatus() {
    try {
      const status = await request("/api/status");
      latestStatus = status;
      hardwareCalibration = status.source === "dual-serial" && status.calibration?.id &&
        status.calibration.id !== dismissedCalibrationId ? status.calibration : null;
      byId("status-dot").classList.toggle("connected", status.connected && streamOpen);
      byId("connection-label").textContent = !streamOpen ? "Stream reconnecting" : status.connected ? "Live" : "Source disconnected";
      byId("source-label").textContent = status.source;
      byId("sample-count").textContent = status.total_samples;
      byId("gap-count").textContent = status.sequence_gaps;
      byId("ignored-count").textContent = status.ignored_lines;
      byId("malformed-count").textContent = status.malformed_lines;
      byId("prior-revision").textContent = status.prior ? `Revision ${status.prior.revision}` : "No prior";
      recording = status.recording;
      byId("record-button").textContent = recording ? "STOP" : "REC";
      byId("record-button").classList.toggle("stop", recording);
      showError("error-message", status.last_error || "");
      updateRadioStatus(status);
      experimentPanel.update(status);
      analysisPanel.update(status);
      renderCalibration();
    } catch (error) {
      byId("status-dot").classList.remove("connected");
      byId("connection-label").textContent = "Host unavailable";
      showError("error-message", error.message);
      byId("radio-fields").disabled = true;
      byId("radio-refresh").disabled = true;
      byId("experiment-start").disabled = true;
      latestStatus = null;
      renderCalibration();
    }
  }
  // Panel form values and open/closed state survive a page refresh (browser localStorage).
  const PANEL_KEY = "uwb-lab-panels-v1";
  const PANEL_FORMS = ["radio-form", "calibration-form", "prior-form", "experiment-form"];
  function readPanelStore() {
    try { return JSON.parse(localStorage.getItem(PANEL_KEY)) || {}; } catch { return {}; }
  }
  function writePanelStore(store) {
    try { localStorage.setItem(PANEL_KEY, JSON.stringify(store)); } catch {}
  }
  function saveForm(form) {
    const values = {};
    for (const element of form.elements) {
      if (!element.name || element.type === "submit" || element.type === "button") continue;
      values[element.name] = element.type === "checkbox" ? element.checked : element.value;
    }
    const store = readPanelStore();
    store.forms = { ...store.forms, [form.id]: values };
    writePanelStore(store);
  }
  function restorePanelSettings() {
    const store = readPanelStore();
    for (const id of PANEL_FORMS) {
      const saved = store.forms?.[id];
      const form = byId(id);
      if (!saved || !form) continue;
      for (const [name, value] of Object.entries(saved)) {
        const element = form.elements.namedItem(name);
        if (!element || element.type === "submit" || element.type === "button") continue;
        if (element.type === "checkbox") element.checked = value === true;
        else if (typeof value === "string" && (element.tagName !== "SELECT" ||
            [...element.options].some((option) => option.value === value))) element.value = value;
      }
    }
    applyPriorMode();
    const panels = [...document.querySelectorAll("details.control-panel, details.subpanel")];
    panels.forEach((panel, index) => {
      const open = store.panels?.[panel.id || `panel-${index}`];
      if (typeof open === "boolean") panel.open = open;
    });
    for (const id of PANEL_FORMS) {
      const form = byId(id);
      form?.addEventListener("input", () => saveForm(form));
      form?.addEventListener("change", () => saveForm(form));
    }
    panels.forEach((panel, index) => panel.addEventListener("toggle", () => {
      const current = readPanelStore();
      current.panels = { ...current.panels, [panel.id || `panel-${index}`]: panel.open };
      writePanelStore(current);
    }));
  }
  function loadRadioFields(state) {
    const form = byId("radio-form");
    form.elements.namedItem("sampling_hz").value = (1000 / state.period_ms).toFixed(3);
    for (const key of ["channel", "preamble", "data_rate"]) form.elements.namedItem(key).value = state[key];
    const power = form.elements.namedItem("tx_power");
    power.value = `0x${state.tx_power.toString(16).padStart(8, "0").toUpperCase()}`;
    // A board left on a value outside the presets (e.g. the firmware default) shows the empty placeholder.
    if (power.selectedIndex < 1) power.selectedIndex = 0;
    radioInitialized = true;
    saveForm(form);
  }
  function updateRadioStatus(status) {
    const radio = status.radio;
    const available = radioAvailable(radio);
    byId("radio-fields").disabled = !available || radioSubmitting || status.experiment?.active || status.calibration?.active;
    byId("radio-refresh").disabled = !available || radioSubmitting || status.experiment?.active || status.calibration?.active;
    byId("observed-rate").textContent = `${formatAnalysisValue(status.observed_hz, "Hz")} samples/s`;
    if (!radio?.supported) {
      byId("radio-state").textContent = "Requires dual-serial mode and updated firmware on both USB-connected boards. Demo, replay and single-board sources are read-only.";
      return;
    }
    if (!radioInitialized && radio.boards.tx.state) {
      // The boards keep their last applied settings, so they win over the values stored in the page.
      const radioValues = () => ["sampling_hz", "channel", "preamble", "data_rate", "tx_power"]
        .map((name) => byId("radio-form").elements.namedItem(name).value).join("|");
      const before = radioValues();
      loadRadioFields(radio.boards.tx.state);
      if (before !== radioValues()) {
        byId("radio-result").textContent = "Loaded settings from the boards.";
      }
    }
    const descriptions = ["tx", "rx"].map((role) => {
      const board = radio.boards[role], state = board.state;
      const lost = board.telemetry_dropped || 0;
      if (!board.connected || !state) return `${role.toUpperCase()}: ${board.error || "waiting for USB identification"}`;
      return `${role.toUpperCase()} #${state.board}: ch ${state.channel} · ${state.preamble} sym · ${formatAnalysisValue(state.data_rate / 1000, "Mbps")} Mbps · ${(1000 / state.period_ms).toFixed(1)} Hz · ant ${state.tx_ant_dly ?? "--"}/${state.rx_ant_dly ?? "--"} · ${state.running ? "running" : "PAUSED"}${lost ? ` · ${lost} lines lost` : ""}`;
    });
    byId("radio-state").textContent = descriptions.join("\n");
    if (radio.last_error) showError("radio-error", radio.last_error);
    if (radio.busy || radioSubmitting) byId("radio-result").textContent = "Coordinating both boards; waiting for acknowledgements...";
  }
  function connect() {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${location.host}/ws`);
    socket.onopen = () => { streamOpen = true; refreshStatus(); };
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === "snapshot") view.snapshot(message.samples);
        else if (message.type === "sample") {
          view.add(message.sample);
          if (calibration.running) {
            calibrationConsumeSample(calibration, message.sample);
            if (calibrationShouldStop(calibration)) {
              calibration.running = false;
              calibrationStickyNote = "Capture target reached. Review stats and recommendation below.";
            }
          }
        }
        drawAll();
      } catch (error) { showError("display-warning", `Stream message could not be displayed: ${error.message}`); }
    };
    socket.onclose = () => {
      streamOpen = false;
      byId("calibration-apply").disabled = true;
      byId("status-dot").classList.remove("connected");
      byId("connection-label").textContent = "Stream reconnecting";
      setTimeout(connect, 1500);
    };
  }

  document.querySelector(".display-controls").addEventListener("change", updatePreferences);
  for (const prefix of ["", "second-"]) {
    byId(`${prefix}plot-controls`).addEventListener("change", () => updatePlotPreferences(prefix));
  }
  byId("pause-view").addEventListener("click", () => {
    if (view.paused()) view.resume(); else view.pause();
    drawAll();
  });
  byId("reset-display").addEventListener("click", () => {
    preferences = validatePreferences(DEFAULTS);
    showError("plot-error", ""); showError("second-plot-error", "");
    showError("settings-error", ""); syncControls(); savePreferences(); drawAll();
  });
  byId("toggle-controls").addEventListener("click", () => {
    preferences.controls = !preferences.controls; syncControls(); savePreferences(); drawAll();
  });
  function applyPriorMode() {
    const positions = byId("prior-mode").value === "positions";
    byId("distance-fields").hidden = positions;
    byId("position-fields").hidden = !positions;
    document.querySelector('[name="distance_m"]').required = !positions;
  }
  byId("prior-mode").addEventListener("change", applyPriorMode);
  restorePanelSettings();
  byId("prior-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    showError("prior-error", "");
    const fields = new FormData(event.target);
    const mode = fields.get("mode");
    const payload = { mode, uncertainty_m: Number(fields.get("uncertainty_m")), note: fields.get("note") };
    if (mode === "distance") payload.distance_m = Number(fields.get("distance_m"));
    else {
      payload.node_a_m = ["ax", "ay", "az"].map((name) => Number(fields.get(name)));
      payload.node_b_m = ["bx", "by", "bz"].map((name) => Number(fields.get(name)));
    }
    try {
      await request("/api/prior", { method: "POST", body: JSON.stringify(payload) });
      await refreshStatus();
    } catch (error) { showError("prior-error", error.message); }
  });
  byId("recording-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    showError("recording-error", "");
    try {
      if (recording) await request("/api/recording/stop", { method: "POST" });
      else {
        const name = new FormData(event.target).get("name");
        await request("/api/recording/start", { method: "POST", body: JSON.stringify({ name }) });
      }
      await refreshStatus();
    } catch (error) { showError("recording-error", error.message); }
  });
  byId("calibration-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    showError("calibration-error", "");
    try {
      const fields = calibrationInputs(Object.fromEntries(new FormData(event.target)));
      calibrationSubmitting = true;
      renderCalibration();
      const status = await request("/api/status");
      if (status.source === "dual-serial") {
        hardwareCalibration = await request("/api/calibration/start", {method: "POST", body: JSON.stringify({
          known_distance_m: fields.knownDistanceM, target_samples: fields.targetSamples, tolerance_m: fields.toleranceM,
        })});
        dismissedCalibrationId = null;
        calibrationReset(calibration);
        return;
      }
      calibrationDtuPerCm = fields.dtuPerCm;
      calibrationStickyNote = "";
      calibrationStart(calibration, fields);
      renderCalibration("Capture started: collecting valid corrected samples from incoming telemetry.");
    } catch (error) {
      showError("calibration-error", error.message);
      renderCalibration();
    } finally {
      calibrationSubmitting = false;
      await refreshStatus();
    }
  });
  async function stopCalibration(reset) {
    calibrationSubmitting = true;
    try {
      if (hardwareCalibration) {
        await request("/api/calibration/stop", {method: "POST"});
        if (reset) {
          dismissedCalibrationId = hardwareCalibration.id;
          hardwareCalibration = null;
        }
      }
      calibration.running = false;
      calibrationStickyNote = "";
      if (reset) calibrationDtuPerCm = calibrationReset(calibration).dtuPerCm;
      showError("calibration-error", "");
    } catch (error) { showError("calibration-error", error.message); }
    finally { calibrationSubmitting = false; await refreshStatus(); }
  }
  byId("calibration-stop").addEventListener("click", () => stopCalibration(false));
  byId("calibration-reset").addEventListener("click", () => stopCalibration(true));
  byId("calibration-apply").addEventListener("click", async () => {
    const hardware = hardwareCalibrationView(latestStatus, hardwareCalibration);
    if (calibrationSubmitting || !hardware.canApply) return;
    calibrationSubmitting = true;
    showError("calibration-error", "");
    renderCalibration();
    try {
      hardwareCalibration = await request("/api/calibration/apply", {
        method: "POST", body: JSON.stringify({id: hardwareCalibration.id}),
      });
    } catch (error) { showError("calibration-error", error.message); }
    finally { calibrationSubmitting = false; await refreshStatus(); }
  });
  byId("radio-refresh").addEventListener("click", async () => {
    try {
      const status = await request("/api/status");
      if (!radioAvailable(status.radio)) throw new Error("Both USB boards must be connected and identified.");
      loadRadioFields(status.radio.boards.tx.state);
      updateRadioStatus(status);
    } catch (error) { showError("radio-error", error.message); }
  });
  byId("radio-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    showError("radio-error", "");
    try {
      const payload = radioPayload(Object.fromEntries(new FormData(event.target)));
      radioSubmitting = true;
      byId("radio-fields").disabled = true;
      byId("radio-refresh").disabled = true;
      byId("radio-result").textContent = "Pausing ranging and applying settings to both boards...";
      await request("/api/radio", { method: "POST", body: JSON.stringify(payload) });
      byId("radio-result").textContent = "Settings applied.";
    } catch (error) {
      byId("radio-result").textContent = "Configuration not confirmed; inspect board states below.";
      showError("radio-error", error.message);
    } finally {
      radioSubmitting = false;
      await refreshStatus();
    }
  });
  window.addEventListener("resize", drawAll);
  function applyTheme(light) {
    document.documentElement.dataset.theme = light ? "light" : "dark";
    const button = byId("theme-toggle");
    button.textContent = light ? "\u263e" : "\u2600";
    button.title = button.ariaLabel = light ? "Switch to night mode" : "Switch to light mode";
  }
  applyTheme(document.documentElement.dataset.theme === "light");
  byId("theme-toggle").addEventListener("click", () => {
    const light = document.documentElement.dataset.theme !== "light";
    applyTheme(light);
    try { localStorage.setItem("uwb-theme", light ? "light" : "dark"); } catch {}
    drawAll();
    window.dispatchEvent(new Event("resize"));
  });
  syncControls(); drawAll(); renderCalibration(); connect(); refreshStatus();
  setInterval(refreshStatus, 1000);
}

if (typeof module !== "undefined") module.exports = {
  DEFAULTS, TX_POWER_PRESETS, validatePreferences, axisBounds, createViewState, getPlotData, radioPayload, radioAvailable,
  createCalibrationState, calibrationStart, calibrationConsumeSample, calibrationSummary, calibrationDirection,
  calibrationShouldStop, calibrationReset, calibrationInputs, resolveCalibrationStatusMessage,
  hardwareCalibrationView,
};
if (typeof document !== "undefined") startDashboard();
