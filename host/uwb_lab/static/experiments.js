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
        : "Set a distance or position prior in Geometry prior below.";
      byId("experiment-state").textContent = state.batch_id
        ? `${locked ? state.phase : state.outcome} - run ${state.run_index}/${state.run_count}`
        : "No batch started";
      if (locked && state.phase === "waiting") {
        byId("experiment-timing").textContent =
          `Waiting for you to start run ${state.run_index}/${state.run_count}. Move the setup, then press the button.`;
      } else if (locked && state.phase === "delay") {
        byId("experiment-timing").textContent =
          `Run ${state.run_index} starts in ${formatAnalysisValue(state.remaining_s, "s")} s. Get clear of the setup.`;
      } else if (locked) {
        byId("experiment-timing").textContent =
          `${formatAnalysisValue(state.elapsed_s, "s")} s elapsed; ${formatAnalysisValue(state.remaining_s, "s")} s remaining in this ${state.phase === "interval" ? "interval" : "run"}. Saved on the host.`;
      } else if (state.batch_id) {
        byId("experiment-timing").textContent = `Batch ${state.batch_id}: ${state.outcome}. Saved recordings remain available below.`;
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
