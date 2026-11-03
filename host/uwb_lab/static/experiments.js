function experimentPayload(fields) {
  const numeric = (name, integer, minimum, inclusive = true) => {
    const raw = fields[name];
    const value = Number(raw);
    if (raw === "" || raw == null || !Number.isFinite(value) ||
        (integer && !Number.isInteger(value)) || (inclusive ? value < minimum : value <= minimum)) {
      throw new Error(`Invalid ${name.replaceAll("_", " ")}`);
    }
    return value;
  };
  return {
    name: String(fields.name || "experiment"), notes: String(fields.notes || ""),
    duration_s: numeric("duration_s", false, 0, false),
    run_count: numeric("run_count", true, 1),
    interval_s: numeric("interval_s", false, 0),
    start_delay_s: numeric("start_delay_s", false, 0),
    manual_start: fields.manual_start === true || fields.manual_start === "on",
  };
}

function experimentTotalRemaining(state) {
  const run = state.duration_s + state.start_delay_s;
  const left = state.run_count - state.run_index;
  const current = state.remaining_s;
  if (state.phase === "recording") return current + left * run + left * state.interval_s;
  if (state.phase === "interval") return current + left * run + Math.max(0, left - 1) * state.interval_s;
  if (state.phase === "delay") return current + state.duration_s + left * (run + state.interval_s);
  return run + left * (run + state.interval_s);
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  return minutes ? `${minutes} min ${total % 60} s` : `${total} s`;
}

function formatProgress(elapsed, total) {
  const done = Math.min(Math.round(elapsed), Math.round(total));
  return Math.round(total) < 60 ? `${done} / ${formatDuration(total)}` : `${formatDuration(done)} / ${formatDuration(total)}`;
}

function experimentLocked(experiment) {
  return experiment?.active === true;
}

function startExperimentPanel({ request, showError, refreshStatus }) {
  const byId = (id) => document.getElementById(id);
  let submitting = false;
  byId("experiment-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    showError("experiment-error", "");
    submitting = true;
    byId("experiment-start").disabled = true;
    try {
      const payload = experimentPayload(Object.fromEntries(new FormData(event.target)));
      await request("/api/experiments/start", {method: "POST", body: JSON.stringify(payload)});
    } catch (error) { showError("experiment-error", error.message); }
    finally { submitting = false; await refreshStatus(); }
  });
  byId("experiment-next").addEventListener("click", async () => {
    byId("experiment-next").disabled = true;
    try { await request("/api/experiments/next", {method: "POST"}); }
    catch (error) { showError("experiment-error", error.message); }
    finally { await refreshStatus(); }
  });
  byId("experiment-stop").addEventListener("click", async () => {
    byId("experiment-stop").disabled = true;
    try { await request("/api/experiments/stop", {method: "POST"}); }
    catch (error) { showError("experiment-error", error.message); }
    finally { await refreshStatus(); }
  });
  return {
    update(status) {
      const state = status.experiment;
      const locked = experimentLocked(state);
      const calibrating = status.calibration?.active === true;
      byId("experiment-fields").disabled = locked || submitting || calibrating;
      byId("experiment-start").disabled = locked || submitting || status.recording ||
        !status.connected || !status.prior || status.radio?.busy === true || calibrating;
      byId("experiment-stop").disabled = !locked;
      const waiting = locked && state.phase === "waiting";
      byId("experiment-next").hidden = !waiting;
      byId("experiment-next").disabled = !waiting;
      if (waiting) byId("experiment-next").textContent = `Start run ${state.run_index}/${state.run_count}`;
      for (const form of ["prior-form", "recording-form"]) {
        byId(form).querySelectorAll("input,select,button").forEach((input) => { input.disabled = locked || calibrating; });
      }
      const prior = locked ? state.prior : status.prior;
      byId("experiment-prior").textContent = prior
        ? `${prior.mode} prior: ${formatAnalysisValue(prior.expected_distance_m, "m")} m, uncertainty ${formatAnalysisValue(prior.uncertainty_m, "m")} m; revision ${prior.revision}.`
        : "Set a distance or position prior above.";
      byId("experiment-state").textContent = state.batch_id
        ? `${locked ? state.phase : state.outcome} - run ${state.run_index}/${state.run_count}`
        : "No batch started";
      if (locked && state.phase === "waiting") {
        byId("experiment-timing").textContent =
          `Run ${state.run_index}/${state.run_count} waiting · ${formatDuration(experimentTotalRemaining(state))} total`;
      } else if (locked && state.phase === "delay") {
        byId("experiment-timing").textContent =
          `Run ${state.run_index} starts in ${formatDuration(state.remaining_s)} · ${formatDuration(experimentTotalRemaining(state))} left`;
      } else if (locked) {
        byId("experiment-timing").textContent =
          `${formatProgress(state.elapsed_s, state.elapsed_s + state.remaining_s)} · ${formatDuration(experimentTotalRemaining(state))} left`;
      } else if (state.batch_id) {
        byId("experiment-timing").textContent = `Batch ${state.batch_id}: ${state.outcome}`;
      }
      const progress = byId("experiment-progress");
      progress.max = state.run_count || 1;
      const fraction = state.elapsed_s / (state.elapsed_s + state.remaining_s || 1);
      progress.value = state.outcome === "completed" ? state.run_count :
        state.phase === "interval" ? state.run_index :
          state.phase === "waiting" || state.phase === "delay" ? state.run_index - 1 :
          Math.max(0, state.run_index - 1 + (locked ? fraction : 0));
      if (state.error) showError("experiment-error", state.error);
    },
  };
}

if (typeof module !== "undefined") module.exports = { experimentPayload, experimentLocked };
