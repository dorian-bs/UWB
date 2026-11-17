const ANALYSIS_FITS = ["auto", "gaussian", "gmm", "lognormal", "student_t", "laplace", "skew_normal"];
const ANALYSIS_DEFAULTS = {
  field: "corrected_m", methods: ["statistics", "distribution", "psd"],
  fit: "gaussian", histogram_bins: 30, components: 2, seed: 0, compare_show_fits: false,
};

function validateAnalysisPreferences(value, methodIds = ANALYSIS_DEFAULTS.methods) {
  if (!value || typeof value !== "object") throw new Error("Invalid analysis preferences");
  const result = {...ANALYSIS_DEFAULTS};
  for (const key of Object.keys(result)) if (key in value) result[key] = value[key];
  if (Array.isArray(result.methods)) {
    // Older settings had separate histogram and GMM cards; they are now one distribution fit.
    if (!("fit" in value) && result.methods.includes("gmm")) result.fit = "gmm";
    result.methods = result.methods.map((method) => ["histogram", "gmm"].includes(method) ? "distribution" : method);
  }
  if (!ANALYSIS_FITS.includes(result.fit)) throw new Error("Invalid distribution model");
  if (!["raw_m", "corrected_m"].includes(result.field) || !Array.isArray(result.methods) ||
      result.methods.some((method) => method !== "acf" && !methodIds.includes(method))) throw new Error("Invalid analysis methods");
  for (const [name, minimum, maximum] of [
    ["histogram_bins", 1, 200],
    ["components", 1, 6], ["seed", 0, 4294967295],
  ]) {
    if (!Number.isInteger(result[name]) || result[name] < minimum || result[name] > maximum) {
      throw new Error(`Invalid ${name.replaceAll("_", " ")}`);
    }
  }
  if (typeof result.compare_show_fits !== "boolean") throw new Error("Invalid comparison fit visibility");
  result.methods = [...new Set(result.methods.filter((method) => method !== "acf"))];
  return result;
}

function formatAnalysisValue(value, unit = "", signed = false) {
  if (value == null || (typeof value === "number" && !Number.isFinite(value))) return "--";
  if (typeof value !== "number") return String(value);
  let formatted;
  if (unit !== "m" && value !== 0 && Math.abs(value) < 0.001) {
    formatted = value.toExponential(3);
  } else if (Number.isInteger(value) && ["", "samples"].includes(unit)) {
    formatted = String(value);
  } else {
    const rounded = Number(value.toFixed(3));
    formatted = (Object.is(rounded, -0) ? 0 : rounded).toFixed(3);
  }
  return signed && Number(formatted) >= 0 ? `+${formatted}` : formatted;
}

function analysisCardSummary(method, metrics) {
  const headerKeys = method === "statistics" ? ["count"] : method === "psd" ? ["count", "runs"] : [];
  let estimator = null;
  if (method === "psd") {
    if (metrics.some((metric) => metric.key === "missing_samples")) {
      estimator = "Lomb-Scargle";
    } else if (metrics.some((metric) => metric.key === "fs_hz")) {
      estimator = "Welch PSD";
    }
  }
  const labels = {
    std_m: "Std dev", p95_abs_m: "P95 error", log_likelihood: "Log-lik / sample", ks: "KS distance",
    fs_hz: "Sample rate", segment: "Segment", missing_samples: "Missing",
    resolution_hz: estimator === "Lomb-Scargle" ? "Grid step" : "Bin width",
  };
  return {
    estimator,
    metrics: metrics.filter((metric) => !headerKeys.includes(metric.key))
      .map((metric) => {
        const scope = metric.label?.match(/ \((?:full selection|window)\)$/)?.[0] || "";
        return {...metric, title: metric.label, label: labels[metric.key] ? labels[metric.key] + scope : metric.label};
      }),
  };
}

function analysisSourceSummary(output, field) {
  const provenance = output.provenance, runs = provenance.runs || [];
  const excluded = Object.values(output.excluded).reduce((sum, count) => sum + count, 0);
  const parts = [`${output.included_count} samples`];
  if (runs.length) parts.push(`${runs.length} run${runs.length === 1 ? "" : "s"}`);
  parts.push(`${field === "raw_m" ? "Raw" : "Corrected"} residuals`);
  if (excluded) parts.push(`${excluded} excluded`);
  if (!["completed", "running"].includes(provenance.batch_outcome)) parts.push(provenance.batch_outcome);
  return {
    text: parts.join(" / "),
    details: `Batch ${provenance.batch_outcome} (${provenance.batch_id}). Runs: ${runs.map((run) => run.run_id).join(", ")}. ` +
      `Field: ${field}. Exclusions: ${JSON.stringify(output.excluded)}. ` +
      `Source ${provenance.source_sha256}; ${provenance.analysis_version}. ` +
      "Residual = range - recorded expected distance; prior errors are included. " +
      "All eligible samples are pooled by sample count. Repeated runs need not be independent." +
      (provenance.spectrum_runs?.length ? ` The spectrum pools all ${provenance.spectrum_runs.length} recorded runs of the batch.` : ""),
  };
}

