const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { experimentPayload, experimentLocked } = require("../uwb_lab/static/experiments.js");
const { resultMatches, chartBounds, validateAnalysisPreferences } = require("../uwb_lab/static/analysis.js");

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
  assert.throws(() => validateAnalysisPreferences({...preferences, window_size:2001}));
  assert.throws(() => validateAnalysisPreferences({...preferences, methods:["invented"]}));
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
