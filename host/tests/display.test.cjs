const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const context = { module: { exports: {} } };
vm.createContext(context);
const script = fs.readFileSync(`${__dirname}/../uwb_lab/static/app.js`, "utf8");
// Loading in a DOM-free context also checks that the display logic is independent.
vm.runInContext(script, context);
const api = context.module.exports;
const plain = (value) => JSON.parse(JSON.stringify(value));

test("display helpers are available without a browser", () => {
  assert.equal(typeof api.createViewState, "function");
  assert.equal(typeof api.validatePreferences, "function");
  assert.equal(typeof api.axisBounds, "function");
});

test("calibration helper accumulates corrected/raw samples and tracks skipped values", () => {
  const state = api.createCalibrationState();
  api.calibrationStart(state, { knownDistanceM: 5, targetSamples: 3, toleranceM: 0.01 });
  api.calibrationConsumeSample(state, { valid: true, corrected_m: 5.04, raw_m: 5.07 });
  api.calibrationConsumeSample(state, { valid: true, corrected_m: Number.NaN, raw_m: 5.05 });
  api.calibrationConsumeSample(state, { valid: true, corrected_m: 4.99, raw_m: 5.01 });
  assert.equal(state.captured, 2);
  assert.equal(state.skipped, 1);
});

test("calibration recommendation follows error sign and tolerance", () => {
  assert.equal(api.calibrationDirection({ errorM: 0.02, toleranceM: 0.01 }), "increase");
  assert.equal(api.calibrationDirection({ errorM: -0.02, toleranceM: 0.01 }), "decrease");
  assert.equal(api.calibrationDirection({ errorM: 0.005, toleranceM: 0.01 }), "none");
});

test("hardware calibration apply requires a completed fresh supported capture", () => {
  const states = {
    tx: { board: 1, role: "tx", revision: 4, running: true, tx_ant_dly: 16380, rx_ant_dly: 16380 },
    rx: { board: 2, role: "rx", revision: 4, running: true, tx_ant_dly: 16382, rx_ant_dly: 16382 },
  };
  const status = { source: "dual-serial", connected: true, experiment: { active: false },
    radio: { supported: true, ready: true, busy: false, boards: {
      tx: { connected: true, state: states.tx }, rx: { connected: true, state: states.rx },
    } } };
  const capture = { id: "capture", phase: "ready", before: states, delta_dtu: 10,
    proposed: { tx: {tx_ant_dly: 16390, rx_ant_dly: 16390}, rx: {tx_ant_dly: 16392, rx_ant_dly: 16392} } };
  assert.equal(api.hardwareCalibrationView(status, capture).canApply, true);
  assert.match(api.hardwareCalibrationView(status, capture).message, /16380.*16390/);
  for (const change of [{phase: "capturing"}, {phase: "applied"}, {phase: "invalid"}, {delta_dtu: 0}]) {
    assert.equal(api.hardwareCalibrationView(status, {...capture, ...change}).canApply, false);
  }
  for (const change of [{source: "demo"}, {source: "replay"}, {connected: false},
    {experiment: {active: true}}, {radio: {...status.radio, busy: true}}]) {
    assert.equal(api.hardwareCalibrationView({...status, ...change}, capture).canApply, false);
  }
  const stale = plain(status);
  stale.radio.boards.tx.state.revision++;
  assert.equal(api.hardwareCalibrationView(stale, capture).canApply, false);
  const old = plain(status);
  delete old.radio.boards.rx.state.tx_ant_dly;
  assert.equal(api.hardwareCalibrationView(old, capture).canApply, false);
  assert.match(api.hardwareCalibrationView(old, capture).message, /firmware/);
});

test("calibration form inputs reject invalid values and parse valid entries", () => {
  assert.throws(
    () => api.calibrationInputs({ known_distance_m: "0", target_samples: "10", tolerance_m: "0.01", dtu_per_cm: "42" }),
    /Known distance must be a positive finite value in meters/,
  );
  assert.throws(
    () => api.calibrationInputs({ known_distance_m: "2", target_samples: "1", tolerance_m: "0.01", dtu_per_cm: "42" }),
    /Target samples must be an integer greater than or equal to 2/,
  );
  assert.throws(
    () => api.calibrationInputs({ known_distance_m: "2", target_samples: "10", tolerance_m: "-0.01", dtu_per_cm: "42" }),
    /Tolerance must be a finite value greater than or equal to 0/,
  );
  assert.throws(
    () => api.calibrationInputs({ known_distance_m: "2", target_samples: "10", tolerance_m: "0.01", dtu_per_cm: "-1" }),
    /DTU-per-cm hint must be blank or a finite value greater than or equal to 0/,
  );
  assert.deepEqual(
    plain(api.calibrationInputs({ known_distance_m: "2.5", target_samples: "12", tolerance_m: "0.015", dtu_per_cm: "18.2" })),
    { knownDistanceM: 2.5, targetSamples: 12, toleranceM: 0.015, dtuPerCm: 18.2 },
  );
});