function analysisLegendLabels(series) {
  if (series.length < 2) return [];
  return series.map((curve) =>
    curve.label === "Empirical density" ? "Samples" : curve.label.replace(/^Component (\d+)$/, "C$1"));
}

function comparisonLegendLabels(_series) {
  return [];
}

function comparisonMetricRows(primaryMetrics, secondaryMetrics) {
  const index = (metrics) => new Map(metrics.map((metric) => [metric.key, metric]));
  const left = index(primaryMetrics), right = index(secondaryMetrics);
  const orderedKeys = [...new Set([...primaryMetrics, ...secondaryMetrics].map((metric) => metric.key))];
  return orderedKeys.map((key) => {
    const first = left.get(key), second = right.get(key);
    const unit = first?.unit || second?.unit || "";
    const label = first?.label || second?.label || key;
    const delta = typeof first?.value === "number" && typeof second?.value === "number"
      ? second.value - first.value : null;
    return [
      label,
      formatAnalysisValue(first?.value ?? null, unit),
      formatAnalysisValue(second?.value ?? null, unit),
      formatAnalysisValue(delta, unit, true),
    ];
  });
}

function comparisonDeltaClass(delta) {
  if (typeof delta !== "number" || !Number.isFinite(delta) || delta === 0) return "same";
  return delta > 0 ? "up" : "down";
}

function comparisonMetricTiles(primaryMetrics, secondaryMetrics) {
  return comparisonMetricRows(primaryMetrics, secondaryMetrics).map(([label, left, right, delta]) => {
    const leftNumber = Number(left), rightNumber = Number(right);
    const trend = comparisonDeltaClass(
      Number.isFinite(leftNumber) && Number.isFinite(rightNumber) ? rightNumber - leftNumber : null
    );
    const key = [...primaryMetrics, ...secondaryMetrics].find((metric) => metric.label === label)?.key || label;
    return {key, label, left, right, delta, trend};
  });
}

