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
    fs_hz: "Sample rate", segment: "Window", missing_samples: "Missing",
    resolution_hz: "Bin width", narrowband_pct: "Tone windows", tone_wander_hz: "Tone wander",
    tonal_share_pct: "Power in tone", peak_excess_db: "Tone vs floor", acf_lag1: "Lag-1 corr.", acf_lag2: "Lag-2 corr.",
  };
  const descriptions = {
    peak_excess_db: "Strongest line against the median noise floor; white noise stays below about 4 dB",
    tonal_share_pct: "Share of all noise power within two bins of the strongest line",
    narrowband_pct: "Share of windows holding a line 12 dB above their own median; white noise gives about 0%",
    tone_wander_hz: "Standard deviation of the strongest line's frequency across windows that hold a tone",
    flatness: "Geometric over arithmetic mean of the spectrum: 1 is perfectly white, near 0 is dominated by lines",
    acf_lag1: "Correlation between neighbouring samples; white noise stays within about +-2/sqrt(n)",
    acf_lag2: "Correlation two samples apart; a tone near a quarter of the sample rate makes this strongly negative",
    segment: "Window length used for every averaged spectrum; it follows the longest run",
    windows: "Number of overlapping windows averaged into the pooled spectrum",
    resolution_hz: "Frequency spacing of the spectrum bins",
    peak_hz: "Frequency of the strongest line in the pooled spectrum",
    fs_hz: "Sampling rate measured from the device timestamps, not the requested rate",
  };
  return {
    estimator,
    descriptions,
    metrics: metrics.filter((metric) => !headerKeys.includes(metric.key) && metric.key !== "missing_samples")
      .map((metric) => {
        const scope = metric.label?.match(/ \((?:full selection|window)\)$/)?.[0] || "";
        return {...metric, title: descriptions[metric.key] || metric.label, label: labels[metric.key] ? labels[metric.key] + scope : metric.label};
      }),
  };
}

const SPECTRUM_PLOT_NOTES = {
  "Power spectral density": "Noise power per Hz on log-log axes. The bold line is the pooled estimate with its 95% confidence band, thin grey lines are individual runs, and the dashed line is the median noise floor. White noise is flat; a tone is a peak.",
  "Spectrogram": "Spectrum over recorded time, runs laid end to end with dashed run boundaries. A wandering tone draws a moving ridge; white noise stays uniform.",
  "Autocorrelation": "Correlation between samples a few steps apart. White noise stays inside the dashed 95% limits.",
};

