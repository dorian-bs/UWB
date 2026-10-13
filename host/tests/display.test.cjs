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
