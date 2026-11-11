const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { experimentPayload, experimentLocked } = require("../uwb_lab/static/experiments.js");
const {
  resultMatches, chartBounds, validateAnalysisPreferences, formatAnalysisValue, analysisCardSummary,
  analysisSourceSummary, analysisLegendLabels, comparisonMetricRows, overlaySeries, colorWithAlpha,
  comparisonDeltaClass, comparisonMetricTiles, comparisonLegendLabels,
} = require("../uwb_lab/static/analysis.js");

test("experiment inputs preserve duration, integer repetitions and interval", () => {
  assert.deepEqual(experimentPayload({
    name: "LOS", notes: "", duration_s: "10", run_count: "3", interval_s: "2",
    start_delay_s: "3", manual_start: "on",
  }), { name: "LOS", notes: "", duration_s: 10, run_count: 3, interval_s: 2, start_delay_s: 3, manual_start: true });
  for (const fields of [{duration_s: ""}, {run_count: "1.5"}, {interval_s: "-1"}, {start_delay_s: "-1"}]) {
    assert.throws(() => experimentPayload({name: "", duration_s: "10", run_count: "3", interval_s: "2", start_delay_s: "0", ...fields}));
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
    histogram_bins:20, max_lag:10, components:3, seed:4, compare_show_fits: true,
  });
  assert.deepEqual(preferences.methods, ["statistics","distribution"]);
  assert.equal(preferences.fit, "gmm", "legacy GMM selection becomes the GMM distribution model");
  assert.equal(preferences.components, 3);
  assert.equal(preferences.compare_show_fits, true);
  assert.equal(validateAnalysisPreferences({methods:["statistics"]}).compare_show_fits, false);
  assert.throws(() => validateAnalysisPreferences({...preferences, fit:"invented"}));
  assert.throws(() => validateAnalysisPreferences({...preferences, histogram_bins:201}));
  assert.throws(() => validateAnalysisPreferences({...preferences, methods:["invented"]}));
});

test("analysis display uses three decimal places without changing counts or hiding small spectral values", () => {
  assert.equal(formatAnalysisValue(2.123456, "m"), "2.123");
  assert.equal(formatAnalysisValue(-0.0001, "m"), "0.000");
  assert.equal(formatAnalysisValue(-0.0016, "m"), "-0.002");
  assert.equal(formatAnalysisValue(0, "m"), "0.000");
  assert.equal(formatAnalysisValue(3201, ""), "3201");
  assert.equal(formatAnalysisValue(64, "samples"), "64");
  assert.equal(formatAnalysisValue(10, "Hz"), "10.000");
  assert.equal(formatAnalysisValue(12.345678, ""), "12.346");
  assert.equal(formatAnalysisValue(0.12, ""), "0.120");
  assert.equal(formatAnalysisValue(0.0000123456, "m^2/Hz"), "1.235e-5");
  assert.equal(formatAnalysisValue(null, "m"), "--");
  assert.equal(formatAnalysisValue(NaN, "m"), "--");
  assert.equal(formatAnalysisValue(0.01234, "m", true), "+0.012");
  assert.equal(formatAnalysisValue(-0.00001, "m", true), "0.000");
});

test("comparison tables keep metric order and show per-batch deltas", () => {
  const rows = comparisonMetricRows(
    [{key: "bias_m", label: "Bias", value: 0.01, unit: "m"}, {key: "std_m", label: "Std dev", value: 0.02, unit: "m"}],
    [{key: "bias_m", label: "Bias", value: 0.013, unit: "m"}, {key: "std_m", label: "Std dev", value: 0.018, unit: "m"}]
  );
  assert.deepEqual(rows, [
    ["Bias", "0.010", "0.013", "+0.003"],
    ["Std dev", "0.020", "0.018", "-0.002"],
  ]);
});