function analysisExplanation(node, summary, plots) {
  const details = node("details", null, "analysis-method-details");
  details.append(node("summary", "What these statistics mean"));
  const list = node("dl", null, "analysis-explain");
  for (const metric of summary.metrics) {
    list.append(node("dt", metric.label), node("dd", summary.descriptions[metric.key] || metric.title));
  }
  for (const plot of plots) {
    if (SPECTRUM_PLOT_NOTES[plot.title]) list.append(node("dt", plot.title), node("dd", SPECTRUM_PLOT_NOTES[plot.title]));
  }
  details.append(list);
  return details;
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
  const shown = series.filter((curve) => curve.legend !== false);
  if (shown.length < 2) return [];
  return shown.map((curve) =>
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

function interpolateAt(xs, ys, x) {
  const last = xs.length - 1;
  if (x <= xs[0]) return ys[0];
  if (x >= xs[last]) return ys[last];
  let low = 0, high = last;
  while (high - low > 1) {
    const mid = (low + high) >> 1;
    if (xs[mid] <= x) low = mid; else high = mid;
  }
  return ys[low] + (ys[high] - ys[low]) * (x - xs[low]) / (xs[high] - xs[low]);
}

const SPECTRUM_COMPARE_BANDS = [[0, 1], [1, 3], [3, 6], [6, 10], [10, Infinity]];
const SPECTRUM_DOF_PER_WINDOW = 1.89;  // matches the confidence band computed by the backend

// Overlay, B-over-A ratio and band-power table for two pooled spectra. Returns null when either lacks a usable spectrum.
function spectrumComparison(left, right, labels = ["A", "B"]) {
  const take = (result) => {
    const series = result?.plots?.[0]?.series || [];
    const pooled = series[0], band = series.find((curve) => curve.style === "band");
    const floor = series.find((curve) => curve.label?.startsWith("Noise floor"));
    if (!pooled || pooled.x.length < 4) return null;
    const keep = pooled.x.map((value, index) => index).filter((index) => pooled.x[index] > 0);
    const pick = (curve) => curve && {...curve, x: keep.map((index) => curve.x[index]), y: keep.map((index) => curve.y[index])};
    return {pooled: pick(pooled), band, floor, windows: result.metrics.find((metric) => metric.key === "windows")?.value};
  };
  const a = take(left), b = take(right);
  if (!a || !b) return null;
  const low = Math.max(a.pooled.x[0], b.pooled.x[0]), high = Math.min(a.pooled.x.at(-1), b.pooled.x.at(-1));
  const within = (curve) => curve.x.filter((value) => value >= low && value <= high);
  const gridA = within(a.pooled), gridB = within(b.pooled);
  const grid = gridA.length <= gridB.length ? gridA : gridB;
  if (grid.length < 3) return null;
  const valuesA = grid.map((f) => interpolateAt(a.pooled.x, a.pooled.y, f));
  const valuesB = grid.map((f) => interpolateAt(b.pooled.x, b.pooled.y, f));
  const ratio = valuesA.map((value, index) => value > 0 && valuesB[index] > 0 ? 10 * Math.log10(valuesB[index] / value) : 0);
  const sigma = a.windows > 0 && b.windows > 0
    ? (10 / Math.LN10) * Math.sqrt(2 / (SPECTRUM_DOF_PER_WINDOW * a.windows) + 2 / (SPECTRUM_DOF_PER_WINDOW * b.windows))
    : null;
  const limit = sigma === null ? null : 1.96 * sigma;
  const colors = ["#4fb3d1", "#ff8a5c"];
  const styled = (side, index) => {
    const name = labels[index];
    const out = [{...side.pooled, label: `${name}: pooled`, color: colors[index], width: 2.2}];
    if (side.band) out.unshift({...side.band, label: `${name}: 95% confidence`, color: colors[index], alpha: 0.22, legend: false});
    if (side.floor) out.push({...side.floor, label: `${name}: noise floor`, color: colors[index], alpha: 0.9, dashed: true, legend: false});
    return out;
  };
  const overlay = {
    title: "Spectrum overlay", x_label: "Frequency (Hz)", y_label: "PSD (m^2/Hz)", x_scale: "log", y_scale: "log",
    series: [...styled(a, 0), ...styled(b, 1)], annotations: [],
  };
  const significant = limit === null ? [] : grid.map((f, index) => index).filter((index) => Math.abs(ratio[index]) > limit);
  const ratioSeries = [];
  if (limit !== null) {
    ratioSeries.push({label: "No significant difference", x: grid, y: grid.map(() => -limit), y2: grid.map(() => limit),
      style: "band", color: "#8a9893", alpha: 0.3, legend: false});
  }
  ratioSeries.push({label: "0 dB", x: [grid[0], grid.at(-1)], y: [0, 0], color: "#8a9893", dashed: true, legend: false});
  ratioSeries.push({label: `${labels[1]} vs ${labels[0]}`, x: grid, y: ratio, color: "#e0a73a", width: 1.8});
  if (significant.length) {
    ratioSeries.push({label: "Significant", x: significant.map((index) => grid[index]), y: significant.map((index) => ratio[index]),
      style: "points", color: "#ff6b72"});
  }
  const ratioPlot = {
    title: `Where ${labels[1]} differs from ${labels[0]}`, x_label: "Frequency (Hz)", y_label: `${labels[1]} / ${labels[0]} (dB)`,
    x_scale: "log", series: ratioSeries, annotations: [],
  };
  const step = (grid.at(-1) - grid[0]) / (grid.length - 1);
  const power = (values, from, to) => values.reduce((sum, value, index) => sum + (grid[index] >= from && grid[index] < to ? value * step : 0), 0) * 1e6;
  const edges = [...SPECTRUM_COMPARE_BANDS, [0, Infinity]];
  const rows = edges.map(([from, to], index) => {
    const total = index === edges.length - 1;
    if (!total && !grid.some((f) => f >= from && f < to)) return null;
    const powerA = power(valuesA, from, to), powerB = power(valuesB, from, to);
    const name = total ? "Total" : to === Infinity ? `> ${from} Hz` : from === 0 ? `< ${to} Hz` : `${from}-${to} Hz`;
    return [name, powerA, powerB, powerA > 0 && powerB > 0 ? 10 * Math.log10(powerB / powerA) : null];
  }).filter(Boolean);
  const total = rows.at(-1);
  let worst = 0;
  ratio.forEach((value, index) => { if (Math.abs(value) > Math.abs(ratio[worst])) worst = index; });
  const share = limit === null ? null : significant.length / grid.length * 100;
  const sign = (value) => `${value >= 0 ? "+" : ""}${value.toFixed(1)}`;
  const summary = `${labels[1]} carries ${sign(total[3] ?? 0)} dB total power vs ${labels[0]} (${grid[0].toFixed(2)}-${grid.at(-1).toFixed(1)} Hz). ` +
    (share === null ? "" : `${share.toFixed(0)}% of frequency bins differ beyond the 95% noise band. `) +
    `Largest gap ${sign(ratio[worst])} dB at ${grid[worst].toFixed(2)} Hz.`;
  return {
    overlay, ratio: ratioPlot, summary, significant_pct: share, limit_db: limit,
    bands: {columns: ["Band", `${labels[0]} (mm²)`, `${labels[1]} (mm²)`, `${labels[1]} vs ${labels[0]} (dB)`], rows},
  };
}

const COMPARE_COLORS = ["#4fb3d1", "#ff8a5c"];

// Both spectrograms share one colour scale so equal colours mean equal dB over each batch's own median level.
function spectrogramComparison(left, right, labels = ["A", "B"]) {
  const maps = [left, right].map((result) => result?.plots?.find((plot) => plot.heatmap));
  if (!maps[0] || !maps[1]) return null;
  const min = Math.min(maps[0].heatmap.z_min, maps[1].heatmap.z_min);
  const max = Math.max(maps[0].heatmap.z_max, maps[1].heatmap.z_max);
  return maps.map((plot, index) => ({
    ...plot, title: `${labels[index]}: ${plot.title}`, heatmap: {...plot.heatmap, z_min: min, z_max: max},
  }));
}

// Two autocorrelation curves compared lag by lag. The difference of two independent estimates has
// standard deviation sqrt(1/nA + 1/nB); each white-noise limit 1.96/sqrt(n) gives that n.
function acfComparison(left, right, labels = ["A", "B"]) {
  const take = (result) => {
    const plot = result?.plots?.find((item) => item.title === "Autocorrelation");
    const curve = plot?.series.find((item) => item.label === "Autocorrelation");
    const limit = plot?.series.find((item) => item.label?.includes("white-noise limit"))?.y[0];
    return curve && curve.x.length ? {lags: curve.x, values: curve.y, limit} : null;
  };
  const a = take(left), b = take(right);
  if (!a || !b) return null;
  const count = Math.min(a.lags.length, b.lags.length);
  const lags = a.lags.slice(0, count), sides = [a, b];
  const series = [];
  sides.forEach((side, index) => {
    const color = COMPARE_COLORS[index], name = labels[index];
    series.push({label: name, x: lags, y: side.values.slice(0, count), color, width: 1.8});
    series.push({label: `${name}: lags`, x: lags, y: side.values.slice(0, count), style: "points", color, legend: false});
    if (side.limit > 0) {
      series.push({label: `${name}: 95% white-noise limit`, x: lags, y: lags.map(() => side.limit), color, alpha: 0.7, dashed: true, legend: false});
      series.push({label: `${name}: lower limit`, x: lags, y: lags.map(() => -side.limit), color, alpha: 0.7, dashed: true, legend: false});
    }
  });
  const plot = {title: "Autocorrelation", x_label: "Lag (samples)", y_label: "Autocorrelation", series, annotations: []};
  const sigma = a.limit > 0 && b.limit > 0 ? Math.sqrt((a.limit / 1.96) ** 2 + (b.limit / 1.96) ** 2) : null;
  const difference = lags.map((lag, index) => b.values[index] - a.values[index]);
  const differing = sigma === null ? [] : difference.map((value, index) => index).filter((index) => Math.abs(difference[index]) > 1.96 * sigma);
  const sign = (value) => `${value >= 0 ? "+" : ""}${value.toFixed(3)}`;
  let worst = 0;
  difference.forEach((value, index) => { if (Math.abs(value) > Math.abs(difference[worst])) worst = index; });
  const outside = (side) => side.values.slice(0, count).filter((value) => side.limit > 0 && Math.abs(value) > side.limit).length;
  const summary = `Lag 1: ${labels[0]} ${sign(a.values[0])}, ${labels[1]} ${sign(b.values[0])}. ` +
    `${labels[0]} has ${outside(a)} and ${labels[1]} ${outside(b)} of ${count} lags outside the white-noise limit. ` +
    (sigma === null ? "" : `${differing.length} of ${count} lags differ beyond the 95% noise band; `) +
    `largest gap ${sign(difference[worst])} at lag ${lags[worst]}.`;
  const rows = lags.slice(0, 5).map((lag, index) => [
    lag, a.values[index], b.values[index], difference[index],
    sigma === null ? null : (Math.abs(difference[index]) > 1.96 * sigma ? "yes" : "no"),
  ]);
  return {
    plot, summary, differing: differing.length, limit: sigma === null ? null : 1.96 * sigma,
    table: {columns: ["Lag", labels[0], labels[1], `${labels[1]} - ${labels[0]}`, "Beyond noise band"], rows},
  };
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

function axisTransform(scale) {
  return scale === "log" ? Math.log10 : (value) => value;
}

function plotBounds(plot) {
  const logX = plot.x_scale === "log", logY = plot.y_scale === "log";
  const toX = axisTransform(plot.x_scale), toY = axisTransform(plot.y_scale);
  const xs = [], ys = logY ? [] : [0];
  for (const curve of plot.series) {
    if (curve.x.length !== curve.y.length || (curve.y2 && curve.y2.length !== curve.y.length)) {
      throw new Error("Mismatched analysis coordinates");
    }
    for (let index = 0; index < curve.x.length; index++) {
      for (const y of curve.y2 ? [curve.y[index], curve.y2[index]] : [curve.y[index]]) {
        if (!Number.isFinite(curve.x[index]) || !Number.isFinite(y)) throw new Error("Non-finite analysis coordinates");
        if ((logX && curve.x[index] <= 0) || (logY && y <= 0)) continue;
        xs.push(toX(curve.x[index])); ys.push(toY(y));
      }
    }
  }
  if (!xs.length) throw new Error("No plot coordinates");
  const extent = (values, log) => {
    let min = values[0], max = values[0];
    for (const value of values) { min = Math.min(min, value); max = Math.max(max, value); }
    const padding = max > min ? (max - min) * (log ? 0.04 : 0.06) : log ? 0.5 : Math.max(Math.abs(min) * 0.05, 0.001);
    return {min: min - padding, max: max + padding};
  };
  return {x: extent(xs, logX), y: extent(ys, logY)};
}

// Tick positions in log10 units: decades, plus 2 and 5 when the range is narrow enough to stay readable.
function logTicks(min, max, maxSpan) {
  const mantissas = max - min > maxSpan ? [1] : [1, 2, 5];
  const ticks = [];
  for (let exponent = Math.floor(min); exponent <= Math.ceil(max); exponent++) {
    for (const mantissa of mantissas) {
      const tick = exponent + Math.log10(mantissa);
      if (tick >= min - 1e-9 && tick <= max + 1e-9) ticks.push(tick);
    }
  }
  return ticks;
}

// Round linear ticks (1, 2 or 5 times a power of ten) so zero and simple values land on gridlines.
function niceTicks(min, max, target = 5) {
  const rough = (max - min) / Math.max(1, target - 1);
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((factor) => factor * magnitude).find((value) => value >= rough);
  const ticks = [];
  for (let tick = Math.ceil(min / step - 1e-9) * step; tick <= max + step * 1e-9; tick += step) {
    ticks.push(Math.abs(tick) < step * 1e-9 ? 0 : Number(tick.toPrecision(12)));
  }
  return ticks;
}

function formatLogTick(tick) {
  const value = 10 ** tick;
  return value >= 0.01 && value < 10000 ? String(Number(value.toPrecision(2))) : value.toExponential(0).replace("e+", "e");
}

// Dark-to-bright perceptual ramp for the spectrogram; t is clamped to [0, 1].
function heatColor(t) {
  const stops = [[68, 1, 84], [59, 82, 139], [33, 144, 141], [93, 200, 99], [253, 231, 37]];
  const scaled = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0)) * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(scaled)), fraction = scaled - index;
  return stops[index].map((channel, slot) => Math.round(channel + (stops[index + 1][slot] - channel) * fraction));
}