test("calibration lifecycle helpers support autostop checks and reset without DOM state", () => {
  const state = api.createCalibrationState();
  api.calibrationStart(state, { knownDistanceM: 3, targetSamples: 2, toleranceM: 0.01 });
  assert.equal(api.calibrationShouldStop(state), false);
  api.calibrationConsumeSample(state, { valid: true, corrected_m: 3.01 });
  assert.equal(api.calibrationShouldStop(state), false);
  api.calibrationConsumeSample(state, { valid: true, corrected_m: 2.99 });
  assert.equal(api.calibrationShouldStop(state), true);

  state.skipped = 5;
  const reset = api.calibrationReset(state);
  assert.equal(reset.dtuPerCm, 0);
  assert.equal(reset.errorMessage, "");
  assert.deepEqual(plain(state), plain(api.createCalibrationState()));
});

test("calibration summary reports not-enough-data for fewer than 2 corrected samples", () => {
  const state = api.createCalibrationState();
  api.calibrationStart(state, { knownDistanceM: 2, targetSamples: 5, toleranceM: 0.01 });
  api.calibrationConsumeSample(state, { valid: true, corrected_m: 2.02, raw_m: 2.04 });
  const summary = api.calibrationSummary(state);
  assert.equal(summary.ready, false);
  assert.equal(summary.message, "Not enough data: captured 1 valid corrected sample(s); need at least 2.");
});

test("calibration summary recommendation text is explicit for increase/decrease/none", () => {
  const increaseState = api.createCalibrationState();
  api.calibrationStart(increaseState, { knownDistanceM: 2, targetSamples: 5, toleranceM: 0.01 });
  api.calibrationConsumeSample(increaseState, { valid: true, corrected_m: 2.03, raw_m: 2.05 });
  api.calibrationConsumeSample(increaseState, { valid: true, corrected_m: 2.04, raw_m: 2.06 });
  const increase = api.calibrationSummary(increaseState);
  assert.equal(increase.message, "Recommendation: increase TX/RX antenna delays equally on both boards.");

  const decreaseState = api.createCalibrationState();
  api.calibrationStart(decreaseState, { knownDistanceM: 2, targetSamples: 5, toleranceM: 0.01 });
  api.calibrationConsumeSample(decreaseState, { valid: true, corrected_m: 1.96, raw_m: 1.98 });
  api.calibrationConsumeSample(decreaseState, { valid: true, corrected_m: 1.97, raw_m: 1.99 });
  const decrease = api.calibrationSummary(decreaseState);
  assert.equal(decrease.message, "Recommendation: decrease TX/RX antenna delays equally on both boards.");

  const noneState = api.createCalibrationState();
  api.calibrationStart(noneState, { knownDistanceM: 2, targetSamples: 5, toleranceM: 0.01 });
  api.calibrationConsumeSample(noneState, { valid: true, corrected_m: 2.004, raw_m: 2.005 });
  api.calibrationConsumeSample(noneState, { valid: true, corrected_m: 2.006, raw_m: 2.007 });
  const none = api.calibrationSummary(noneState);
  assert.equal(none.message, "Within tolerance (±0.010 m); no antenna-delay change suggested.");
});

test("calibration status message preserves sticky autostop notice when no new status note is passed", () => {
  const recommendation = "Recommendation: increase TX/RX antenna delays equally on both boards.";
  const autostop = "Capture target reached. Review stats and recommendation below.";
  assert.equal(
    api.resolveCalibrationStatusMessage({
      statusNote: "",
      stickyNote: autostop,
      hasActivity: true,
      recommendation,
    }),
    autostop,
  );
  assert.equal(
    api.resolveCalibrationStatusMessage({
      statusNote: "Capture started: collecting valid corrected samples from incoming telemetry.",
      stickyNote: autostop,
      hasActivity: true,
      recommendation,
    }),
    "Capture started: collecting valid corrected samples from incoming telemetry.",
  );
  assert.equal(
    api.resolveCalibrationStatusMessage({
      statusNote: "",
      stickyNote: "",
      hasActivity: true,
      recommendation,
    }),
    recommendation,
  );
});

