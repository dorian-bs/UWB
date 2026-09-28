const history = [];
let recording = false;

const byId = (id) => document.getElementById(id);
const format = (value, digits = 3) => value == null ? "--" : Number(value).toFixed(digits);

function addSample(sample) {
  history.push(sample);
  if (history.length > 180) history.shift();
  byId("range-value").textContent = format(sample.corrected_m);
  byId("expected-value").textContent = format(sample.expected_m);
  byId("residual-value").textContent = format(sample.residual_m);
  byId("bias-value").textContent = format(sample.rolling_bias_m);
  byId("spread-value").textContent = format(sample.rolling_std_m);
  drawAll();
}

function drawChart(canvas, series, zeroLine = false) {
  const box = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(box.width * scale));
  canvas.height = Math.max(1, Math.floor(box.height * scale));
  const context = canvas.getContext("2d");
  context.scale(scale, scale);

  const width = box.width;
  const height = box.height;
  const pad = { top: 16, right: 15, bottom: 23, left: 48 };
  const values = series.flatMap((item) => item.values).filter((value) => value != null && Number.isFinite(value));
  context.clearRect(0, 0, width, height);
  if (values.length < 2) {
    context.fillStyle = "#64706c";
    context.font = "13px Avenir Next";
    context.fillText("Waiting for samples", pad.left, height / 2);
    return;
  }

  let minimum = Math.min(...values);
  let maximum = Math.max(...values);
  const margin = Math.max((maximum - minimum) * 0.15, zeroLine ? 0.01 : 0.03);
  minimum -= margin;
  maximum += margin;
  if (zeroLine) {
    minimum = Math.min(minimum, 0);
    maximum = Math.max(maximum, 0);
  }

  const x = (index) => pad.left + index * (width - pad.left - pad.right) / Math.max(history.length - 1, 1);
  const y = (value) => pad.top + (maximum - value) * (height - pad.top - pad.bottom) / (maximum - minimum);

  context.strokeStyle = "#d7ded8";
  context.fillStyle = "#64706c";
  context.font = "11px SFMono-Regular";
  for (let index = 0; index <= 4; index += 1) {
    const value = minimum + (maximum - minimum) * index / 4;
    const rowY = y(value);
    context.beginPath();
    context.moveTo(pad.left, rowY);
    context.lineTo(width - pad.right, rowY);
    context.stroke();
    context.fillText(value.toFixed(3), 4, rowY + 4);
  }

  if (zeroLine && minimum <= 0 && maximum >= 0) {
    context.strokeStyle = "#83908b";
    context.setLineDash([5, 5]);
    context.beginPath();
    context.moveTo(pad.left, y(0));
    context.lineTo(width - pad.right, y(0));
    context.stroke();
    context.setLineDash([]);
  }

  for (const item of series) {
    context.strokeStyle = item.color;
    context.lineWidth = 2;
    context.beginPath();
    let drawing = false;
    item.values.forEach((value, index) => {
      if (value == null) { drawing = false; return; }
      if (drawing) context.lineTo(x(index), y(value));
      else { context.moveTo(x(index), y(value)); drawing = true; }
    });
    context.stroke();
  }
}

function drawAll() {
  drawChart(byId("distance-chart"), [
    { color: "#176b87", values: history.map((sample) => sample.corrected_m) },
    { color: "#bc7a16", values: history.map((sample) => sample.expected_m) },
  ]);
  drawChart(byId("residual-chart"), [
    { color: "#c2413b", values: history.map((sample) => sample.residual_m) },
  ], true);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "Request failed");
  return body;
}

async function refreshStatus() {
  try {
    const status = await request("/api/status");
    byId("status-dot").classList.toggle("connected", status.connected);
    byId("connection-label").textContent = status.connected ? "Live" : "Disconnected";
    byId("source-label").textContent = status.source;
    byId("sample-count").textContent = status.total_samples;
    byId("gap-count").textContent = status.sequence_gaps;
    byId("malformed-count").textContent = status.malformed_lines;
    byId("prior-revision").textContent = status.prior ? `Revision ${status.prior.revision}` : "No prior";
    recording = status.recording;
    byId("recording-state").textContent = recording ? status.session : "Stopped";
    byId("record-button").textContent = recording ? "Stop recording" : "Start recording";
    byId("record-button").classList.toggle("stop", recording);
    byId("error-message").hidden = !status.last_error;
    byId("error-message").textContent = status.last_error || "";
  } catch (error) {
    byId("connection-label").textContent = "Host unavailable";
  }
}

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws`);
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "snapshot") {
      history.length = 0;
      message.samples.forEach(addSample);
    } else if (message.type === "sample") {
      addSample(message.sample);
    }
  };
  socket.onclose = () => setTimeout(connect, 1500);
}

byId("prior-mode").addEventListener("change", (event) => {
  const positions = event.target.value === "positions";
  byId("distance-fields").hidden = positions;
  byId("position-fields").hidden = !positions;
  document.querySelector('[name="distance_m"]').required = !positions;
});

byId("prior-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const fields = new FormData(event.target);
  const mode = fields.get("mode");
  const payload = {
    mode,
    uncertainty_m: Number(fields.get("uncertainty_m")),
    note: fields.get("note"),
  };
  if (mode === "distance") payload.distance_m = Number(fields.get("distance_m"));
  else {
    payload.node_a_m = ["ax", "ay", "az"].map((name) => Number(fields.get(name)));
    payload.node_b_m = ["bx", "by", "bz"].map((name) => Number(fields.get(name)));
  }
  try {
    await request("/api/prior", { method: "POST", body: JSON.stringify(payload) });
    await refreshStatus();
  } catch (error) {
    alert(error.message);
  }
});

byId("recording-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    if (recording) await request("/api/recording/stop", { method: "POST" });
    else {
      const name = new FormData(event.target).get("name");
      await request("/api/recording/start", { method: "POST", body: JSON.stringify({ name }) });
    }
    await refreshStatus();
  } catch (error) {
    alert(error.message);
  }
});

window.addEventListener("resize", drawAll);
connect();
refreshStatus();
setInterval(refreshStatus, 1000);