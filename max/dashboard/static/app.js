// SightLine head-motion dashboard: reads /stream (Server-Sent Events) and draws it.
"use strict";

const WINDOW_S = 10;                      // seconds of history in the charts
const KINDS = [                           // CSV columns v0..v2 for each row kind
  ["GYRO", ["gx", "gy", "gz"], "°/s"],
  ["ACC", ["ax", "ay", "az"], "g"],
  ["CAM", ["yaw_deg"], "°"],
  ["MARK", ["index"], ""],
  ["RESET", ["yaw_deg"], "°"],
  ["DROP", ["samples_lost"], ""],
];

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmt = (x, d = 2) => (x === null || x === undefined ? "–" : Number(x).toFixed(d));

// ------------------------------------------------------------------ charts
class Chart {
  constructor(canvas, names, opts = {}) {
    this.canvas = canvas;
    this.names = names;
    this.series = names.map(() => []);
    this.min = opts.min;                  // fixed range if given, else auto
    this.max = opts.max;
    this.wrap = opts.wrap || false;       // angles: don't draw a line across the +-180 jump
  }
  push(i, t, v) {
    if (v !== null && v !== undefined) this.series[i].push([t, v]);
  }
  trim(tNow) {
    for (const s of this.series) {
      while (s.length && s[0][0] < tNow - WINDOW_S) s.shift();
    }
  }
  draw(tNow) {
    const c = this.canvas, dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    if (c.width !== w * dpr || c.height !== h * dpr) { c.width = w * dpr; c.height = h * dpr; }
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    this.trim(tNow);

    let lo = this.min, hi = this.max;
    if (lo === undefined || hi === undefined) {
      lo = Infinity; hi = -Infinity;
      for (const s of this.series) for (const [, v] of s) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
      if (!isFinite(lo)) { lo = -1; hi = 1; }
      const pad = Math.max((hi - lo) * 0.1, 0.05);
      lo -= pad; hi += pad;
    }
    const left = 44, right = 8, top = 8, bottom = 18;
    const x = (t) => left + (w - left - right) * (1 - (tNow - t) / WINDOW_S);
    const y = (v) => top + (h - top - bottom) * (1 - (v - lo) / (hi - lo));

    g.font = "11px system-ui, sans-serif";
    g.fillStyle = css("--muted");
    g.strokeStyle = css("--grid");
    g.lineWidth = 1;
    for (let k = 0; k <= 4; k++) {        // horizontal grid + labels
      const v = lo + (hi - lo) * k / 4, yy = y(v);
      g.beginPath(); g.moveTo(left, yy); g.lineTo(w - right, yy); g.stroke();
      g.fillText(v.toFixed(Math.abs(hi - lo) < 5 ? 2 : 0), 2, yy + 4);
    }
    for (let s = 0; s <= WINDOW_S; s += 2) g.fillText(`-${s}s`, x(tNow - s) - 10, h - 4);

    const colors = [css("--s1"), css("--s2"), css("--s3")];
    this.series.forEach((s, i) => {
      g.strokeStyle = colors[i % colors.length];
      g.lineWidth = 1.5;
      g.beginPath();
      let prev = null;
      for (const [t, v] of s) {
        const jump = this.wrap && prev !== null && Math.abs(v - prev) > 180;
        if (prev === null || jump) g.moveTo(x(t), y(v)); else g.lineTo(x(t), y(v));
        prev = v;
      }
      g.stroke();
    });
    this.names.forEach((n, i) => {        // legend
      g.fillStyle = colors[i % colors.length];
      g.fillText(n, w - right - 46 * (this.names.length - i), top + 10);
    });
  }
}

const charts = {
  gyro: new Chart($("chart-gyro"), ["gx", "gy", "gz"]),
  acc: new Chart($("chart-acc"), ["ax", "ay", "az"]),
  yaw: new Chart($("chart-yaw"), ["yaw", "camera"], { min: -180, max: 180, wrap: true }),
};
let tNow = 0;