test("comparison metric tiles include trend metadata for boxed rendering", () => {
  const tiles = comparisonMetricTiles(
    [{key: "bias_m", label: "Bias", value: 0.01, unit: "m"}],
    [{key: "bias_m", label: "Bias", value: 0.013, unit: "m"}]
  );
  assert.deepEqual(tiles, [{
    key: "bias_m",
    label: "Bias",
    left: "0.010",
    right: "0.013",
    delta: "+0.003",
    trend: "up",
  }]);
});

test("comparison overlay keeps only empirical histograms and applies stronger opacity", () => {
  const series = overlaySeries(
    [{label: "Empirical density", style: "bars", x: [0, 1, 2], y: [1, 2, 3]}, {label: "Gaussian", style: "line", x: [0], y: [1]}],
    [{label: "Empirical density", style: "bars", x: [0.5, 1.5, 2.5], y: [3, 2, 1]}, {label: "Laplace", style: "line", x: [0], y: [1]}],
    "A", "B"
  );
  assert.equal(series.length, 2);
  assert.equal(series[0].label, "Empirical density (A)");
  assert.equal(series[0].alpha, 0.62);
  assert.equal(series[1].alpha, 0.62);
  assert.ok(series.every((curve) => curve.style === "bars"));
  assert.equal(series[1].label, "Empirical density (B)");
  assert.deepEqual(series[0].x, [0, 1, 2], "batch A bins should be the alignment anchor");
  assert.deepEqual(series[1].x, [0, 1, 2], "batch B bins should align to batch A without centering");
  assert.ok(series[0].y.every((value) => value > 0), "aligned batch A bars should stay filled");
  assert.ok(series[1].y.every((value) => value > 0), "aligned batch B bars should stay filled");
});

test("comparison overlay can include fitted curves when requested", () => {
  const series = overlaySeries(
    [{label: "Empirical density", style: "bars", x: [0, 1], y: [1, 2]}, {label: "Gaussian", style: "line", x: [0, 1], y: [1, 1]}],
    [{label: "Empirical density", style: "bars", x: [0.2, 1.2], y: [2, 1]}, {label: "Laplace", style: "line", x: [0.2, 1.2], y: [1, 1]}],
    "A", "B", true
  );
  assert.equal(series.length, 4);
  assert.ok(series.some((curve) => curve.label === "Gaussian (A)" && curve.style === "line" && curve.alpha === 1));
  assert.ok(series.some((curve) => curve.label === "Laplace (B)" && curve.style === "line" && curve.alpha === 1));
  const gaussian = series.find((curve) => curve.label === "Gaussian (A)").x;
  const laplace = series.find((curve) => curve.label === "Laplace (B)").x;
  gaussian.forEach((value, index) => assert.ok(Math.abs(value - [0, 1][index]) < 1e-9));
  laplace.forEach((value, index) => assert.ok(Math.abs(value - [0, 1][index]) < 1e-9));
});

test("comparison delta color direction maps larger to green and smaller to red", () => {
  assert.equal(comparisonDeltaClass(0.01), "up");
  assert.equal(comparisonDeltaClass(-0.01), "down");
  assert.equal(comparisonDeltaClass(0), "same");
  assert.equal(comparisonDeltaClass(null), "same");
});

test("comparison legend labels are intentionally suppressed", () => {
  assert.deepEqual(
    comparisonLegendLabels([{label: "Empirical density (A)"}, {label: "Gaussian (B)"}]),
    []
  );
});

test("hex chart colors can be converted to rgba transparency", () => {
  assert.equal(colorWithAlpha("#176b87", 0.5), "rgba(23, 107, 135, 0.5)");
  assert.equal(colorWithAlpha("#abc", 0.25), "rgba(170, 187, 204, 0.25)");
  assert.equal(colorWithAlpha("#176b87", 1), "#176b87");
});

