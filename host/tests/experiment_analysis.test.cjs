const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { experimentPayload, experimentLocked } = require("../uwb_lab/static/experiments.js");
const { resultMatches, chartBounds, validateAnalysisPreferences, formatAnalysisValue } = require("../uwb_lab/static/analysis.js");

test("experiment inputs preserve duration, integer repetitions and interval", () => {
  assert.deepEqual(experimentPayload({
    name: "LOS", notes: "", duration_s: "10", run_count: "3", interval_s: "2",
  }), { name: "LOS", notes: "", duration_s: 10, run_count: 3, interval_s: 2 });
  for (const fields of [{duration_s: ""}, {run_count: "1.5"}, {interval_s: "-1"}]) {
    assert.throws(() => experimentPayload({name: "", duration_s: "10", run_count: "3", interval_s: "2", ...fields}));
  }
  assert.equal(experimentLocked({active: true, phase: "interval"}), true);
  assert.equal(experimentLocked({active: false}), false);
});

test("analysis results must match both selected job and resolved recipe", () => {
  assert.equal(resultMatches({jobId:"b", recipeKey:"r2"}, {job_id:"a", recipe_key:"r1"}), false);
  assert.equal(resultMatches({jobId:"b", recipeKey:"r2"}, {job_id:"b", recipe_key:"r2"}), true);
});

test("analysis axes support constant series and reject malformed numeric results", () => {
  const bounds = chartBounds([{x:[1,1], y:[0,0]}]);
  assert.ok(bounds.x.max > bounds.x.min);
  assert.ok(bounds.y.max > bounds.y.min);
  assert.throws(() => chartBounds([{x:[1], y:[Infinity]}]));
  assert.throws(() => chartBounds([{x:[1], y:[1,2]}]));
});

test("analysis preferences are independent and validate persisted methods", () => {
  const preferences = validateAnalysisPreferences({
    field:"raw_m", methods:["statistics","gmm"], window_size:100,
    histogram_bins:20, max_lag:10, components:3, seed:4,
  });
  assert.deepEqual(preferences.methods, ["statistics","gmm"]);
  assert.equal(preferences.components, 3);
  assert.throws(() => validateAnalysisPreferences({...preferences, histogram_bins:201}));
  assert.throws(() => validateAnalysisPreferences({...preferences, methods:["invented"]}));
});

test("analysis display rounds metres without changing counts or small spectral values", () => {
  assert.equal(formatAnalysisValue(2.123456, "m"), "2.12");
  assert.equal(formatAnalysisValue(-0.0001, "m"), "0.00");
  assert.equal(formatAnalysisValue(3201, ""), "3201");
  assert.equal(formatAnalysisValue(12.345678, ""), "12.3");
  assert.equal(formatAnalysisValue(0.0000123456, "m^2/Hz"), "0.0000123");
  assert.equal(formatAnalysisValue(null, "m"), "--");
});

test("old preferences migrate away from autocorrelation without losing other selections", () => {
  const preferences = validateAnalysisPreferences({methods: ["statistics", "acf", "psd"], max_lag: 20});
  assert.deepEqual(preferences.methods, ["statistics", "psd"]);
  assert.ok(!("max_lag" in preferences));
  assert.ok(!("window_size" in preferences));
});

  test("an explicitly selected run refreshes after closure instead of accepting a delayed partial result", async () => {
    class Element {
      constructor() { this.value = ""; this.children = []; this.textContent = ""; this.dataset = {}; }
      append(...children) { this.children.push(...children); }
      replaceChildren(...children) { this.children = children; }
      addEventListener() {}
      get options() { return this.children; }
      querySelector(selector) { return getElement(selector); }
    }
    const elements = new Map();
    function getElement(id) {
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    }
    getElement("analysis-scope").value = "run-0001";
    const timers = new Map();
    let timerId = 0, submitted = 0, delayed;
    let batch = {
      batch_id: "batch-a", outcome: "running", config: {name: "Test"},
      started_at: "2026-10-17T10:00:00Z",
      runs: [{run_id: "run-0001", state: "recording", session: "runs/one"}],
    };
    const output = (count, outcome) => ({
      mode: "live", included_count: count, excluded: {}, results: [],
      provenance: {batch_outcome: outcome, source_sha256: "abcdef123456", analysis_version: "noise-1"},
    });
    const environment = {
      document: {
        getElementById: getElement, createElement: () => new Element(),
        createTextNode: (text) => text,
      },
      window: {addEventListener() {}},
      localStorage: {getItem: () => null},
      Option: class extends Element {
        constructor(text, value) { super(); this.textContent = text; this.value = value; }
      },
      setTimeout: (callback) => { timers.set(++timerId, callback); return timerId; },
      clearTimeout: (id) => timers.delete(id),
    };
    vm.runInNewContext(fs.readFileSync(require.resolve("../uwb_lab/static/analysis.js"), "utf8"), environment);
    const panel = environment.startAnalysisPanel({
      showError() {},
      async request(path, options) {
        if (path === "/api/analysis/methods") return {methods: {statistics: {label: "Statistics"}}};
        if (path === "/api/experiments") return {batches: [structuredClone(batch)]};
        if (path === "/api/experiments/batch-a") return structuredClone(batch);
        if (options?.method === "POST") {
          submitted++;
          return {job_id: `job-${submitted}`, recipe_key: `recipe-${submitted}`};
        }
        if (path.endsWith("/job-1")) return new Promise((resolve) => { delayed = resolve; });
        return {job_id: `job-${submitted}`, recipe_key: `recipe-${submitted}`,
          state: "succeeded", output: output(10, "completed")};
      },
    });
    const flush = () => new Promise(setImmediate);
    const status = (active, outcome) => ({experiment: {
      batch_id: "batch-a", run_id: "run-0001", active, outcome, phase: active ? "recording" : "idle",
    }});
    panel.update(status(true, "running"));
    await flush();
    assert.equal(submitted, 1);
    const [id, callback] = timers.entries().next().value;
    timers.delete(id); callback();
    await flush();
    assert.equal(typeof delayed, "function");
    batch = {...batch, outcome: "completed", runs: [{...batch.runs[0], state: "completed"}]};
    panel.update(status(false, "completed"));
    delayed({job_id: "job-1", recipe_key: "recipe-1", state: "succeeded", output: output(1, "running")});
    await flush();
    panel.update(status(false, "completed"));
    await flush();
    assert.ok(submitted >= 2, "closure must request final data for the explicitly selected run");
    const [nextId, nextCallback] = timers.entries().next().value;
    timers.delete(nextId); nextCallback();
    await flush();
    assert.match(getElement("analysis-source").textContent, /10 eligible samples/);
    assert.match(getElement("analysis-source").textContent, /Batch completed/);
  });