// ------------------------------------------------------------ fields table
const tbody = $("fields");
for (const [kind, names, unit] of KINDS) {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td>${kind}</td><td data-k="t">–</td>` +
    [0, 1, 2].map((i) => `<td data-k="v${i}">${names[i] ? "–" : ""}</td>`).join("") +
    `<td data-k="n">0</td>`;
  tr.dataset.kind = kind;
  tbody.appendChild(tr);
}
function renderFields(latest, counts) {
  for (const [kind, names, unit] of KINDS) {
    const tr = tbody.querySelector(`tr[data-kind="${kind}"]`);
    const row = latest[kind];
    tr.querySelector('[data-k="n"]').textContent = counts[kind] || 0;
    if (!row) continue;
    tr.querySelector('[data-k="t"]').textContent = fmt(row[1], 3);
    names.forEach((n, i) => {
      const digits = kind === "MARK" || kind === "DROP" ? 0 : 3;
      tr.querySelector(`[data-k="v${i}"]`).innerHTML =
        `${fmt(row[2 + i], digits)}<span class="unit">${n}${unit ? " " + unit : ""}</span>`;
    });
  }
}

// ---------------------------------------------------------------- panels
function renderHeader(s) {
  $("source").textContent = `source: ${s.source}`;
  const st = $("status");
  const state = s.ended ? "ended" : s.health.connected ? "live" : "stale";
  st.textContent = state === "live" ? "live" : state === "ended" ? "source ended" : "no gyro data";
  st.className = `pill ${state}`;
  $("rate").textContent = fmt(s.health.rate_hz, 0);
  $("drops").textContent = fmt(s.health.drops_per_min, 1);
  $("lost").textContent = s.health.lost_samples;
  $("battery").textContent = s.health.battery_pct === null ? "n/a" : `${fmt(s.health.battery_pct, 0)}%`;
  const err = $("error");
  err.hidden = !s.error;
  err.textContent = s.error || "";
}

function renderYaw(s) {
  const y = s.yaw;
  $("bias").textContent = s.bias ? s.bias.join(", ") + " °/s" : "measuring…";
  if (!y) return;
  $("yaw").textContent = y.yaw_deg === null ? "–" : fmt(y.yaw_deg, 1);
  $("rate-dps").textContent = fmt(y.rate_dps, 1);
  $("yaw-source").textContent = y.source;
  $("cam-age").textContent = y.cam_age_s === null ? "no camera" : `${fmt(y.cam_age_s, 2)} s`;
  $("valid").innerHTML = y.valid ? '<span class="good">yes</span>' : '<span class="badv">no</span>';
  if (y.yaw_deg !== null) $("yaw-marker").style.left = `${50 + y.yaw_deg / 3.6}%`;
}

function renderRecording(r) {
  $("rec-dot").className = r.on ? "dot on" : "dot";
  $("btn-start").disabled = r.on;
  $("btn-stop").disabled = !r.on;
  $("rec-name").disabled = r.on;
  $("rec-text").textContent = r.on
    ? `REC ${r.name} · ${fmt(r.seconds, 1)} s · ${r.rows} rows`
    : "not recording";
}

function addLog(entries) {
  const ol = $("log");
  for (const [t, text] of entries) {
    const li = document.createElement("li");
    li.innerHTML = `<time>${fmt(t, 2)}</time>`;
    li.appendChild(document.createTextNode(text));
    ol.prepend(li);
  }
  while (ol.children.length > 200) ol.lastChild.remove();
}

// ---------------------------------------------------------------- stream
function onSnapshot(s) {
  tNow = s.now;
  for (const [kind, t, v0, v1, v2] of s.rows) {
    if (kind === "GYRO") { charts.gyro.push(0, t, v0); charts.gyro.push(1, t, v1); charts.gyro.push(2, t, v2); }
    else if (kind === "ACC") { charts.acc.push(0, t, v0); charts.acc.push(1, t, v1); charts.acc.push(2, t, v2); }
    else if (kind === "CAM") charts.yaw.push(1, t, v0);
  }
  for (const [t, y] of s.yaws) charts.yaw.push(0, t, y);
  renderHeader(s);
  renderFields(s.latest, s.counts);
  renderYaw(s);
  renderRecording(s.recording);
  addLog(s.log);
}

function connect() {
  const es = new EventSource("/stream");
  es.onmessage = (e) => onSnapshot(JSON.parse(e.data));
  es.onerror = () => {
    const st = $("status");
    st.textContent = "dashboard disconnected";
    st.className = "pill stale";
  };
}

function frame() {
  for (const c of Object.values(charts)) c.draw(tNow);
  requestAnimationFrame(frame);
}

// ---------------------------------------------------------------- buttons
async function post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const j = await r.json();
  const err = $("action-error");
  err.hidden = j.ok;
  err.textContent = j.ok ? "" : j.error;
  if (j.recording) renderRecording(j.recording);
  return j;
}
$("btn-start").onclick = () => post("/api/record/start", { name: $("rec-name").value });
$("btn-stop").onclick = () => post("/api/record/stop");
$("btn-mark").onclick = () => post("/api/mark");
$("btn-rezero").onclick = () => post("/api/rezero");

connect();
requestAnimationFrame(frame);