function chartTheme() {
  if (document.documentElement.dataset.theme === "light") {
    return {
      text: "#52615e", grid: "#dfe5e2", zero: "#8a9893", axis: "#5a6a65", panel: "#fff",
      series: {raw_m: "#536878", corrected_m: "#176b87", expected_m: "#966000", residual_m: "#b63036", rolling_bias_m: "#7851a9", rolling_std_m: "#087f5b", rate_hz: "#0a8f5a", requested_hz: "#6b7a76"},
      curves: ["#176b87", "#087f5b", "#966000", "#b63036", "#7851a9", "#536878", "#b35c10"],
    };
  }
  return {
    text: "#9aaaa5", grid: "#2a3532", zero: "#6f7e79", axis: "#4c5853", panel: "#18201e", series: {},
    curves: ["#4fb3d1", "#3ecf9f", "#e0a73a", "#ff6b72", "#b08fe0", "#8fa7b8", "#d47729"],
  };
}

function prepareCanvas(canvas, plot) {
  const width = canvas.clientWidth || 400, height = canvas.clientHeight || 220, ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio; canvas.height = height * ratio;
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  canvas._plot = plot;
  if (!canvas._hoverBound) {
    canvas._hoverBound = true;
    const position = (event) => {
      const rect = canvas.getBoundingClientRect();
      return {x: event.clientX - rect.left, y: event.clientY - rect.top};
    };
    canvas.addEventListener("mousemove", (event) => drawAnalysisChart(canvas, canvas._plot, position(event)));
    canvas.addEventListener("mouseleave", () => drawAnalysisChart(canvas, canvas._plot));
  }
  return {context, width, height};
}