test("radio form converts units and validates raw TX power without silent coercion", () => {
  const fields = { sampling_hz: "5", channel: "9", preamble: "128", data_rate: "6800", tx_power: "0xA0A0A0A0" };
  assert.deepEqual(plain(api.radioPayload(fields)), {
    sampling_hz: 5, channel: 9, preamble: 128, data_rate: 6800, tx_power: 0xA0A0A0A0,
  });
  for (const changes of [
    { sampling_hz: "" }, { sampling_hz: "0" }, { sampling_hz: "11" },
    { sampling_hz: "NaN" }, { channel: "7" }, { preamble: "65" }, { data_rate: "110" },
    { tx_power: "" }, { tx_power: "-1" }, { tx_power: "0x123456789" }, { tx_power: "not hex" },
  ]) assert.throws(() => api.radioPayload({ ...fields, ...changes }));
});

test("radio controls require two connected, identified boards and respect busy state", () => {
  assert.equal(api.radioAvailable({ supported: false }), false);
  const radio = { supported: true, busy: false, boards: {
    tx: { connected: true, state: { role: "tx", board: 1 } },
    rx: { connected: true, state: { role: "rx", board: 2 } },
  } };
  assert.equal(api.radioAvailable(radio), true);
  assert.equal(api.radioAvailable({ ...radio, busy: true }), false);
  radio.boards.rx.state = null;
  assert.equal(api.radioAvailable(radio), false);
});

test("preferences validate all controls and reject invalid manual limits", () => {
  assert.deepEqual(plain(api.validatePreferences(api.DEFAULTS)), plain(api.DEFAULTS));
  for (const changes of [
    { count: 9 }, { count: 2001 }, { count: 12.5 }, { height: 601 },
    { height: 179 }, { height: 180.5 }, { controls: null },
    { style: "unknown" }, { series: ["bogus"] }, { secondary: "yes" },
    { distance: { auto: false, min: 2, max: 1 } },
    { error: { auto: false, min: null, max: 1 } },
    { error: { auto: false, min: -Infinity, max: Infinity } },
  ]) {
    assert.throws(() => api.validatePreferences({ ...api.DEFAULTS, ...changes }));
  }
  const copy = api.validatePreferences(api.DEFAULTS);
  copy.series.push("raw_m");
  assert.notDeepEqual(plain(copy.series), plain(api.DEFAULTS.series));
  assert.equal(api.validatePreferences({ ...api.DEFAULTS, count: 10, height: 180 }).count, 10);
  assert.equal(api.validatePreferences({ ...api.DEFAULTS, count: 2000, height: 600 }).height, 600);
});

test("automatic bounds ignore missing data and include residual zero", () => {
  assert.deepEqual(plain(api.axisBounds([null, NaN, Infinity], api.DEFAULTS.error, true)), [-0.01, 0.01]);
  const bounds = api.axisBounds([0.2, 0.3], api.DEFAULTS.error, true);
  assert.ok(bounds[0] <= 0 && bounds[1] > 0.3);
  const single = api.axisBounds([2], api.DEFAULTS.distance);
  assert.ok(single[0] < 2 && single[1] > 2);
  assert.deepEqual(plain(api.axisBounds([9], { auto: false, min: 1, max: 3 })), [1, 3]);
});

test("each plot has independent trace selection and axis settings", () => {
  const settings = api.validatePreferences(api.DEFAULTS);
  assert.deepEqual(plain(settings.secondPlot.series), ["residual_m"]);
  settings.series = ["corrected_m"];
  settings.distance = { auto: false, min: 1, max: 3 };
  settings.secondPlot.series = ["raw_m", "residual_m"];
  settings.secondPlot.distance = { auto: false, min: 0, max: 5 };
  settings.secondPlot.error = { auto: false, min: -0.2, max: 0.2 };
  const restored = api.validatePreferences(JSON.parse(JSON.stringify(settings)));
  assert.deepEqual(plain(restored.series), ["corrected_m"]);
  assert.deepEqual(plain(restored.secondPlot.series), ["raw_m", "residual_m"]);
  assert.equal(restored.distance.min, 1);
  assert.equal(restored.secondPlot.distance.min, 0);
  restored.secondPlot.error.min = -1;
  assert.equal(restored.error.min, -0.1);
  assert.equal(api.DEFAULTS.secondPlot.error.min, -0.1);
  restored.secondPlot.series.push("expected_m");
  assert.deepEqual(plain(api.DEFAULTS.secondPlot.series), ["residual_m"]);
});