function colorWithAlpha(color, alpha) {
  if (!Number.isFinite(alpha) || alpha >= 1) return color;
  const clamped = Math.max(0, Math.min(1, alpha));
  const short = /^#([\da-f]{3})$/i.exec(color);
  if (short) {
    const [r, g, b] = short[1].split("").map((digit) => Number.parseInt(digit + digit, 16));
    return `rgba(${r}, ${g}, ${b}, ${clamped})`;
  }
  const full = /^#([\da-f]{6})$/i.exec(color);
  if (full) {
    const r = Number.parseInt(full[1].slice(0, 2), 16);
    const g = Number.parseInt(full[1].slice(2, 4), 16);
    const b = Number.parseInt(full[1].slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${clamped})`;
  }
  return color;
}

function alignHistogramSeries(left, right) {
  if (left.x.length !== right.x.length) return [left, right];
  const rightShift = left.x[0] - right.x[0];
  return [left, shiftSeriesX(right, rightShift)];
}

function shiftSeriesX(series, delta) {
  if (!delta) return series;
  return {...series, x: series.x.map((value) => value + delta)};
}

function overlaySeries(left, right, leftLabel, rightLabel, includeFits = false) {
  const leftPalette = ["#176b87", "#087f5b", "#966000", "#7851a9"];
  const rightPalette = ["#b63036", "#6d3ca4", "#b35c10", "#536878"];
  const leftBars = left.find((curve) => curve.label === "Empirical density");
  const rightBars = right.find((curve) => curve.label === "Empirical density");
  if (!leftBars || !rightBars) return [];
  const [alignedLeftBars, alignedRightBars] = alignHistogramSeries(leftBars, rightBars);
  const leftDelta = alignedLeftBars.x[0] - leftBars.x[0];
  const rightDelta = alignedRightBars.x[0] - rightBars.x[0];
  const withStyle = (series, palette, suffix) => [series]
    .map((curve, index) => ({
    ...curve,
    style: "bars",
    label: `${curve.label} (${suffix})`,
    color: palette[index % palette.length],
    alpha: 0.62,
  }));
  const combined = [
    ...withStyle(alignedLeftBars, leftPalette, leftLabel),
    ...withStyle(alignedRightBars, rightPalette, rightLabel),
  ];
  if (!includeFits) return combined;
  const fitted = (series, palette, suffix, delta) => series
    .filter((curve) => curve.label !== "Empirical density")
    .map((curve, index) => ({
      ...shiftSeriesX(curve, delta),
      label: `${curve.label} (${suffix})`,
      color: palette[(index + 1) % palette.length],
      alpha: 1,
    }));
  return [
    ...combined,
    ...fitted(left, leftPalette, leftLabel, leftDelta),
    ...fitted(right, rightPalette, rightLabel, rightDelta),
  ];
}

function resultMatches(selection, job) {
  return selection.jobId === job.job_id && selection.recipeKey === job.recipe_key;
}

function chartBounds(series) {
  const xs = [], ys = [0];
  for (const curve of series) {
    if (curve.x.length !== curve.y.length) throw new Error("Mismatched analysis coordinates");
    for (let index = 0; index < curve.x.length; index++) {
      if (!Number.isFinite(curve.x[index]) || !Number.isFinite(curve.y[index])) {
        throw new Error("Non-finite analysis coordinates");
      }
      xs.push(curve.x[index]); ys.push(curve.y[index]);
    }
  }
  if (!xs.length) throw new Error("No plot coordinates");
  const extent = (values) => {
    let min = values[0], max = values[0];
    for (const value of values) { min = Math.min(min, value); max = Math.max(max, value); }
    const padding = max > min ? (max - min) * 0.06 : Math.max(Math.abs(min) * 0.05, 0.001);
    return { min: min - padding, max: max + padding };
  };
  return {x: extent(xs), y: extent(ys)};
}

function chartTheme() {
  if (document.documentElement.dataset.theme === "light") {
    return {
      text: "#52615e", grid: "#dfe5e2", zero: "#8a9893", panel: "#fff",
      series: {raw_m: "#536878", corrected_m: "#176b87", expected_m: "#966000", residual_m: "#b63036", rolling_bias_m: "#7851a9", rolling_std_m: "#087f5b", rate_hz: "#0a8f5a", requested_hz: "#6b7a76"},
      curves: ["#176b87", "#087f5b", "#966000", "#b63036", "#7851a9", "#536878", "#b35c10"],
    };
  }
  return {
    text: "#9aaaa5", grid: "#2a3532", zero: "#6f7e79", panel: "#18201e", series: {},
    curves: ["#4fb3d1", "#3ecf9f", "#e0a73a", "#ff6b72", "#b08fe0", "#8fa7b8", "#d47729"],
  };
}

function drawAnalysisChart(canvas, plot) {
  const theme = chartTheme();
  const bounds = chartBounds(plot.series);
  const width = canvas.clientWidth || 400, height = 220, ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio; canvas.height = height * ratio;
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  const area = {left: 66, right: width - 12, top: 12, bottom: 178};
  const x = (value) => area.left + (value - bounds.x.min) / (bounds.x.max - bounds.x.min) * (area.right - area.left);
  const y = (value) => area.bottom - (value - bounds.y.min) / (bounds.y.max - bounds.y.min) * (area.bottom - area.top);
  const xUnit = plot.x_label.match(/\(([^)]+)\)$/)?.[1] || "";
  const yUnit = plot.y_label.match(/\(([^)]+)\)$/)?.[1] || "";
  context.fillStyle = theme.panel; context.fillRect(0, 0, width, height);
  context.font = "10px system-ui"; context.lineWidth = 1;
  for (let tick = 0; tick <= 4; tick++) {
    const value = bounds.y.min + tick * (bounds.y.max - bounds.y.min) / 4;
    context.strokeStyle = theme.grid; context.beginPath(); context.moveTo(area.left, y(value)); context.lineTo(area.right, y(value)); context.stroke();
    context.fillStyle = theme.text; context.textAlign = "right"; context.fillText(formatAnalysisValue(value, yUnit), area.left - 5, y(value) + 3);
    const xv = bounds.x.min + tick * (bounds.x.max - bounds.x.min) / 4;
    context.textAlign = "center"; context.fillText(formatAnalysisValue(xv, xUnit), x(xv), area.bottom + 15);
  }
  const colors = theme.curves;
  plot.series.forEach((curve, index) => {
    const color = curve.color || colors[index % colors.length];
    context.strokeStyle = context.fillStyle = colorWithAlpha(color, curve.alpha ?? 1);
    context.lineWidth = 1.6; context.beginPath();
    const barWidth = curve.x.length > 1 ? Math.max(1, Math.abs(x(curve.x[1]) - x(curve.x[0])) * 0.85) : 10;
    curve.x.forEach((value, point) => {
      const px = x(value), py = y(curve.y[point]);
      if (curve.style === "bars") context.fillRect(px - barWidth / 2, Math.min(py, y(0)), barWidth, Math.max(1, Math.abs(y(0) - py)));
      else if (curve.style === "stems") { context.moveTo(px, y(0)); context.lineTo(px, py); }
      else if (curve.style === "points") { context.moveTo(px + 2, py); context.arc(px, py, 2, 0, 2 * Math.PI); }
      else if (point === 0) context.moveTo(px, py);
      else context.lineTo(px, py);
    });
    context.stroke();
  });
  context.fillStyle = theme.text; context.textAlign = "center";
  context.fillText(plot.x_label, width / 2, 214);
  context.save(); context.translate(12, 95); context.rotate(-Math.PI / 2);
  context.fillText(plot.y_label, 0, 0); context.restore();
}

function startAnalysisPanel({request, showError}) {
  const byId = (id) => document.getElementById(id);
  const storageKey = "uwb-analysis-v1";
  let preferences = validateAnalysisPreferences(ANALYSIS_DEFAULTS);
  let catalogue = null, status = null, batchesKey = null, currentKey = null;
  let selection = null, generation = 0, busy = false, dirty = true, lastComputed = 0;
  let mode = "cumulative", plots = [], timer = null, hasResults = false;
  let cards = new Map();
  const compareOpen = new Map();
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  function renderExperimentConfig(batch) {
    const config = batch.config || {}, prior = batch.prior || {};
    const text = (value, unit = "") => value === null || value === undefined || value === "" ? null : `${value}${unit}`;
    const hex = (value) => Number.isFinite(value) ? `0x${value.toString(16).padStart(8, "0").toUpperCase()}` : null;
    const radio = (state) => state ? [
      ["Channel", state.channel], ["Preamble", text(state.preamble)],
      ["Data rate", state.data_rate === 6800 ? "6.8 Mbps" : text(state.data_rate, " kbps")],
      ["Rate", text(state.period_ms ? 1000 / state.period_ms : null, " Hz")],
      ["TX power", hex(state.tx_power)], ["Antenna delay", text(state.tx_ant_dly)],
    ] : [];
    const boards = batch.radio?.boards || {}, tx = boards.tx?.state, rx = boards.rx?.state;
    const txRows = radio(tx), rxRows = radio(rx);
    const radioItems = txRows.map(([name, value], index) => {
      const other = rxRows[index]?.[1];
      return !rx || value === other ? [name, value] : [name, `TX ${value} / RX ${other}`];
    });
    const rows = [
      ["Experiment", [["Name", config.name], ["Runs", text(config.run_count, ` x ${config.duration_s} s`)], ["Source", batch.source]]],
      ["Prior", [["Distance", text(prior.expected_distance_m, " m")], ["Uncertainty", text(prior.uncertainty_m, " m")]]],
      ["Radio", radioItems.length ? radioItems : [["Settings", "not recorded"]]],
    ];
    const element = node("table", null, "analysis-table config-table"), body = node("tbody");
    for (const [title, items] of rows) {
      const row = node("tr");
      row.append(node("th", title));
      const cell = node("td");
      cell.textContent = items.filter(([, value]) => value !== null && value !== undefined && value !== "")
        .map(([name, value]) => `${name} ${value}`).join("  ·  ") || "--";
      row.append(cell); body.append(row);
    }
    element.append(body);
    byId("experiment-config-body").replaceChildren(element);
  }
  function table(columns, rows) {
    const wrapper = node("div", null, "table-scroll");
    const element = node("table", null, "analysis-table"), head = node("thead"), heading = node("tr");
    columns.forEach((column) => heading.append(node("th", column)));
    head.append(heading); element.append(head);
    const body = node("tbody");
    rows.forEach((row) => {
      const tr = node("tr");
      row.forEach((value, index) => tr.append(node("td",
        formatAnalysisValue(value, columns[index].match(/\(([^)]+)\)$/)?.[1] || ""))));
      body.append(tr);
    });
    element.append(body); wrapper.append(element);
    return wrapper;
  }
  function batchLabel(batch) {
    const name = batch.config?.name || "Batch";
    const started = batch.started_at ? batch.started_at.slice(0, 19) : batch.batch_id;
    return `${name} (${started})`;
  }
  function runRows(batch) {
    return batch.runs.map((run) => {
      const summary = run.summaries?.[preferences.field] || {};
      return [run.run_id, run.state, summary.count ?? 0, run.excluded_count ?? 0,
        run.sample_yield_pct, summary.bias_m, summary.std_m, summary.rmse_m];
    });
  }
  function renderRunComparison(primaryBatch, secondaryBatch = null) {
    const holder = byId("run-comparison");
    if (!secondaryBatch) {
      holder.replaceChildren(table(
        ["Run", "State", "Eligible n", "Excluded n", "Yield (%)", "Bias (m)", "Sample std. (m)", "RMSE (m)"],
        runRows(primaryBatch)
      ));
      return;
    }
    const leftTitle = node("h3", `Runs: ${batchLabel(primaryBatch)}`, "plot-note");
    const rightTitle = node("h3", `Runs: ${batchLabel(secondaryBatch)}`, "plot-note");
    holder.replaceChildren(
      leftTitle,
      table(["Run", "State", "Eligible n", "Excluded n", "Yield (%)", "Bias (m)", "Sample std. (m)", "RMSE (m)"], runRows(primaryBatch)),
      rightTitle,
      table(["Run", "State", "Eligible n", "Excluded n", "Yield (%)", "Bias (m)", "Sample std. (m)", "RMSE (m)"], runRows(secondaryBatch))
    );
  }
  function renderComparison(primaryBatch, primaryOutput, secondaryBatch, secondaryOutput) {
    const primaryLabel = batchLabel(primaryBatch), secondaryLabel = batchLabel(secondaryBatch);
    const left = analysisSourceSummary(primaryOutput, preferences.field);
    const right = analysisSourceSummary(secondaryOutput, preferences.field);
    byId("analysis-source").textContent =
      `${primaryLabel}: ${primaryOutput.included_count} samples vs ${secondaryLabel}: ${secondaryOutput.included_count} samples`;
    byId("analysis-provenance").textContent = `${left.details}\n\n${right.details}`;
    byId("analysis-details").hidden = false;
    const cardsNode = byId("analysis-cards");
    const nextPlots = [];
    const cardsList = [];
    const residualLeft = primaryOutput.results.find((item) => item.method === "statistics")?.result;
    const residualRight = secondaryOutput.results.find((item) => item.method === "statistics")?.result;
    if (residualLeft?.status === "ok" && residualRight?.status === "ok") {
      const card = node("article", null, "analysis-card");
      const header = node("header", null, "analysis-card-header");
      const title = node("div", null, "analysis-card-title");
      title.append(node("h3", "Residual comparison"));
      header.append(title); card.append(header);
      const metrics = analysisCardSummary("statistics", residualLeft.metrics).metrics;
      const secondaryMetrics = analysisCardSummary("statistics", residualRight.metrics).metrics;
      const tiles = node("dl", null, "analysis-metrics");
      comparisonMetricTiles(metrics, secondaryMetrics).forEach(({key, label, left, right, delta, trend}) => {
        const tile = node("div", null, `metric-tile metric-tile--comparison metric-tile--${trend}`);
        tile.dataset.key = key;
        tile.append(node("dt", label));
        const value = node("dd", null, "metric-value");
        value.append(node("strong", right));
        tile.append(value);
        tile.append(node("p", `A ${left} → B ${right} (Δ ${delta})`, "plot-note"));
        tiles.append(tile);
      });
      card.append(tiles);
      cardsList.push(card);
    }
    const distLeft = primaryOutput.results.find((item) => item.method === "distribution")?.result;
    const distRight = secondaryOutput.results.find((item) => item.method === "distribution")?.result;
    if (distLeft?.status === "ok" && distRight?.status === "ok" &&
        distLeft.plots?.[0]?.series?.length && distRight.plots?.[0]?.series?.length) {
      const card = node("article", null, "analysis-card");
      const header = node("header", null, "analysis-card-header");
      const title = node("div", null, "analysis-card-title");
      title.append(node("h3", "Residual distribution overlap"));
      header.append(title); card.append(header);
      const fitLeft = analysisCardSummary("distribution", distLeft.metrics).metrics;
      const fitRight = analysisCardSummary("distribution", distRight.metrics).metrics;
      const fitTiles = node("dl", null, "analysis-metrics");
      comparisonMetricTiles(fitLeft, fitRight).forEach(({key, label, left, right, delta, trend}) => {
        const tile = node("div", null, `metric-tile metric-tile--comparison metric-tile--${trend}`);
        tile.dataset.key = key;
        tile.append(node("dt", label));
        const value = node("dd", null, "metric-value");
        value.append(node("strong", right));
        tile.append(value);
        tile.append(node("p", `A ${left} → B ${right} (Δ ${delta})`, "plot-note"));
        fitTiles.append(tile);
      });
      if (fitTiles.children.length) card.append(fitTiles);
      const canvas = node("canvas");
      const plot = {
        x_label: distLeft.plots[0].x_label,
        y_label: distLeft.plots[0].y_label,
        series: overlaySeries(
          distLeft.plots[0].series, distRight.plots[0].series, primaryLabel, secondaryLabel, preferences.compare_show_fits
        ),
      };
      canvas.setAttribute("aria-label", `Distribution overlap: ${plot.x_label}; ${plot.y_label}`);
      card.append(canvas);
      const labels = comparisonLegendLabels(plot.series);
      if (labels.length) card.append(node("p", labels.join(" / "), "plot-note"));
      cardsList.push(card);
      nextPlots.push({canvas, plot});
    }
    if (!cardsList.length) {
      cardsList.push(node("p", "Comparison needs both statistics and distribution results for the selected batches.", "plot-note"));
    }
    cardsNode.replaceChildren(...cardsList);
    plots = nextPlots;
    cards = new Map();
    hasResults = true;
    plots.forEach(({canvas, plot}) => drawAnalysisChart(canvas, plot));
  }
  function setComparisonMode(enabled) {
    for (const id of ["analysis-scope", "analysis-run-from", "analysis-run-to"]) byId(id).disabled = enabled;
    const excludePanel = byId("analysis-exclude");
    if (enabled) excludePanel.open = false;
    byId("analysis-exclude-summary").textContent = enabled
      ? "Exclude runs (disabled while comparing batches)"
      : excluded.size ? `Exclude runs (${excluded.size} excluded)` : "Exclude runs";
  }
  const excluded = new Set();
  let excludeKey = null;
  function renderExcludeList(batch) {
    const runs = batch.runs.filter((run) => run.session);
    const key = `${batch.batch_id}:${runs.map((run) => run.run_id).join(",")}`;
    if (excludeKey !== key) {
      if (!excludeKey || excludeKey.split(":")[0] !== batch.batch_id) excluded.clear();
      excludeKey = key;
      byId("analysis-exclude-list").replaceChildren(...runs.map((run) => {
        const box = node("input"), label = node("label");
        box.type = "checkbox"; box.value = run.run_id; box.checked = excluded.has(run.run_id);
        label.append(box, document.createTextNode(`#${batch.runs.indexOf(run) + 1}`));
        return label;
      }));
    }
    byId("analysis-exclude-summary").textContent = excluded.size ? `Exclude runs (${excluded.size} excluded)` : "Exclude runs";
  }
  byId("analysis-exclude-list").addEventListener("change", (event) => {
    const box = event.target;
    if (box.checked) excluded.add(box.value); else excluded.delete(box.value);
    byId("analysis-exclude-summary").textContent = excluded.size ? `Exclude runs (${excluded.size} excluded)` : "Exclude runs";
    mode = "cumulative"; invalidate(); tick();
  });
  function invalidate() {
    generation++; selection = null; dirty = true; lastComputed = 0;
    byId("analysis-state").textContent = hasResults
      ? "Updating..." : "Awaiting analysis";
  }
  function render(output) {
    const nextPlots = [], nextCards = [], nextEntries = new Map();
    const source = analysisSourceSummary(output, preferences.field);
    byId("analysis-source").textContent = source.text;
    byId("analysis-provenance").textContent = source.details;
    byId("analysis-details").hidden = false;
    const container = byId("analysis-cards");
    for (const item of output.results) {
      const result = item.result, previous = cards.get(item.method);
      if (result.status !== "ok" && previous?.successful) {
        previous.notice.hidden = false;
        previous.notice.textContent = `${result.reason}. Showing previous result.`;
        previous.notice.title = previous.source;
        nextCards.push(previous.card);
        nextPlots.push(...previous.plots);
        nextEntries.set(item.method, previous);
        continue;
      }
      const card = node("article", null, "analysis-card");
      card.dataset.method = item.method;
      const summary = analysisCardSummary(item.method, result.metrics);
      const header = node("header", null, "analysis-card-header");
      const titles = {statistics: "Residuals", distribution: "Distribution fit", psd: "Spectrum (all runs)"};
      const title = node("div", null, "analysis-card-title");
      title.append(node("h3", `${titles[item.method] || item.label}${item.run_id ? ` - ${item.run_id}` : ""}`));
      // Methodology notes live behind an info button instead of an extra disclosure.
      const methodNotes = result.warnings.filter((warning) =>
        item.method === "psd" || warning.startsWith("Components are exploratory"));
      if (methodNotes.length) {
        const info = node("button", "i", "info");
        info.type = "button";
        info.dataset.tip = methodNotes.join(" ");
        info.setAttribute("aria-label", info.dataset.tip);
        title.append(info);
      }
      header.append(title);
      if (summary.estimator) header.append(node("span", summary.estimator, "analysis-badge analysis-badge--method"));
      card.append(header);
      const notice = node("p", result.status === "ok" ? "" : `${result.status}: ${result.reason}`, "analysis-notice");
      notice.hidden = result.status === "ok";
      card.append(notice);
      const cardPlots = [];
      const metrics = node("dl", null, "analysis-metrics");
      for (const metric of summary.metrics) {
        const primary = item.method === "statistics" && metric.key === "bias_m";
        const cell = node("div", null, `metric-tile${primary ? " metric-tile--primary" : ""}`);
        cell.dataset.key = metric.key;
        const value = node("dd", null, "metric-value");
        value.append(node("strong", formatAnalysisValue(metric.value, metric.unit, primary)));
        if (metric.unit) value.append(node("span", metric.unit, "metric-unit"));
        const label = node("dt", metric.label);
        label.title = metric.title;
        cell.append(label, value);
        metrics.append(cell);
      }
      if (summary.metrics.length) card.append(metrics);
      const comparisons = [];
      for (const value of result.tables) {
        if (value.title === "Compare models") comparisons.push(value);
        else card.append(table(value.columns, value.rows));
      }
      for (const plot of result.plots) {
        const canvas = node("canvas");
        canvas.setAttribute("aria-label", `${item.label}: ${plot.x_label}; ${plot.y_label}`);
        card.append(canvas);
        const labels = analysisLegendLabels(plot.series);
        if (labels.length) {
          const legend = node("p", labels.join(" / "), "plot-note");
          legend.title = plot.series.map((curve) => curve.label).join(" / ");
          card.append(legend);
        }
        cardPlots.push({canvas, plot});
      }
      result.warnings.filter((warning) => !methodNotes.includes(warning))
        .forEach((warning) => card.append(node("p", warning, "plot-note")));
      for (const value of comparisons) {
        const details = node("details", null, "analysis-method-details");
        details.open = compareOpen.get(item.method) === true;
        details.addEventListener("toggle", () => { compareOpen.set(item.method, details.open); });
        details.append(node("summary", "Compare all models (lower AIC / BIC is better)"), table(value.columns, value.rows));
        card.append(details);
      }
      nextCards.push(card);
      nextPlots.push(...cardPlots);
      nextEntries.set(item.method, {
        card, notice, plots: cardPlots, successful: result.status === "ok",
        source: `${output.provenance.batch_id}, ${(output.provenance.runs || []).map((run) => run.run_id).join(", ") || "selected runs"}, ` +
          `${preferences.field}, ${output.included_count} eligible samples`,
      });
    }
    if (!output.results.length) nextCards.push(node("p", "Select one or more analysis methods.", "plot-note"));
    container.replaceChildren(...nextCards);
    plots = nextPlots;
    cards = nextEntries;
    hasResults = true;
    plots.forEach(({canvas, plot}) => drawAnalysisChart(canvas, plot));
  }
  function loadControls() {
    for (const key of ["field", "fit", "histogram_bins", "components", "seed"]) {
      byId("analysis-settings").querySelector(`[name="${key}"]`).value = preferences[key];
    }
    byId("analysis-settings").querySelector('[name="compare_show_fits"]').checked = preferences.compare_show_fits;
    byId("analysis-methods").replaceChildren();
    for (const [id, definition] of Object.entries(catalogue)) {
      const label = node("label", null, "check"), input = node("input");
      input.type = "checkbox"; input.value = id; input.checked = preferences.methods.includes(id);
      label.append(input, document.createTextNode(definition.label)); byId("analysis-methods").append(label);
    }
  }
  function changed() {
    try {
      const candidate = {
        field: byId("analysis-settings").querySelector('[name="field"]').value,
        fit: byId("analysis-settings").querySelector('[name="fit"]').value,
        compare_show_fits: byId("analysis-settings").querySelector('[name="compare_show_fits"]').checked,
      };
      for (const key of ["histogram_bins", "components", "seed"]) {
        const raw = byId("analysis-settings").querySelector(`[name="${key}"]`).value;
        if (raw === "") throw new Error(`Enter ${key.replaceAll("_", " ")}`);
        candidate[key] = Number(raw);
      }
      candidate.methods = [...byId("analysis-methods").querySelectorAll("input:checked")].map((input) => input.value);
      preferences = validateAnalysisPreferences(candidate, Object.keys(catalogue));
      try { localStorage.setItem(storageKey, JSON.stringify(preferences)); }
      catch (error) { showError("analysis-error", `Settings work but could not be saved: ${error.message}`); }
      invalidate(); tick();
    } catch (error) { showError("analysis-error", error.message); }
  }
  async function tick() {
    if (busy || !status) return;
    busy = true;
    const startedGeneration = generation;
    try {
      if (!catalogue) {
        catalogue = (await request("/api/analysis/methods")).methods;
        try {
          const stored = localStorage.getItem(storageKey);
          if (stored !== null) preferences = validateAnalysisPreferences(JSON.parse(stored), Object.keys(catalogue));
        } catch (error) { showError("analysis-error", `Could not restore analysis settings: ${error.message}; using defaults.`); }
        loadControls();
      }
      const batchState = status.experiment;
      const listKey = `${batchState.batch_id}:${batchState.active}:${batchState.outcome}`;
      if (batchesKey !== listKey) {
        const batches = (await request("/api/experiments")).batches;
        const dataset = byId("analysis-dataset"), compareDataset = byId("analysis-compare-dataset");
        const previous = dataset.value, previousCompare = compareDataset.value;
        dataset.replaceChildren(new Option("Current / latest batch", ""));
        compareDataset.replaceChildren(new Option("No comparison", ""));
        for (const batch of batches) dataset.append(new Option(
          `${batch.config.name} - ${batch.started_at.slice(0, 19)} - ${batch.outcome}`, batch.batch_id
        ));
        for (const batch of batches) compareDataset.append(new Option(
          `${batch.config.name} - ${batch.started_at.slice(0, 19)} - ${batch.outcome}`, batch.batch_id
        ));
        dataset.value = previous;
        compareDataset.value = previousCompare;
        batchesKey = listKey;
      }
      const batchId = byId("analysis-dataset").value || batchState.batch_id;
      if (!batchId) {
        byId("analysis-state").textContent = "Start a batch or select saved data";
        return;
      }
      const compareId = byId("analysis-compare-dataset").value;
      const compareActive = compareId && compareId !== batchId;
      setComparisonMode(compareActive);
      const [batch, compareBatch] = await Promise.all([
        request(`/api/experiments/${encodeURIComponent(batchId)}`),
        compareActive ? request(`/api/experiments/${encodeURIComponent(compareId)}`) : Promise.resolve(null),
      ]);
      if (generation !== startedGeneration) return;
      const scope = byId("analysis-scope"), priorScope = scope.value;
      scope.replaceChildren(new Option("All runs (all samples)", "batch"), new Option("Latest / current run", "current"));
      batch.runs.filter((run) => run.session).forEach((run) => scope.append(new Option(`${run.run_id} - ${run.state}`, run.run_id)));
      scope.value = [...scope.options].some((option) => option.value === priorScope) ? priorScope : "batch";
      renderExperimentConfig(batch);
      if (!compareActive) renderExcludeList(batch);
      else byId("analysis-exclude-list").replaceChildren();
      renderRunComparison(batch, compareBatch);
      const selectedRuns = batch.runs.filter((run) => run.session &&
        (scope.value === "batch" || scope.value === "current" || scope.value === run.run_id));
      const closedPrimary = scope.value === "current"
        ? selectedRuns.at(-1)?.state !== "recording"
        : selectedRuns.every((run) => run.state !== "recording");
      const closedSecondary = !compareBatch || compareBatch.runs
        .filter((run) => run.session)
        .every((run) => run.state !== "recording");
      byId("analysis-full").disabled = !selectedRuns.length || !closedPrimary || !closedSecondary;
      if (selection) {
        const jobs = await Promise.all(selection.jobs.map(async (entry) => ({
          entry, job: await request(`/api/analysis/jobs/${entry.jobId}`),
        })));
        if (generation !== startedGeneration) return;
        if (jobs.some(({entry, job}) => !resultMatches(entry, job))) return;
        if (jobs.some(({job}) => ["queued", "running"].includes(job.state))) {
          const running = jobs.find(({job}) => ["queued", "running"].includes(job.state))?.job.state || "running";
          byId("analysis-state").textContent = hasResults
            ? "Updating..." : `Analysis ${running}`;
          return;
        }
        const failed = jobs.find(({job}) => job.state === "failed");
        if (failed) throw new Error(failed.job.error);
        if (jobs.some(({job}) => job.state === "superseded")) { selection = null; dirty = true; }
        else {
          const primaryOutput = jobs.find(({entry}) => entry.role === "primary")?.job.output;
          const secondaryOutput = jobs.find(({entry}) => entry.role === "secondary")?.job.output;
          if (compareBatch && secondaryOutput) {
            renderComparison(batch, primaryOutput, compareBatch, secondaryOutput);
          } else {
            render(primaryOutput);
          }
          showError("analysis-error", "");
          byId("analysis-state").textContent = mode === "full" ? "Saved results" : "Updated";
          selection = null; lastComputed = Date.now();
        }
      }
      if (!dirty && (mode === "full" || (batch.outcome !== "running" && (!compareBatch || compareBatch.outcome !== "running")) ||
        Date.now() - lastComputed < 2000)) return;
      const scopeValue = scope.value;
      const runNumber = (id) => Number.parseInt(byId(id).value, 10) || null;
      const runFrom = runNumber("analysis-run-from"), runTo = runNumber("analysis-run-to");
      if (runFrom && runTo && runFrom > runTo) throw new Error("From run # must not exceed To run #");
      const recipe = {...preferences, batch_id: batchId, mode, run_from: runFrom, run_to: runTo,
        exclude_runs: [...excluded],
        scope: ["current", "batch"].includes(scopeValue) ? scopeValue : "run",
        run_id: ["current", "batch"].includes(scopeValue) ? null : scopeValue};
      if (compareActive) {
        recipe.scope = "batch";
        recipe.run_id = null;
        recipe.run_from = null;
        recipe.run_to = null;
        recipe.exclude_runs = [];
      }
      const jobs = [{role: "primary", recipe}];
      if (compareBatch) {
        jobs.push({role: "secondary", recipe: {...recipe, batch_id: compareBatch.batch_id}});
      }
      const submitted = await Promise.all(jobs.map(async (entry) => ({
        role: entry.role,
        batchId: entry.recipe.batch_id,
        job: await request("/api/analysis/jobs", {method: "POST", body: JSON.stringify(entry.recipe)}),
      })));
      if (generation !== startedGeneration) return;
      selection = {jobs: submitted.map((entry) => ({
        role: entry.role, batchId: entry.batchId,
        jobId: entry.job.job_id, recipeKey: entry.job.recipe_key,
      }))};
      dirty = false;
      byId("analysis-state").textContent = hasResults
        ? "Updating..." : "Analysis queued";
    } catch (error) {
      selection = null;
      showError("analysis-error", error.message);
      byId("analysis-state").textContent = hasResults
        ? "Update failed - previous results" : "Analysis unavailable";
    } finally {
      busy = false;
      if (selection) { clearTimeout(timer); timer = setTimeout(tick, 400); }
    }
  }
  byId("analysis-settings").addEventListener("change", changed);
  for (const id of ["analysis-dataset", "analysis-compare-dataset", "analysis-scope", "analysis-run-from", "analysis-run-to"]) {
    byId(id).addEventListener("change", () => { mode = "cumulative"; invalidate(); tick(); });
  }
  byId("analysis-full").addEventListener("click", () => { mode = "full"; invalidate(); tick(); });
  window.addEventListener("resize", () => plots.forEach(({canvas, plot}) => drawAnalysisChart(canvas, plot)));
  return {
    update(next) {
      status = next;
      const scope = byId("analysis-scope").value;
      const followsRuns = ["current", "batch"].includes(scope);
      const selectedPhase = followsRuns
        ? `${next.experiment.run_id}:${next.experiment.phase}`
        : scope === next.experiment.run_id && next.experiment.phase === "recording" ? "open" : "closed";
      const key = `${next.experiment.batch_id}:${selectedPhase}:${next.experiment.active}:${next.experiment.outcome}`;
      if ((byId("analysis-dataset").value === "" || byId("analysis-dataset").value === next.experiment.batch_id) &&
          key !== currentKey) {
        currentKey = key;
        if (followsRuns) mode = "cumulative";
        invalidate();
      }
      tick();
    },
  };
}

if (typeof module !== "undefined") module.exports = {
  validateAnalysisPreferences, resultMatches, chartBounds, formatAnalysisValue, analysisCardSummary,
  analysisSourceSummary, analysisLegendLabels, comparisonMetricRows, comparisonMetricTiles,
  overlaySeries, colorWithAlpha, comparisonDeltaClass, comparisonLegendLabels,
};