test("statistic values and units stay together while tiles reflow", () => {
  const css = fs.readFileSync(require.resolve("../uwb_lab/static/app.css"), "utf8");
  assert.match(css, /\.analysis-metrics \.metric-value \{[^}]*flex-wrap:\s*nowrap/);
  assert.match(css, /\.analysis-metrics \.metric-value \{[^}]*white-space:\s*nowrap/);
  assert.doesNotMatch(css, /\.metric-value strong \{[^}]*overflow-wrap:\s*anywhere/);
  assert.match(css, /\.analysis-metrics \{[^}]*display:\s*flex;[^}]*flex-wrap:\s*wrap/);
  assert.match(css, /\.analysis-metrics \.metric-tile \{[^}]*min-width:\s*max-content/);
  assert.match(css, /\.metrics div \{[^}]*white-space:\s*nowrap/);
});

test("residual cards keep concise error labels without repeating shared counts or descriptions", () => {
  const metrics = [
    {key: "count", label: "Eligible samples", value: 3201, unit: ""},
    {key: "bias_m", label: "Bias", value: 0.012345, unit: "m"},
    {key: "std_m", label: "Sample standard deviation", value: 0.004567, unit: "m"},
    {key: "rmse_m", label: "RMSE", value: 0.013456, unit: "m"},
    {key: "p95_abs_m", label: "P95 absolute error", value: 0.024567, unit: "m"},
  ];
  const summary = analysisCardSummary("statistics", metrics);
  assert.equal(summary.badges, undefined);
  assert.equal(summary.description, undefined);
  assert.deepEqual(summary.metrics.map((metric) => metric.key), ["bias_m", "std_m", "rmse_m", "p95_abs_m"]);
  assert.equal(summary.metrics[0].value, 0.012345, "presentation must not round source values");
  assert.equal(summary.metrics[1].label, "Std dev");
  assert.equal(summary.metrics[1].title, "Sample standard deviation");
});

test("spectrum summary distinguishes Welch density from timestamp-aware Lomb-Scargle power", () => {
  const shared = [
    {key: "count", value: 294, unit: ""},
    {key: "runs", value: 5, unit: ""},
  ];
  const welch = analysisCardSummary("psd", [...shared,
    {key: "fs_hz", value: 10, unit: "Hz"}, {key: "segment", value: 59, unit: "samples"},
    {key: "resolution_hz", value: 10 / 59, unit: "Hz"},
  ]);
  assert.equal(welch.badges, undefined);
  assert.equal(welch.estimator, "Welch PSD");
  assert.deepEqual(welch.metrics.map((metric) => metric.key), ["fs_hz", "segment", "resolution_hz"]);
  const lomb = analysisCardSummary("psd", [...shared,
    {key: "missing_samples", value: 3, unit: ""},
    {key: "resolution_hz", value: 0.018857, unit: "Hz"},
  ]);
  assert.equal(lomb.estimator, "Lomb-Scargle");
  assert.deepEqual(lomb.metrics.map((metric) => metric.key), ["missing_samples", "resolution_hz"]);
  const unavailable = analysisCardSummary("psd", []);
  assert.equal(unavailable.estimator, null);
  assert.deepEqual(unavailable.metrics, []);
});

test("analysis context shows shared counts once and keeps provenance in Details", () => {
  const output = {
    included_count: 294, mode: "cumulative", excluded: {invalid: 2, missing_prior: 0},
    provenance: {runs: [{run_id: "one"}, {run_id: "two"}], batch_outcome: "completed",
      batch_id: "batch-a", source_sha256: "abcdef123456", analysis_version: "noise-3", spectrum_runs: ["one", "two"]},
  };
  const source = analysisSourceSummary(output, "corrected_m");
  assert.equal(source.text, "294 samples / 2 runs / Corrected residuals / 2 excluded");
  assert.ok(source.text.length < 85);
  assert.match(source.details, /Batch completed/);
  assert.match(source.details, /abcdef123456/);
  assert.match(source.details, /noise-3/);
  assert.match(source.details, /recorded expected distance/);
  assert.match(source.details, /spectrum pools all 2 recorded runs/);
});