// Left and bottom axis lines only; no closed frame around the plot area.
function drawAxes(context, theme, area) {
  context.strokeStyle = theme.axis; context.lineWidth = 1;
  context.beginPath();
  context.moveTo(area.left, area.top); context.lineTo(area.left, area.bottom); context.lineTo(area.right, area.bottom);
  context.stroke();
}

function drawReadout(context, theme, area, lines, anchorX) {
  context.font = "10px system-ui";
  const boxWidth = Math.max(...lines.map((line) => context.measureText(line).width)) + 12;
  const boxHeight = lines.length * 13 + 8;
  const left = anchorX + 10 + boxWidth > area.right ? anchorX - 10 - boxWidth : anchorX + 10;
  context.fillStyle = theme.panel; context.strokeStyle = theme.zero; context.lineWidth = 1;
  context.fillRect(left, area.top + 2, boxWidth, boxHeight); context.strokeRect(left, area.top + 2, boxWidth, boxHeight);
  context.fillStyle = theme.text; context.textAlign = "left";
  lines.forEach((line, index) => context.fillText(line, left + 6, area.top + 15 + index * 13));
}

function formatHeatAxis(value) {
  const magnitude = Math.abs(value);
  return String(Number(value.toFixed(magnitude >= 100 ? 0 : magnitude >= 10 ? 1 : 2)));
}