test("legacy settings retain plot 1 and initialize independent plot 2 defaults", () => {
  const legacy = plain(api.DEFAULTS);
  delete legacy.secondPlot;
  legacy.series = ["raw_m", "rolling_bias_m"];
  legacy.distance = { auto: false, min: 1, max: 8 };
  const migrated = api.validatePreferences(legacy);
  assert.deepEqual(plain(migrated.series), legacy.series);
  assert.deepEqual(plain(migrated.distance), legacy.distance);
  assert.deepEqual(plain(migrated.secondPlot), plain(api.DEFAULTS.secondPlot));
  assert.notEqual(migrated.distance, migrated.secondPlot.distance);
});

test("invalid second plot settings are rejected even when the plot is hidden", () => {
  for (const secondPlot of [
    null, {}, { ...api.DEFAULTS.secondPlot, series: ["unknown"] },
    { ...api.DEFAULTS.secondPlot, error: { auto: false, min: 1, max: 1 } },
    { ...api.DEFAULTS.secondPlot, distance: { auto: false, min: NaN, max: 5 } },
  ]) {
    assert.throws(() => api.validatePreferences({ ...api.DEFAULTS, secondary: false, secondPlot }));
  }
});

test("plot render data contains only its selected traces and own bounds", () => {
  const samples = [
    { corrected_m: 2, raw_m: 2.1, residual_m: 0.1 },
    { corrected_m: 2.05, raw_m: 2.15, residual_m: 0.15 },
  ];
  const first = api.getPlotData(samples, {
    ...api.DEFAULTS, series: ["corrected_m"],
    distance: { auto: false, min: 1, max: 3 },
  });
  const second = api.getPlotData(samples, {
    ...api.DEFAULTS.secondPlot, series: ["raw_m", "residual_m"],
    distance: { auto: false, min: 0, max: 5 },
    error: { auto: false, min: -0.2, max: 0.2 },
  });
  assert.deepEqual(plain(first.selected.map((s) => s.key)), ["corrected_m"]);
  assert.deepEqual(plain(second.selected.map((s) => s.key)), ["raw_m", "residual_m"]);
  assert.deepEqual(plain(first.bounds.distance), [1, 3]);
  assert.deepEqual(plain(second.bounds.distance), [0, 5]);
  assert.deepEqual(plain(second.bounds.error), [-0.2, 0.2]);
  const empty = api.getPlotData(samples, { ...api.DEFAULTS.secondPlot, series: [] });
  assert.equal(empty.selected.length, 0);
});

test("sample buffer is bounded and changing display count does not discard history", () => {
  const state = api.createViewState(20);
  for (let seq = 1; seq <= 30; seq++) state.add({ seq });
  assert.equal(state.buffered(), 20);
  assert.deepEqual(plain(state.visible(3).map((s) => s.seq)), [28, 29, 30]);
  assert.equal(state.visible(20).length, 20);
});

test("the default buffer retains exactly the newest 2000 samples", () => {
  const state = api.createViewState();
  for (let seq = 0; seq < 2010; seq++) state.add({ seq });
  assert.equal(state.buffered(), 2000);
  assert.equal(state.visible(2000)[0].seq, 10);
  assert.equal(state.visible(180).length, 180);
  assert.equal(state.visible(180).at(-1).seq, 2009);
});

test("pause freezes data through arrivals and snapshots; resume shows latest data", () => {
  const state = api.createViewState(20);
  state.snapshot([{ seq: 1 }, { seq: 2 }]);
  state.pause();
  state.add({ seq: 3 });
  state.snapshot([{ seq: 10 }, { seq: 11 }]);
  assert.deepEqual(plain(state.visible(20).map((s) => s.seq)), [1, 2]);
  assert.deepEqual(plain(state.visible(1).map((s) => s.seq)), [2]);
  state.resume();
  assert.deepEqual(plain(state.visible(20).map((s) => s.seq)), [10, 11]);
  state.snapshot(Array.from({ length: 30 }, (_, seq) => ({ seq })));
  assert.equal(state.visible(100).length, 20);
});

test("dashboard markup includes antenna-delay calibration assistant panel", () => {
  const html = fs.readFileSync(require.resolve("../uwb_lab/static/index.html"), "utf8");
  assert.match(html, /Antenna-delay calibration assistant/);
  assert.match(html, /id="calibration-panel"/);
  assert.match(html, /id="calibration-form"/);
  assert.match(
    html,
    /Valid for controlled fixed-LOS real-hardware captures; not for demo\/replay\./,
  );
  assert.match(html, /id="calibration-apply"[^>]*disabled/);
  assert.match(html, /Settings last until each board restarts/);
});