test("legends retain short multi-curve names but omit redundant single-curve captions", () => {
  assert.deepEqual(analysisLegendLabels([{label: "Pooled Welch PSD"}]), []);
  assert.deepEqual(analysisLegendLabels([{label: "Empirical density"}, {label: "Gaussian"}]),
    ["Samples", "Gaussian"]);
  assert.deepEqual(
    analysisLegendLabels([{label: "Empirical density"}, {label: "Gaussian mixture (2)"}, {label: "Component 1"}]),
    ["Samples", "Gaussian mixture (2)", "C1"]);
});

test("distribution cards show fit diagnostics directly with short labels", () => {
  const summary = analysisCardSummary("distribution", [
    {key: "log_likelihood", label: "Mean log likelihood", value: 0.5}, {key: "aic", label: "AIC", value: 10},
    {key: "bic", label: "BIC", value: 20}, {key: "ks", label: "KS distance", value: 0.02},
  ]);
  assert.deepEqual(summary.metrics.map((metric) => metric.key), ["log_likelihood", "aic", "bic", "ks"]);
  assert.equal(summary.metrics[0].label, "Log-lik / sample");
  assert.equal(summary.detailMetrics, undefined);
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
    assert.match(getElement("analysis-source").textContent, /10 samples/);
    assert.match(getElement("analysis-provenance").textContent, /Batch completed/);
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
  const legacyPreferences = {
    field: "raw_m", methods: ["statistics", "histogram", "acf", "psd", "gmm"], fit: "laplace",
    window_size: 2000, histogram_bins: 17, max_lag: 30, components: 3, seed: 4,
  };
  const environment = {
    document: {getElementById: getElement, createElement: () => new Element(), createTextNode: (text) => text},
    window: {addEventListener() {}}, localStorage: {getItem: () => JSON.stringify(legacyPreferences)},
    Option: class extends Element { constructor(text, value) { super(); this.textContent = text; this.value = value; } },
    setTimeout: (callback) => { timers.set(++timerId, callback); return timerId; },
    clearTimeout: (id) => timers.delete(id),
  };
  vm.runInNewContext(fs.readFileSync(require.resolve("../uwb_lab/static/analysis.js"), "utf8"), environment);
  const panel = environment.startAnalysisPanel({
    showError: (_, message) => { if (message) errors.push(message); },
    async request(path, options) {
      if (path === "/api/analysis/methods") return {methods: {
        statistics: {label: "Statistics"}, distribution: {label: "Distribution fit"},
        psd: {label: "Spectrum"},
      }};
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
              metrics: [], plots: [], tables: [], warnings: []}},
            {method: "distribution", label: "Distribution fit", run_id: null, result: {
              status: "ok", metrics: [], plots: [], tables: [
                {title: "Compare models", columns: ["Model", "AIC"], rows: [["Gaussian", 1]]}],
              warnings: ["Components are exploratory, not LOS/NLOS labels; covariance regularization = 1e-10 m^2",
                "Numerical fit warning"],
            }}]},
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
  assert.ok(!("max_lag" in recipes[0]));
  assert.deepEqual(recipes[0].methods, ["statistics", "distribution", "psd"]);
  assert.equal(recipes[0].fit, "laplace");
  assert.equal(recipes[0].field, "raw_m");
  assert.equal(recipes[0].histogram_bins, 17);
  assert.equal(recipes[0].components, 3);
  assert.equal(recipes[0].seed, 4);
  const [id, callback] = timers.entries().next().value;
  timers.delete(id); callback();
  await flush();
  const card = getElement("analysis-cards").children[0];
  assert.ok(card);
  assert.equal(card.dataset.method, "statistics");
  const mixture = getElement("analysis-cards").children[1];
  assert.ok(mixture.children.some((child) => child.textContent === "Numerical fit warning"),
    "numerical warnings must stay visible, not be folded into methodology notes");
  const header = mixture.children.find((child) => child.className === "analysis-card-header");
  const info = header.children[0].children.find((child) => child.className === "info");
  assert.ok(info && info.dataset.tip.startsWith("Components are exploratory"));
  const details = mixture.children.find((child) => child.className === "analysis-method-details");
  assert.ok(details, "model comparison is available behind a disclosure");
  assert.equal(details.open, false);
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