test("pooled cumulative analysis keeps existing cards visible throughout run and dataset changes", async () => {
  class Element {
    constructor() { this.value = ""; this.children = []; this.textContent = ""; this.dataset = {}; this.listeners = {}; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    setAttribute(name, value) { this[name] = value; }
    get options() { return this.children; }
    querySelector(selector) { return getElement(selector); }
  }
  const elements = new Map();
  function getElement(id) {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  }
  getElement("analysis-scope").value = "batch";
  const timers = new Map(), recipes = [], errors = [];
  let timerId = 0, resultStatus = "ok";
  const environment = {
    document: {getElementById: getElement, createElement: () => new Element(), createTextNode: (text) => text},
    window: {addEventListener() {}}, localStorage: {getItem: () => null},
    Option: class extends Element { constructor(text, value) { super(); this.textContent = text; this.value = value; } },
    setTimeout: (callback) => { timers.set(++timerId, callback); return timerId; },
    clearTimeout: (id) => timers.delete(id),
  };
  vm.runInNewContext(fs.readFileSync(require.resolve("../uwb_lab/static/analysis.js"), "utf8"), environment);
  const panel = environment.startAnalysisPanel({
    showError: (_, message) => { if (message) errors.push(message); },
    async request(path, options) {
      if (path === "/api/analysis/methods") return {methods: {statistics: {label: "Statistics"}}};
      if (path === "/api/experiments") return {batches: []};
      if (path === "/api/experiments/batch-a") return {
        outcome: "running", runs: [{run_id: "run-0001", state: "completed", session: "one"},
          {run_id: "run-0002", state: "recording", session: "two"}],
      };
      if (options?.method === "POST") {
        recipes.push(JSON.parse(options.body));
        return {job_id: `job-${recipes.length}`, recipe_key: `recipe-${recipes.length}`};
      }
      return {
        job_id: `job-${recipes.length}`, recipe_key: `recipe-${recipes.length}`, state: "succeeded",
        output: {mode: "cumulative", included_count: 3200, excluded: {},
          provenance: {batch_outcome: "running", source_sha256: "abc123", analysis_version: "noise-2"},
          results: [{method: "statistics", label: "Residual statistics", run_id: null,
            result: {status: resultStatus, reason: resultStatus === "ok" ? null : "Not enough new samples",
              metrics: [], plots: [], tables: [], warnings: []}}]},
      };
    },
  });
  const flush = () => new Promise(setImmediate);
  const status = (run, phase = "recording") => ({experiment: {
    batch_id: "batch-a", run_id: run, active: true, outcome: "running", phase,
  }});
  panel.update(status("run-0001"));
  await flush();
  assert.equal(recipes[0].scope, "batch");
  assert.equal(recipes[0].mode, "cumulative");
  assert.ok(!("window_size" in recipes[0]));
  const [id, callback] = timers.entries().next().value;
  timers.delete(id); callback();
  await flush();
  const card = getElement("analysis-cards").children[0];
  assert.ok(card);
  assert.equal(card.dataset.method, "statistics");
  resultStatus = "unavailable";
  panel.update(status("run-0002"));
  assert.equal(getElement("analysis-cards").children[0], card);
  assert.match(getElement("analysis-state").textContent, /updating/i);
  await flush();
  assert.deepEqual(errors, []);
  const [nextId, nextCallback] = timers.entries().next().value;
  timers.delete(nextId); nextCallback();
  await flush();
  assert.equal(getElement("analysis-cards").children[0], card,
    "a temporarily unavailable method must retain its last successful result with a notice");
  assert.ok(card.children.some((child) => /Not enough new samples.*previous result/i.test(child.textContent)));
  getElement("analysis-dataset").listeners.change();
  assert.equal(getElement("analysis-cards").children[0], card);
});