test("dashboard wires hardware capture, server preview, apply and reset without client-side offsets", async () => {
  const elements = new Map();
  class Element {
    constructor() {
      this.value = ""; this.textContent = ""; this.disabled = false;
      this.events = {}; this.style = {setProperty() {}};
      this.classList = {toggle() {}, remove() {}};
      this.elements = {namedItem: (name) => byId(name)};
    }
    addEventListener(name, callback) { this.events[name] = callback; }
    setAttribute() {}
    querySelectorAll() { return []; }
    getBoundingClientRect() { return {width: 800, height: 300}; }
    getContext() { return new Proxy({}, {get: () => () => {}, set: () => true}); }
  }
  function byId(id) {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  }
  const state = (role) => ({board: role === "tx" ? 1 : 2, role, revision: 0, period_ms: 1000,
    channel: 9, preamble: 128, data_rate: 6800, tx_power: 42, running: true, tx_ant_dly: 16380, rx_ant_dly: 16380});
  const status = {source: "dual-serial", connected: true, experiment: {active: false}, calibration: {phase: "idle"},
    radio: {supported: true, ready: true, busy: false, boards: {
      tx: {connected: true, state: state("tx")}, rx: {connected: true, state: state("rx")},
    }}};
  const posts = [], ticks = [], sockets = [];
  let rejected = false;
  const sandbox = {
    module: {exports: {}}, console,
    document: {getElementById: byId, querySelectorAll: () => [], querySelector: byId,
      documentElement: byId("root")},
    window: {devicePixelRatio: 1, addEventListener() {}},
    location: {protocol: "http:", host: "localhost"},
    localStorage: {getItem: () => null, setItem() {}},
    FormData: class { constructor(form) { return Object.entries(form.fields); } },
    WebSocket: class { constructor() { sockets.push(this); } },
    setInterval: (callback) => ticks.push(callback), setTimeout() {},
    startExperimentPanel: () => ({update() {}}), startAnalysisPanel: () => ({update() {}}),
    formatAnalysisValue: (value) => Number.isFinite(value) ? String(value) : "--",
    fetch: async (path, options) => {
      if (options.method === "POST") posts.push([path, JSON.parse(options.body || "{}")]);
      if (path === "/api/calibration/start") status.calibration = {
        id: "verified-capture", phase: "capturing", active: true, count: 0, skipped: 0,
        parameters: {known_distance_m: 5, target_samples: 3}, before: {tx: state("tx"), rx: state("rx")},
      };
      if (path === "/api/calibration/apply") {
        if (rejected) return {ok: false, json: async () => ({detail: "Board settings changed; capture again"})};
        status.calibration.phase = "applied";
      }
      if (path === "/api/calibration/stop") status.calibration.phase = "invalid";
      return {ok: true, json: async () => plain(path === "/api/status" ? status : status.calibration)};
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(script, sandbox);
  await sockets[0].onopen();
  await ticks[0]();
  assert.equal(byId("calibration-apply").disabled, true);
  byId("calibration-form").fields = {
    known_distance_m: "5", target_samples: "3", tolerance_m: "0.01", dtu_per_cm: "999",
  };
  await byId("calibration-form").events.submit({preventDefault() {}, target: byId("calibration-form")});
  assert.deepEqual(posts[0], ["/api/calibration/start", {
    known_distance_m: 5, target_samples: 3, tolerance_m: 0.01,
  }]);
  assert.equal(byId("calibration-start").disabled, true);
  status.calibration = {...status.calibration, active: false, phase: "ready", count: 3,
    delta_dtu: 10, error_m: 0.0938,
    corrected: {mean: 5.0938, std: 0, min: 5.0938, max: 5.0938},
    raw: {mean: 5.0938, std: 0, min: 5.0938, max: 5.0938},
    proposed: {tx: {tx_ant_dly: 16390, rx_ant_dly: 16390}, rx: {tx_ant_dly: 16390, rx_ant_dly: 16390}}};
  await ticks[0]();
  assert.equal(byId("calibration-captured").textContent, "3");
  assert.equal(byId("calibration-apply").disabled, false);
  assert.match(byId("calibration-hardware").textContent, /\+10 DTU/);
  rejected = true;
  await byId("calibration-apply").events.click();
  assert.match(byId("calibration-error").textContent, /Board settings changed/);
  rejected = false;
  await byId("calibration-apply").events.click();
  assert.deepEqual(posts.at(-1), ["/api/calibration/apply", {id: "verified-capture"}]);
  assert.equal(byId("calibration-apply").disabled, true);
  assert.match(byId("calibration-hardware").textContent, /acknowledged/);
  await byId("calibration-reset").events.click();
  assert.equal(byId("calibration-captured").textContent, "0");
  assert.equal(byId("calibration-apply").disabled, true);
  assert.ok(posts.every(([path]) => path.startsWith("/api/calibration/")));
});