function drawHeatmapChart(canvas, plot, hover) {
  const theme = chartTheme(), map = plot.heatmap;
  const {context, width, height} = prepareCanvas(canvas, plot);
  const area = {left: 52, right: width - 66, top: 12, bottom: height - 42};
  const edges = (values) => {
    if (values.length < 2) return [values[0] - 0.5, values[0] + 0.5];
    return [values[0] - (values[1] - values[0]) / 2, values.at(-1) + (values.at(-1) - values.at(-2)) / 2];
  };
  const [x0, x1] = edges(map.x), [y0, y1] = edges(map.y);
  const px = (value) => area.left + (value - x0) / (x1 - x0) * (area.right - area.left);
  const py = (value) => area.bottom - (value - y0) / (y1 - y0) * (area.bottom - area.top);
  const span = map.z_max - map.z_min || 1;
  if (canvas._heatSource !== map) {
    const image = document.createElement("canvas");
    image.width = map.x.length; image.height = map.y.length;
    const imageContext = image.getContext("2d"), pixels = imageContext.createImageData(image.width, image.height);
    map.z.forEach((row, rowIndex) => row.forEach((value, column) => {
      const offset = (((map.y.length - 1 - rowIndex) * map.x.length) + column) * 4;
      pixels.data.set([...heatColor((value - map.z_min) / span), 255], offset);
    }));
    imageContext.putImageData(pixels, 0, 0);
    canvas._heatSource = map; canvas._heatImage = image;
  }
  context.fillStyle = theme.panel; context.fillRect(0, 0, width, height);
  context.imageSmoothingEnabled = false;
  context.drawImage(canvas._heatImage, area.left, area.top, area.right - area.left, area.bottom - area.top);
  context.font = "10px system-ui"; context.lineWidth = 1;
  context.setLineDash([3, 3]); context.strokeStyle = "rgba(255,255,255,0.55)";
  for (const marker of map.markers) {
    context.beginPath(); context.moveTo(px(marker), area.top); context.lineTo(px(marker), area.bottom); context.stroke();
  }
  context.setLineDash([]);
  drawAxes(context, theme, area);
  context.fillStyle = theme.text;
  for (let tick = 0; tick <= 4; tick++) {
    const xv = x0 + tick * (x1 - x0) / 4, yv = y0 + tick * (y1 - y0) / 4;
    context.textAlign = "center"; context.fillText(formatHeatAxis(xv), px(xv), area.bottom + 15);
    context.textAlign = "right"; context.fillText(formatHeatAxis(yv), area.left - 5, py(yv) + 3);
  }
  const barLeft = width - 52, barWidth = 10, barHeight = area.bottom - area.top;
  for (let step = 0; step < barHeight; step++) {
    context.fillStyle = `rgb(${heatColor(1 - step / barHeight).join(",")})`;
    context.fillRect(barLeft, area.top + step, barWidth, 1);
  }
  context.fillStyle = theme.text; context.textAlign = "left";
  for (const fraction of [0, 0.5, 1]) {
    const value = map.z_min + fraction * span;
    context.fillText(`${value >= 0 ? "+" : ""}${value.toFixed(0)} dB`, barLeft + barWidth + 3, area.bottom - fraction * barHeight + (fraction ? 3 : 0));
  }
  context.textAlign = "center";
  context.fillText(plot.x_label, (area.left + area.right) / 2, height - 6);
  context.save(); context.translate(11, (area.top + area.bottom) / 2); context.rotate(-Math.PI / 2);
  context.fillText(plot.y_label, 0, 0); context.restore();
  if (hover && hover.x >= area.left && hover.x <= area.right && hover.y >= area.top && hover.y <= area.bottom) {
    const column = Math.max(0, Math.min(map.x.length - 1, Math.round((hover.x - area.left) / (area.right - area.left) * map.x.length - 0.5)));
    const row = Math.max(0, Math.min(map.y.length - 1, Math.round((area.bottom - hover.y) / (area.bottom - area.top) * map.y.length - 0.5)));
    const value = map.z[row][column];
    context.strokeStyle = "rgba(255,255,255,0.8)";
    context.strokeRect(px(map.x[column] - (x1 - x0) / map.x.length / 2), py(map.y[row] + (y1 - y0) / map.y.length / 2),
      (area.right - area.left) / map.x.length, (area.bottom - area.top) / map.y.length);
    drawReadout(context, theme, area, [
      `t ${formatHeatAxis(map.x[column])} s`, `f ${formatHeatAxis(map.y[row])} Hz`,
      `${value >= 0 ? "+" : ""}${value.toFixed(1)} dB`,
    ], hover.x);
  }
}

function drawAnalysisChart(canvas, plot, hover = null) {
  if (plot.heatmap) { drawHeatmapChart(canvas, plot, hover); return; }
  const theme = chartTheme();
  const bounds = plotBounds(plot);
  const {context, width, height} = prepareCanvas(canvas, plot);
  const logX = plot.x_scale === "log", logY = plot.y_scale === "log";
  const toX = axisTransform(plot.x_scale), toY = axisTransform(plot.y_scale);
  const area = {left: 66, right: width - 12, top: 12, bottom: height - 42};
  const x = (value) => area.left + (toX(value) - bounds.x.min) / (bounds.x.max - bounds.x.min) * (area.right - area.left);
  const y = (value) => area.bottom - (toY(value) - bounds.y.min) / (bounds.y.max - bounds.y.min) * (area.bottom - area.top);
  const visible = (px, py) => (!logX || px > 0) && (!logY || py > 0);
  const xUnit = plot.x_label.match(/\(([^)]+)\)$/)?.[1] || "";
  const yUnit = plot.y_label.match(/\(([^)]+)\)$/)?.[1] || "";
  context.fillStyle = theme.panel; context.fillRect(0, 0, width, height);
  context.font = "10px system-ui"; context.lineWidth = 1;
  const ticks = (axis, log, maxSpan) => log
    ? logTicks(axis.min, axis.max, maxSpan)
    : niceTicks(axis.min, axis.max);
  const gridX = (tick) => area.left + (tick - bounds.x.min) / (bounds.x.max - bounds.x.min) * (area.right - area.left);
  const gridY = (tick) => area.bottom - (tick - bounds.y.min) / (bounds.y.max - bounds.y.min) * (area.bottom - area.top);
  for (const tick of ticks(bounds.y, logY, 2)) {
    context.strokeStyle = theme.grid; context.beginPath(); context.moveTo(area.left, gridY(tick)); context.lineTo(area.right, gridY(tick)); context.stroke();
    context.fillStyle = theme.text; context.textAlign = "right";
    context.fillText(logY ? formatLogTick(tick) : formatAnalysisValue(tick, yUnit), area.left - 5, gridY(tick) + 3);
  }
  for (const tick of ticks(bounds.x, logX, 3)) {
    if (logX) {
      context.strokeStyle = theme.grid; context.beginPath(); context.moveTo(gridX(tick), area.top); context.lineTo(gridX(tick), area.bottom); context.stroke();
    }
    context.fillStyle = theme.text; context.textAlign = "center";
    context.fillText(logX ? formatLogTick(tick) : formatAnalysisValue(tick, xUnit), gridX(tick), area.bottom + 15);
  }
  const colors = theme.curves;
  const order = [...plot.series.keys()].sort((a, b) => (plot.series[b].style === "band") - (plot.series[a].style === "band"));
  for (const index of order) {
    const curve = plot.series[index];
    const color = curve.color || colors[index % colors.length];
    context.strokeStyle = context.fillStyle = colorWithAlpha(color, curve.alpha ?? 1);
    context.lineWidth = curve.width ?? (curve.style === "line" && !curve.dashed && curve.alpha == null ? 1.6 : 1.2);
    context.setLineDash(curve.dashed ? [5, 4] : []);
    if (curve.style === "band") {
      const points = curve.x.map((value, point) => point).filter((point) => visible(curve.x[point], curve.y[point]) && visible(curve.x[point], curve.y2[point]));
      if (points.length > 1) {
        context.beginPath();
        points.forEach((point, step) => step ? context.lineTo(x(curve.x[point]), y(curve.y2[point])) : context.moveTo(x(curve.x[point]), y(curve.y2[point])));
        [...points].reverse().forEach((point) => context.lineTo(x(curve.x[point]), y(curve.y[point])));
        context.closePath(); context.fill();
      }
      continue;
    }
    context.beginPath();
    const barWidth = curve.x.length > 1 ? Math.max(1, Math.abs(x(curve.x[1]) - x(curve.x[0])) * 0.85) : 10;
    let pen = false;
    curve.x.forEach((value, point) => {
      if (!visible(value, curve.y[point])) { pen = false; return; }
      const px = x(value), py = y(curve.y[point]);
      if (curve.style === "bars") context.fillRect(px - barWidth / 2, Math.min(py, y(0)), barWidth, Math.max(1, Math.abs(y(0) - py)));
      else if (curve.style === "stems") { context.moveTo(px, y(0)); context.lineTo(px, py); }
      else if (curve.style === "points") { context.moveTo(px + 2, py); context.arc(px, py, 2, 0, 2 * Math.PI); }
      else if (!pen) { context.moveTo(px, py); pen = true; }
      else context.lineTo(px, py);
    });
    context.stroke();
  }
  context.setLineDash([]);
  drawAxes(context, theme, area);
  for (const note of plot.annotations || []) {
    if (!visible(note.x, note.y)) continue;
    const px = x(note.x), py = y(note.y);
    context.strokeStyle = "#ff6b72"; context.fillStyle = "#ff6b72"; context.lineWidth = 1.5;
    context.beginPath(); context.arc(px, py, 4, 0, 2 * Math.PI); context.stroke();
    context.font = "11px system-ui"; context.textAlign = px > area.right - 130 ? "right" : "left";
    context.fillText(note.text, px + (context.textAlign === "left" ? 8 : -8), Math.max(area.top + 11, py - 8));
  }
  context.font = "10px system-ui"; context.fillStyle = theme.text; context.textAlign = "center";
  context.fillText(plot.x_label, (area.left + area.right) / 2, height - 6);
  context.save(); context.translate(12, (area.top + area.bottom) / 2); context.rotate(-Math.PI / 2);
  context.fillText(plot.y_label, 0, 0); context.restore();
  if (hover && hover.x >= area.left && hover.x <= area.right) {
    const inverse = (hover.x - area.left) / (area.right - area.left) * (bounds.x.max - bounds.x.min) + bounds.x.min;
    const target = logX ? 10 ** inverse : inverse;
    const lines = [];
    let anchor = null;
    for (const curve of plot.series) {
      if (curve.style === "band" || curve.legend === false || !curve.x.length) continue;
      let best = 0;
      curve.x.forEach((value, point) => { if (Math.abs(value - target) < Math.abs(curve.x[best] - target)) best = point; });
      anchor = anchor ?? curve.x[best];
      const value = curve.y[best];
      lines.push(`${curve.label}: ${logY ? value.toExponential(2) : formatAnalysisValue(value, yUnit)}`);
    }
    if (anchor !== null) {
      context.strokeStyle = theme.zero; context.setLineDash([2, 3]);
      context.beginPath(); context.moveTo(x(anchor), area.top); context.lineTo(x(anchor), area.bottom); context.stroke();
      context.setLineDash([]);
      drawReadout(context, theme, area, [`${plot.x_label.replace(/ \(.*\)$/, "")} ${formatAnalysisValue(anchor, xUnit)} ${xUnit}`, ...lines], x(anchor));
    }
  }
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
  const plotOpen = new Map();
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
    const psdLeft = primaryOutput.results.find((item) => item.method === "psd")?.result;
    const psdRight = secondaryOutput.results.find((item) => item.method === "psd")?.result;
    const spectra = psdLeft?.status === "ok" && psdRight?.status === "ok" ? spectrumComparison(psdLeft, psdRight) : null;
    if (spectra) {
      const card = node("article", null, "analysis-card");
      card.dataset.method = "psd";
      const header = node("header", null, "analysis-card-header");
      const title = node("div", null, "analysis-card-title");
      title.append(node("h3", "Noise spectrum comparison"));
      header.append(title); card.append(header);
      card.append(node("p", spectra.summary, "plot-note"));
      const tiles = node("dl", null, "analysis-metrics");
      const spectrumMetrics = (result) => analysisCardSummary("psd", result.metrics).metrics;
      comparisonMetricTiles(spectrumMetrics(psdLeft), spectrumMetrics(psdRight)).forEach(({key, label, left, right, delta, trend}) => {
        const tile = node("div", null, `metric-tile metric-tile--comparison metric-tile--${trend}`);
        tile.dataset.key = key;
        tile.append(node("dt", label));
        const value = node("dd", null, "metric-value");
        value.append(node("strong", right));
        tile.append(value, node("p", `A ${left} → B ${right} (Δ ${delta})`, "plot-note"));
        tiles.append(tile);
      });
      card.append(tiles);
      card.append(node("p", `A = ${primaryLabel}; B = ${secondaryLabel}`, "plot-note"));
      for (const plot of [spectra.ratio, spectra.overlay]) {
        const canvas = node("canvas", null, "plot-tall");
        canvas.setAttribute("aria-label", `${plot.title}: ${plot.x_label}; ${plot.y_label}`);
        const key = `compare:${plot.title}`, fold = node("details", null, "analysis-method-details analysis-plot");
        fold.open = plotOpen.has(key) ? plotOpen.get(key) : true;
        fold.addEventListener("toggle", () => {
          plotOpen.set(key, fold.open);
          if (fold.open) drawAnalysisChart(canvas, plot);
        });
        fold.append(node("summary", plot.title), canvas);
        card.append(fold);
        nextPlots.push({canvas, plot});
      }
      card.append(node("p", "Power by frequency band", "plot-title"), table(spectra.bands.columns, spectra.bands.rows));
      const note = spectra.limit_db === null ? "" : `The grey band is the 95% range of ±${spectra.limit_db.toFixed(2)} dB expected from estimation noise alone; red points fall outside it.`;
      if (note) card.append(node("p", note, "plot-note"));
      const addFold = (title, content, canvases) => {
        const key = `compare:${title}`, fold = node("details", null, "analysis-method-details analysis-plot");
        fold.open = plotOpen.has(key) ? plotOpen.get(key) : false;
        fold.addEventListener("toggle", () => {
          plotOpen.set(key, fold.open);
          if (fold.open) canvases.forEach(({canvas, plot}) => drawAnalysisChart(canvas, plot));
        });
        fold.append(node("summary", title), ...content);
        card.append(fold);
        nextPlots.push(...canvases);
      };
      const spectrograms = spectrogramComparison(psdLeft, psdRight);
      if (spectrograms) {
        const canvases = spectrograms.map((plot) => {
          const canvas = node("canvas", null, "plot-tall");
          canvas.setAttribute("aria-label", `${plot.title}: ${plot.x_label}; ${plot.y_label}`);
          return {canvas, plot};
        });
        const content = [];
        canvases.forEach(({canvas, plot}, index) => content.push(node("p", `${index ? "B" : "A"} = ${index ? secondaryLabel : primaryLabel}`, "plot-note"), canvas));
        content.push(node("p", "Both use one colour scale; each is in dB over its own median level, so this compares structure over time, not absolute power.", "plot-note"));
        addFold("Spectrogram comparison", content, canvases);
      }
      const acf = acfComparison(psdLeft, psdRight);
      if (acf) {
        const canvas = node("canvas", null, "plot-tall");
        canvas.setAttribute("aria-label", `${acf.plot.title}: ${acf.plot.x_label}; ${acf.plot.y_label}`);
        const content = [node("p", acf.summary, "plot-note"), canvas, table(acf.table.columns, acf.table.rows)];
        if (acf.limit !== null) {
          content.push(node("p", `Dashed lines are each batch's own 95% white-noise limit. A and B differ at a lag only if they are more than ${acf.limit.toFixed(3)} apart.`, "plot-note"));
        }
        addFold("Autocorrelation comparison", content, [{canvas, plot: acf.plot}]);
      }
      cardsList.push(card);
    }
    if (!cardsList.length) {
      cardsList.push(node("p", "Comparison needs statistics, distribution or spectrum results for the selected batches.", "plot-note"));
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
      const titles = {statistics: "Residuals", distribution: "Distribution fit", psd: "Noise spectrum (all runs)"};
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
      const comparisons = [], afterPlots = [];
      for (const value of result.tables) {
        if (value.title === "Compare models") comparisons.push(value);
        // Spectrum tables support the charts, so they follow them.
        else if (item.method === "psd") afterPlots.push(value);
        else card.append(table(value.columns, value.rows));
      }
      for (const plot of result.plots) {
        const canvas = node("canvas");
        if (item.method === "psd") canvas.className = "plot-tall";
        canvas.setAttribute("aria-label", `${item.label}: ${plot.x_label}; ${plot.y_label}`);
        const labels = analysisLegendLabels(plot.series);
        let legend = null;
        if (labels.length) {
          legend = node("p", labels.join(" / "), "plot-note");
          legend.title = plot.series.filter((curve) => curve.legend !== false).map((curve) => curve.label).join(" / ");
        }
        if (item.method === "psd" && plot.title) {
          // Each spectrum chart sits in its own roll-down; hidden canvases have no size, so redraw on open.
          const key = plot.title, fold = node("details", null, "analysis-method-details analysis-plot");
          fold.open = plotOpen.has(key) ? plotOpen.get(key) : key === "Power spectral density";
          fold.addEventListener("toggle", () => {
            plotOpen.set(key, fold.open);
            if (fold.open) drawAnalysisChart(canvas, plot);
          });
          fold.append(node("summary", plot.title), canvas);
          if (legend) fold.append(legend);
          card.append(fold);
        } else {
          if (plot.title) card.append(node("p", plot.title, "plot-title"));
          card.append(canvas);
          if (legend) card.append(legend);
        }
        cardPlots.push({canvas, plot});
      }
      for (const value of afterPlots) {
        card.append(node("p", value.title, "plot-title"), table(value.columns, value.rows));
      }
      if (item.method === "psd") card.append(analysisExplanation(node, summary, result.plots));
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
  plotBounds, logTicks, niceTicks, heatColor, drawAnalysisChart, analysisSourceSummary, analysisLegendLabels, comparisonMetricRows, comparisonMetricTiles,
  overlaySeries, spectrumComparison, spectrogramComparison, acfComparison, colorWithAlpha, comparisonDeltaClass, comparisonLegendLabels,
};
