/* RAKSHA-NET dashboard front end. Plain JavaScript + Chart.js (bundled, works offline). */
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const maskNum = (s) => String(s ?? "").replace(/\+?\d{6,}(\d{4})/g, "******$1");

const S = {
  ws: null, wsOk: false, offset: 0, mode: "idle", source: {}, status: null,
  samples: [], console: [], room: null, roomHistory: [], alerts: { current: null, history: [] },
  events: [], ml: null, evFilter: "all", evSearch: "", defaults: null,
};
const serverNow = () => Date.now() / 1000 + S.offset;
const pad = (n) => String(n).padStart(2, "0");
function fmtTime(t) { if (!t) return "–"; const d = new Date(t * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; }
function fmtDateTime(t) { if (!t) return "–"; const d = new Date(t * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${fmtTime(t)}`; }
function fmtAge(t) {
  if (!t) return "–"; const s = Math.max(0, serverNow() - t);
  if (s < 1.5) return "just now"; if (s < 90) return `${Math.round(s)} s ago`; if (s < 5400) return `${Math.round(s / 60)} min ago`; return `${(s / 3600).toFixed(1)} h ago`;
}
const fx = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "–" : Number(v).toFixed(d);
const pct = (v) => (v === null || v === undefined) ? "–" : (v * 100).toFixed(1) + "%";
const N = () => (S.status?.settings?.fall_confirm_samples ?? 3);
const THR = () => (S.status?.settings?.alert_threshold ?? 0.7);

/* ------------------------------------------------------------ websocket */
function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  S.ws = ws;
  ws.onopen = () => { S.wsOk = true; };
  ws.onclose = () => {
    S.wsOk = false; renderBand(); $("navFoot").textContent = "Server not reachable. Retrying…";
    setTimeout(connectWs, 2000);
  };
  ws.onmessage = (m) => { let msg; try { msg = JSON.parse(m.data); } catch (e) { return; } handle(msg); };
}

function syncTime(t) { if (t) S.offset = t - Date.now() / 1000; }

function handle(msg) {
  switch (msg.type) {
    case "snapshot":
      syncTime(msg.server_time);
      S.mode = msg.mode; S.source = msg.source || {}; S.status = msg.status;
      S.samples = msg.samples || []; S.console = msg.console || [];
      S.room = msg.room; S.roomHistory = msg.room_history || [];
      S.alerts = msg.alerts || { current: null, history: [] };
      S.events = msg.events || [];
      rebuildConsole(); renderAll(); scheduleCharts(true);
      break;
    case "status":
      syncTime(msg.status.server_time);
      S.status = msg.status; S.mode = msg.status.mode; S.source = msg.status.source || {};
      renderStatus(); renderBand(); renderFall(false);
      break;
    case "sample":
      S.samples.push(msg.sample); if (S.samples.length > 900) S.samples.splice(0, S.samples.length - 900);
      if (S.status) S.status.latest = msg.sample;
      renderLive(); renderBand(); renderStepper(); scheduleCharts();
      break;
    case "console":
      S.console.push(msg.item); if (S.console.length > 500) S.console.splice(0, S.console.length - 500);
      appendConsole(msg.item);
      break;
    case "room":
      S.room = msg.room; S.roomHistory.push(msg.item); if (S.roomHistory.length > 900) S.roomHistory.shift();
      renderRoom(); scheduleCharts();
      break;
    case "alert": {
      syncTime(msg.server_time);
      const a = msg.alert, h = S.alerts.history, i = h.findIndex((x) => x.id === a.id);
      if (i >= 0) h[i] = a; else h.push(a);
      S.alerts.current = a.phase === "closed" ? (S.alerts.current?.id === a.id ? null : S.alerts.current) : a;
      renderFall(true); renderBand(); renderRoom();
      break;
    }
    case "tick": syncTime(msg.server_time); break;
    case "event":
      S.events.push(msg.event); if (S.events.length > 3000) S.events.shift();
      scheduleEvents();
      break;
  }
}

/* ------------------------------------------------------------ routing */
const VIEWS = ["live", "fall", "room", "ml", "events"];
function route() {
  const v = VIEWS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "live";
  VIEWS.forEach((x) => $("view-" + x).classList.toggle("active", x === v));
  document.querySelectorAll(".nav a").forEach((a) => { if (a.dataset.view === v) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  if (v === "ml") loadMl();
  if (v === "events") { loadRecordings(); renderEvents(); }
  scheduleCharts(true);
}

/* ------------------------------------------------------------ band */
function recentClosed() {
  const h = S.alerts.history; if (!h.length) return null;
  const a = h[h.length - 1];
  return a.phase === "closed" && a.closed_at && serverNow() - a.closed_at < 10 ? a : null;
}
function bandModel() {
  if (!S.wsOk) return ["idle", "Dashboard server not reachable", "Start start_dashboard.bat (or run_dashboard.py), then reload this page."];
  if (S.mode === "idle") return ["idle", "No data source", "Connect the ESP32 or start the demo replay from Data source."];
  const st = S.status || {}, ser = st.serial || {}, a = S.alerts.current, demo = S.mode === "demo";
  if (a && a.phase === "window") {
    const rem = Math.max(0, Math.ceil((a.window_end ?? 0) - serverNow()));
    const how = demo ? "Use the on-screen button to simulate the device button." : "Buzzer sounding on the device. Press its button to cancel.";
    return ["alert", "Fall alert: SMS unless cancelled", `${how} Confidence ${fx(a.confidence)}. Room: ${a.room || "unknown"}.`, rem];
  }
  if (a && a.phase === "sms") {
    if (a.sms.length) return ["sms", "Fall confirmed: sending emergency SMS", `${a.sms.length} attempt(s) so far. The sensor stream pauses while the SIM800L is busy.`];
    return ["sms", "Cancel window over", demo ? "Waiting for the simulated board." : "Waiting for SMS activity or for the sensor stream to resume to determine the outcome."];
  }
  const rc = recentClosed();
  if (rc) {
    const cancelled = /^cancelled/.test(rc.outcome);
    return [cancelled ? "check" : "fall", cancelled ? "Fall alert cancelled" : outcomeLabel(rc), rc.outcome_text || ""];
  }
  if (S.mode === "live" && ser.state === "error") return ["stalled", "Serial port error", ser.error || ""];
  if (S.mode === "live" && ser.state === "connecting") return ["check", `Opening ${ser.port}…`, "Waiting for the port."];
  if (S.mode === "live" && ser.state === "disconnected") return ["stalled", "Serial port closed", ser.error || "Reconnect from Data source."];
  if (st.stream_stalled) return ["stalled", "No sensor data", `No valid MLDATA for ${Math.round(serverNow() - (st.stats?.last_sample_at || serverNow()))} s. Check the board and cable.`];
  const l = st.latest;
  if (!l) return ["check", "Waiting for sensor data", S.mode === "live" ? "Port is open. MLDATA lines arrive about once per second." : "Demo starting."];
  if (l.state === "normal") return ["normal", "Normal monitoring", `Model output: straight, confidence ${fx(l.confidence)}.`];
  if (l.state === "check") return ["check", "Checking", `Model leans to fall but below the ${fx(THR())} threshold (confidence ${fx(l.confidence)}).`];
  if (l.state === "suspected") return ["suspected", `Suspected fall, ${l.fall_count} of ${N()} samples`, `Model predicts fall with confidence ${fx(l.confidence)}. The board is alerted after ${N()} in a row.`];
  return ["fall", "Fall predicted", S.status?.settings?.no_board_alert ? "--no-board-alert is set, so nothing is sent to the board." : "PRED,FALL sent to the board."];
}
function renderBand() {
  const [state, title, sub, count] = bandModel();
  const band = $("band");
  band.dataset.state = state; $("bandTitle").textContent = title; $("bandSub").textContent = sub;
  const c = $("bandCount");
  if (count !== undefined) { c.classList.remove("hidden"); c.innerHTML = `${count}<small>s</small>`; } else c.classList.add("hidden");
  const showCancel = state === "alert" && S.mode === "demo" && S.source.kind === "dataset";
  $("bandCancel").classList.toggle("hidden", !showCancel);
  $("navAlert").classList.toggle("hidden", !(S.alerts.current && S.alerts.current.phase !== "closed"));
  // demo banner
  const demo = S.mode === "demo";
  $("demoBanner").classList.toggle("hidden", !demo);
  if (demo) {
    $("demoText").textContent = S.source.kind === "recording"
      ? `Replaying recorded session ${S.source.file}. Not live readings.`
      : "Replaying recorded rows from fall_dataset.csv. Not live readings; the board is simulated and no SMS is sent.";
    $("demoPos").textContent = S.source.total ? `Row ${S.source.position} of ${S.source.total}` + (S.source.row_label ? ` (recorded label: ${S.source.row_label})` : "") : "";
  }
}

/* ------------------------------------------------------------ status / connection */
function renderStatus() {
  const st = S.status; if (!st) return;
  const chip = $("connChip"), ser = st.serial || {};
  let k = "idle", txt = "Not connected";
  if (S.mode === "demo") { k = "demo"; txt = "Demo replay"; }
  else if (S.mode === "live") {
    if (ser.state === "connected") { k = st.stream_stalled ? "wait" : "live"; txt = `Live, ${ser.port}`; }
    else if (ser.state === "connecting") { k = "wait"; txt = `Opening ${ser.port}`; }
    else if (ser.state === "error") { k = "error"; txt = `Error on ${ser.port}`; }
    else { k = "error"; txt = `${ser.port} closed`; }
  }
  chip.dataset.k = k; $("connText").textContent = txt;
  $("welcome").classList.toggle("hidden", S.mode !== "idle");

  const s = st.stats || {};
  const rows = [
    ["Mode", S.mode === "live" ? "Live hardware" : S.mode === "demo" ? "Demo replay" : "None"],
    ["Source", S.source.label || "–"],
  ];
  if (S.mode === "live") rows.push(["Serial", ser.state || "–"]);
  rows.push(
    ["Sample rate", st.sample_rate_hz ? `${st.sample_rate_hz.toFixed(2)} /s` : "–"],
    ["Last sample", fmtAge(s.last_sample_at)],
    ["Lines received", s.lines_rx ?? 0],
    ["Valid samples", s.mldata ?? 0],
    ["Invalid ignored", s.invalid ?? 0],
    ["Commands sent", S.mode === "demo" && S.source.kind === "dataset" ? `${s.commands_sent ?? 0} (to simulated board)` : (s.commands_sent ?? 0)],
  );
  if (S.mode === "live") rows.push(["Recording", st.recording?.file ? st.recording.file : "off"]);
  $("connKv").innerHTML = rows.map(([a, b]) => `<dt>${esc(a)}</dt><dd>${esc(b)}</dd>`).join("");

  const b = st.board || {};
  const pill = (v, map) => { const [cls, t] = map[v] || ["", v || "not seen"]; return `<span class="pill ${cls}">${esc(t)}</span>`; };
  const demoDataset = S.mode === "demo" && S.source.kind === "dataset";
  if (demoDataset) {
    $("bootKv").innerHTML = `<dt>Board</dt><dd><span class="pill warn">simulated in demo</span></dd>`;
    $("bootNote").textContent = "The dataset has no start-up messages, room data or GSM replies.";
  } else {
    $("bootKv").innerHTML = [
      ["SD card", pill(b.sd, { ok: ["ok", "ready"], fail: ["bad", "mount failed"], unknown: ["", "not seen"] })],
      ["GSM (SIM800L)", pill(b.gsm, { ready: ["ok", b.gsm_baud ? `ready, ${b.gsm_baud} baud` : "ready"], fail: ["bad", "AT failed"], unknown: ["", "not seen"] })],
      ["MPU6500", pill(b.mpu, { ok: ["ok", "ok"], missing: ["bad", "not found"], read_fail: ["warn", "read failed"], unknown: ["", "not seen"] })],
      ["BLE scanner", pill(b.beacon_scanner, { running: ["ok", "started"], unknown: ["", "not seen"] })],
      ["Event reporting", b.event_reporting ? `<span class="pill ok">on (patched firmware)</span>` : `<span class="pill">off</span>`],
    ].map(([a, v]) => `<dt>${a}</dt><dd>${v}</dd>`).join("");
    $("bootNote").textContent = S.mode === "live" && !b.booted_seen
      ? "Start-up messages not seen yet. Press EN/RST on the ESP32 while connected to read them."
      : "GSM ready means the modem answered AT commands at boot; it does not test network signal or credit.";
  }

  const m = st.model || {};
  $("navFoot").textContent = `Model ${m.file || ""}, scikit-learn ${m.running_sklearn || "?"}` + (m.version_mismatch ? ` (saved with ${m.trained_sklearn})` : "");
  renderRules();
}

/* ------------------------------------------------------------ live */
function renderLive() {
  const st = S.status || {}, l = st.latest;
  const n = N(), thr = THR();
  $("thrMark").style.left = (thr * 100) + "%";
  if (!l) {
    $("predState").textContent = S.mode === "idle" ? "No data source" : "Waiting for data"; $("predState").dataset.s = "";
    ["ax", "ay", "az", "gx", "gy", "gz"].forEach((k) => $("r-" + k).textContent = "–");
    $("r-acc").textContent = "–"; $("r-gyro").textContent = "–";
    $("confVal").textContent = "–"; $("pfallVal").textContent = "–"; $("confBar").style.width = "0"; $("pfallBar").style.width = "0";
    $("countVal").textContent = `0 / ${n}`; drawPips(0, n); $("sampleAge").textContent = ""; $("predAge").textContent = "";
    $("cmdText").textContent = "No command sent yet"; $("cmdAge").textContent = "";
    return;
  }
  const names = { normal: "Normal (straight)", check: "Check", suspected: "Suspected fall", fall: "Fall" };
  $("predState").textContent = names[l.state] || l.state; $("predState").dataset.s = l.state;
  $("predLabel").textContent = `Model class: ${l.prediction}. Board command: ${l.command}${l.sent ? "" : " (not re-sent, within send interval)"}.`;
  $("confVal").textContent = fx(l.confidence); $("confBar").style.width = (l.confidence * 100) + "%";
  $("pfallVal").textContent = `${fx(l.p_fall)} / ${fx(thr)}`; $("pfallBar").style.width = ((l.p_fall ?? 0) * 100) + "%";
  $("countVal").textContent = l.fall_count > n ? `${l.fall_count} (alert at ${n})` : `${l.fall_count} / ${n}`; drawPips(l.fall_count, n);
  for (const k of ["ax", "ay", "az"]) $("r-" + k).textContent = fx(l[k], 2);
  for (const k of ["gx", "gy", "gz"]) $("r-" + k).textContent = fx(l[k], 1);
  $("r-acc").textContent = fx(l.acc_mag, 3); $("r-gyro").textContent = fx(l.gyro_mag, 1);
  const lc = st.last_command;
  if (lc) { $("cmdText").textContent = `Last sent: ${lc.command}`; }
}
function drawPips(count, n) {
  const p = $("pips"); const k = Math.min(n, 12);
  if (p.children.length !== k) p.innerHTML = "<i></i>".repeat(k);
  [...p.children].forEach((el, i) => el.classList.toggle("on", i < Math.min(count, k)));
  p.classList.toggle("full", count >= n);
}
function tickAges() {
  const st = S.status; if (!st) return;
  const l = st.latest;
  $("sampleAge").textContent = l ? `updated ${fmtAge(l.t)}` : "";
  $("predAge").textContent = l ? fmtTime(l.t) : "";
  $("cmdAge").textContent = st.last_command ? fmtAge(st.last_command.t) : "";
}

/* console */
function consoleLine(it) {
  const div = document.createElement("div");
  const isEv = it.text.startsWith("[demo emulator]") || it.text.startsWith("EVENT,") || it.text.startsWith("GSMLOG,");
  div.className = it.dir === "tx" ? "tx" : (isEv ? "ev" : "");
  div.dataset.ml = it.dir === "rx" && it.text.startsWith("MLDATA,") ? "1" : "";
  div.innerHTML = `<span class="t">${fmtTime(it.t)} ${it.dir === "tx" ? "→" : "←"}</span>${esc(it.text)}`;
  if (div.dataset.ml && $("hideMl").checked) div.style.display = "none";
  return div;
}
function rebuildConsole() {
  const c = $("console"); c.innerHTML = "";
  const frag = document.createDocumentFragment();
  S.console.slice(-400).forEach((it) => frag.appendChild(consoleLine(it)));
  c.appendChild(frag); c.scrollTop = c.scrollHeight;
}
function appendConsole(it) {
  if ($("pauseConsole").checked) return;
  const c = $("console"); const atEnd = c.scrollHeight - c.scrollTop - c.clientHeight < 40;
  c.appendChild(consoleLine(it));
  while (c.children.length > 400) c.removeChild(c.firstChild);
  if (atEnd) c.scrollTop = c.scrollHeight;
}

/* ------------------------------------------------------------ charts */
const charts = {};
function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function baseOpts(yTitle, extra = {}) {
  const grid = cssVar("--line-2"), tick = cssVar("--ink-3");
  return {
    animation: false, responsive: true, maintainAspectRatio: false, parsing: false, normalized: true,
    interaction: { mode: "nearest", intersect: false, axis: "x" },
    plugins: { legend: { display: false }, tooltip: { callbacks: { title: (it) => it.length ? fmtTime(it[0].parsed.x) : "" } } },
    scales: {
      x: { type: "linear", grid: { color: grid }, ticks: { color: tick, maxTicksLimit: 6, callback: (v) => fmtTime(v) } },
      y: { grid: { color: grid }, ticks: { color: tick }, title: { display: !!yTitle, text: yTitle, color: tick }, ...extra },
    },
  };
}
function line(label, color, dash) {
  return { label, data: [], borderColor: color, backgroundColor: color, borderWidth: dash ? 1.5 : 1.75, pointRadius: 0, borderDash: dash ? [5, 4] : [], tension: 0, spanGaps: false };
}
function makeCharts() {
  Object.values(charts).forEach((c) => c.destroy && c.destroy());
  const X = cssVar("--s-x"), Y = cssVar("--s-y"), Z = cssVar("--s-z"), M = cssVar("--s-m"), A = cssVar("--alarm"), I2 = cssVar("--ink-2");
  charts.acc = new Chart($("chAcc"), { type: "line", data: { datasets: [line("X", X), line("Y", Y), line("Z", Z), line("|a|", M, true)] }, options: baseOpts("g") });
  charts.gyro = new Chart($("chGyro"), { type: "line", data: { datasets: [line("X", X), line("Y", Y), line("Z", Z), line("|ω|", M, true)] }, options: baseOpts("dps") });
  charts.prob = new Chart($("chProb"), { type: "line", data: { datasets: [line("P(fall)", A), line("Threshold", I2, true)] }, options: baseOpts("", { min: 0, max: 1 }) });
  const roomColors = { Bedroom: X, Hallroom: Y, Washroom: Z };
  charts.rssi = new Chart($("chRssi"), {
    type: "line",
    data: { datasets: ["Bedroom", "Hallroom", "Washroom", "Other"].map((r) => ({ ...line(r, roomColors[r] || cssVar("--ink-3")), showLine: false, pointRadius: 2.5 })) },
    options: baseOpts("dBm", { suggestedMin: -100, suggestedMax: -40 }),
  });
  $("roomLegend").innerHTML = ["Bedroom", "Hallroom", "Washroom"].map((r) => `<span><i style="background:${roomColors[r]}"></i>${r}</span>`).join("");
  if (S.ml) renderMlCharts();
}
let chartPending = false;
function scheduleCharts(force) {
  if (chartPending && !force) return; chartPending = true;
  setTimeout(() => { chartPending = false; updateCharts(); }, force ? 0 : 400);
}
function updateCharts() {
  if (!charts.acc) return;
  const now = serverNow(), from = now - 90;
  const s = S.samples.filter((x) => x.t >= from - 2);
  const pts = (k) => s.map((x) => ({ x: x.t, y: x[k] }));
  const visible = (id) => $(id).offsetParent !== null;
  if (visible("chAcc")) {
    ["ax", "ay", "az", "acc_mag"].forEach((k, i) => charts.acc.data.datasets[i].data = pts(k));
    Object.assign(charts.acc.options.scales.x, { min: from, max: now }); charts.acc.update("none");
    ["gx", "gy", "gz", "gyro_mag"].forEach((k, i) => charts.gyro.data.datasets[i].data = pts(k));
    Object.assign(charts.gyro.options.scales.x, { min: from, max: now }); charts.gyro.update("none");
    charts.prob.data.datasets[0].data = pts("p_fall");
    charts.prob.data.datasets[1].data = [{ x: from, y: THR() }, { x: now, y: THR() }];
    Object.assign(charts.prob.options.scales.x, { min: from, max: now }); charts.prob.update("none");
  }
  if (visible("chRssi")) {
    const rf = now - 300, rh = S.roomHistory.filter((x) => x.t >= rf && x.rssi !== null && x.rssi !== undefined);
    ["Bedroom", "Hallroom", "Washroom"].forEach((r, i) => charts.rssi.data.datasets[i].data = rh.filter((x) => x.room === r).map((x) => ({ x: x.t, y: x.rssi })));
    charts.rssi.data.datasets[3].data = rh.filter((x) => !["Bedroom", "Hallroom", "Washroom"].includes(x.room)).map((x) => ({ x: x.t, y: x.rssi }));
    Object.assign(charts.rssi.options.scales.x, { min: rf, max: now }); charts.rssi.update("none");
  }
}

/* ------------------------------------------------------------ fall detection */
const OUTCOME = {
  cancelled: ["ok", "Cancelled (button)"], cancelled_inferred: ["ok", "Cancelled (inferred)"],
  confirmed_sms_attempted: ["bad", "Confirmed, SMS attempted"], confirmed_no_gsm: ["bad", "Confirmed, SMS not sent (GSM not ready)"],
  window_elapsed: ["bad", "Confirmed"], demo_no_modem: ["bad", "Confirmed (demo, no SMS)"],
  no_sms_gsm_fail: ["warn", "Ended, outcome not reported"], unknown: ["warn", "Ended, outcome not reported"],
  board_restarted: ["warn", "Interrupted by board restart"],
};
function outcomeLabel(a) {
  if (a.phase === "window") return "Cancel window open";
  if (a.phase === "sms") return a.sms.length ? "Sending SMS" : "Waiting for outcome";
  return (OUTCOME[a.outcome] || ["", a.outcome || "–"])[1];
}
function outcomePill(a) {
  const cls = a.phase === "closed" ? (OUTCOME[a.outcome] || ["warn"])[0] : (a.phase === "window" ? "bad" : "warn");
  return `<span class="pill ${cls}">${esc(outcomeLabel(a))}</span>`;
}
const SMS = { accepted: ["ok", "Accepted by modem"], error: ["bad", "Modem error"], no_reply: ["warn", "No reply printed"], no_confirmation: ["warn", "Not confirmed"], pending: ["info", "Waiting for reply"] };

function renderStepper() {
  const order = ["normal", "suspected", "alert", "confirmed", "sms"];
  const a = S.alerts.current || recentClosed();
  const l = S.status?.latest;
  let cur = null, color = "var(--info)";
  if (S.mode === "idle" || !S.wsOk) cur = null;
  else if (a && a.phase === "window") { cur = "alert"; color = "var(--alarm)"; }
  else if (a && a.phase === "sms") { cur = a.sms.length ? "sms" : "confirmed"; color = "var(--alarm)"; }
  else if (a && a.phase === "closed") {
    if (/^cancelled/.test(a.outcome)) { cur = "alert"; color = "var(--safe)"; }
    else if (a.outcome === "confirmed_sms_attempted") { cur = "sms"; color = "var(--alarm)"; }
    else { cur = "confirmed"; color = "var(--alarm)"; }
  } else if (l) {
    cur = l.state === "normal" ? "normal" : (l.state === "check" ? "normal" : "suspected");
    color = l.state === "normal" ? "var(--safe)" : (l.state === "check" ? "var(--info)" : "var(--watch)");
  }
  const ci = order.indexOf(cur);
  document.querySelectorAll("#stepper li").forEach((li, i) => {
    li.classList.toggle("now", i === ci); li.classList.toggle("done", ci > 0 && i < ci);
    li.style.setProperty("--c", color);
  });
  $("stepSusp").textContent = `Fall with P ≥ ${fx(THR())}, fewer than ${N()} samples in a row.`;
}

function renderRules() {
  const st = S.status || {}, s = st.settings || {}, b = st.board || {};
  $("rulesKv").innerHTML = [
    ["Fall class probability", `≥ ${fx(s.alert_threshold)}`],
    ["Consecutive fall samples", s.fall_confirm_samples],
    ["Repeat command interval", `${s.send_interval} s (PRED,FALL sent at once)`],
    ["No data before PRED,CHECK", `${s.stale_seconds} s`],
    ["Minimum |a| for a valid sample", `${s.min_acc_mag} g (or |ω| > 0.5 dps)`],
    ["Cancel window on board", "12 s (FALL_CONFIRM_WINDOW_MS)"],
    ["Buzzer", "toggles every 300 ms during the window"],
    ["Cancel button", "GPIO14 to GND (INPUT_PULLUP)"],
    ["SMS recipients", "2 (PHONE_NUMBER_1, PHONE_NUMBER_2)"],
    ["Commands to board", s.no_board_alert ? "off (--no-board-alert)" : "on"],
  ].map(([a, v]) => `<dt>${esc(a)}</dt><dd>${esc(v)}</dd>`).join("");
  $("patchNote").innerHTML = S.mode === "demo" && S.source.kind === "dataset"
    ? "Demo: the board's alert logic is simulated in the dashboard with the same rules."
    : b.event_reporting
      ? "<b>Event reporting is on.</b> Cancellations and SMS results come from EVENT/GSMLOG lines printed by the firmware."
      : "<b>Event reporting is off.</b> The current firmware does not print alert start, cancellation or SMS log status on serial, so the dashboard infers them from the commands it sends and the SMS replies it sees. Flash the optional patch in firmware_event_patch/ to report them directly.";
}

function renderFall(full) {
  renderStepper();
  const live = S.alerts.current;
  const a = live || S.alerts.history[S.alerts.history.length - 1];
  const active = !!(live && live.phase !== "closed");
  $("alertIdle").classList.toggle("hidden", active);
  $("alertActive").classList.toggle("hidden", !active);
  $("alertSrc").textContent = active ? `#${live.id}` : "";
  if (active) {
    $("alertKv").innerHTML = [
      ["Started", live.start_known ? fmtTime(live.started_at) : "not seen"],
      ["Confidence", fx(live.confidence)],
      ["Room at start", live.room ? `${live.room}${live.rssi ? ` (${live.rssi} dBm)` : ""}` : (S.source.kind === "dataset" ? "not in this replay" : "unknown")],
      ["Started by", live.start_source + (live.start_confirmed_by_firmware ? ", confirmed by firmware event" : "")],
      ["Status", outcomeLabel(live)],
    ].map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
    $("alertHint").textContent = S.mode === "demo" && S.source.kind === "dataset"
      ? "Demo: the button below stands in for the device button."
      : "Cancel with the physical button on the device. The dashboard cannot cancel an alert on the board.";
    $("demoCancel").classList.toggle("hidden", !(S.mode === "demo" && S.source.kind === "dataset" && live.phase === "window"));
    tickCountdown();
  }
  // SMS attempts for the active or most recent alert
  if (a && a.sms && a.sms.length) {
    $("smsFor").textContent = `alert #${a.id}`;
    $("smsList").innerHTML = a.sms.map((x) => {
      const [cls, t] = SMS[x.status] || ["", x.status];
      return `<div class="attempt"><div class="attempt-head"><span>${esc(x.recipient)}</span><span class="pill ${cls}">${esc(t)}</span></div>
        <div class="small muted" style="margin-top:4px">${esc(x.status_text)}</div>
        ${x.firmware_status ? `<div class="small faint">Firmware log status: <span class="mono">${esc(x.firmware_status)}</span></div>` : ""}
        ${x.reply && x.reply.length ? `<pre>${esc(x.reply.join("\n"))}</pre>` : ""}</div>`;
    }).join("");
  } else if (active) {
    $("smsFor").textContent = `alert #${live.id}`;
    $("smsList").innerHTML = `<div class="empty"><b>No SMS attempts yet</b>${live.phase === "window"
      ? "The firmware sends SMS only if the button is not pressed within 12 s."
      : "Waiting for SMS activity from the firmware."}</div>`;
  } else if (a && a.phase === "closed") {
    $("smsFor").textContent = `alert #${a.id}`;
    const msg = a.outcome === "demo_no_modem" ? "Demo mode has no GSM modem. No SMS was attempted."
      : a.outcome === "confirmed_no_gsm" ? "GSM was not ready, so the firmware did not attempt any SMS."
      : /^cancelled/.test(a.outcome) ? "The alert was cancelled, so no SMS was attempted." : "No SMS attempt was seen on serial for this alert.";
    $("smsList").innerHTML = `<div class="empty"><b>No SMS attempts</b>${esc(msg)}</div>`;
  } else if (!a) {
    $("smsFor").textContent = "";
  }
  if (full !== false) renderAlertHistory();
  renderFallEvents();
}
function tickCountdown() {
  const a = S.alerts.current;
  if (!a || a.phase === "closed") return;
  if (a.phase === "window" && a.window_end) {
    const rem = Math.max(0, a.window_end - serverNow());
    $("ringNum").textContent = Math.ceil(rem);
    $("ringLbl").textContent = "seconds to cancel";
    $("ringFg").style.strokeDashoffset = String(540.35 * (1 - rem / (a.window_s || 12)));
  } else {
    $("ringNum").textContent = "0"; $("ringLbl").textContent = a.sms.length ? "sending SMS" : "window over";
    $("ringFg").style.strokeDashoffset = "540.35";
  }
}
function renderAlertHistory() {
  const h = [...S.alerts.history].reverse();
  $("alertHist").innerHTML = h.length ? h.map((a) => {
    const sms = a.sms.length ? a.sms.map((x) => `${x.recipient}: ${(SMS[x.status] || ["", x.status])[1]}`).join("<br>") : "–";
    return `<tr><td class="time">${a.start_known ? fmtTime(a.started_at) : "not seen"}</td><td class="num">${fx(a.confidence)}</td>
      <td>${esc(a.room || "unknown")}</td><td>${outcomePill(a)}${a.outcome_text ? `<div class="small faint" style="margin-top:4px;max-width:46ch">${esc(a.outcome_text)}</div>` : ""}</td>
      <td class="small">${sms}</td><td class="small muted">${esc(a.outcome_source || "–")}</td></tr>`;
  }).join("") : `<tr><td colspan="6"><div class="empty"><b>No alerts in this session</b>Alerts are listed here with their outcome.</div></td></tr>`;
}
function renderFallEvents() {
  const ev = S.events.filter((e) => ["fall", "cancel", "gsm"].includes(e.type)).slice(-40).reverse();
  $("fallEvents").innerHTML = ev.length ? ev.map((e) => `<tr class="sev-${e.severity}"><td class="time">${fmtTime(e.t)}</td><td><b>${esc(e.title)}</b><div class="small muted">${esc(e.detail)}</div></td></tr>`).join("")
    : `<tr><td colspan="2"><div class="empty">No fall, cancel or GSM events yet.</div></td></tr>`;
}

/* ------------------------------------------------------------ room */
function renderRoom() {
  const r = S.room || {}, demoDataset = S.mode === "demo" && S.source.kind === "dataset";
  const noData = demoDataset || (S.mode !== "idle" && !r.available);
  $("roomNoData").classList.toggle("hidden", !noData);
  $("roomNoDataText").textContent = demoDataset
    ? "fall_dataset.csv has no BLE data. Room data appears in live mode or when replaying a recorded live session."
    : S.mode === "idle" ? "" : "Waiting for the firmware's sensor text line (ACC X: … | Room: … RSSI: …).";
  const alerting = !!(S.alerts.current && S.alerts.current.phase !== "closed");
  const counts = r.counts || {};
  document.querySelectorAll(".room").forEach((el) => {
    const name = el.dataset.room, here = r.available && r.room === name;
    el.classList.toggle("here", here && !alerting); el.classList.toggle("alerting", here && alerting);
    const meta = el.querySelector("[data-meta]");
    if (name !== "Outside_Range") meta.textContent = counts[name] ? `${counts[name]} readings this session` : "Not detected yet";
    else meta.textContent = counts[name] ? `${counts[name]} readings this session` : "No known beacon above −95 dBm";
  });
  const known = ["Bedroom", "Hallroom", "Washroom", "Outside_Range"];
  if (r.available && r.room && !known.includes(r.room)) $("roomName").textContent = `Detected room: ${r.room} (not in the firmware list)`;
  else $("roomName").textContent = `Detected room: ${r.available ? (r.room === "Outside_Range" ? "outside range" : r.room) : "–"}`;
  const rssi = r.available ? r.rssi : null;
  $("rssiBig").innerHTML = `${rssi === null || rssi === undefined ? "–" : rssi}<small>dBm</small>`;
  const p = rssi === null || rssi === undefined ? 0 : Math.min(100, Math.max(0, (rssi + 100) / 70 * 100));
  $("rssiMark").style.left = p + "%"; $("rssiMark").style.opacity = rssi === null || rssi === undefined ? .2 : 1;
  $("roomKv").innerHTML = [
    ["In this room since", r.since ? fmtTime(r.since) : "–"],
    ["Last room update", r.at ? fmtAge(r.at) : "–"],
    ["Beacon scan", "3 s active scan + 0.5 s pause"],
    ["Beacons listed in firmware", (r.known_rooms || ["Bedroom", "Hallroom", "Washroom"]).map((x) => "ROOM_" + x).join(", ")],
  ].map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
}

/* ------------------------------------------------------------ ML */
let mlTimer = null;
async function loadMl(refresh) {
  if (S.ml && !refresh) return;
  try {
    const res = await fetch("/api/ml" + (refresh ? "?refresh=1" : ""));
    const j = await res.json();
    if (!j.ready) {
      $("mlLoading").querySelector(".empty").innerHTML = j.error ? `<b>Could not compute results</b>${esc(j.error)}` : "<b>Computing results</b>Loading the dataset and evaluating the saved model…";
      clearTimeout(mlTimer); mlTimer = setTimeout(() => loadMl(), 1500); return;
    }
    S.ml = j.report; renderMl();
  } catch (e) { clearTimeout(mlTimer); mlTimer = setTimeout(() => loadMl(), 3000); }
}
function renderMl() {
  const r = S.ml; if (!r) return;
  $("mlLoading").classList.add("hidden"); $("mlBody").classList.remove("hidden");
  const m = r.saved_model_test, f = m.per_class.fall || {}, sp = r.split;
  $("mlIntro").textContent = `Computed by the dashboard from ${r.datasets[0].file} and ${r.model.file} on ${fmtDateTime(r.generated_at)}. Nothing on this page is typed in by hand.`;
  $("mlSplit").textContent = `${m.n_test} of ${sp.train_rows + sp.test_rows} rows, test_size 0.25, stratified, random_state 42`;
  const tile = (k, v, s) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
  $("mlTiles").innerHTML = tile("Accuracy", pct(m.accuracy), `${Math.round(m.accuracy * m.n_test)} of ${m.n_test} test rows correct`)
    + tile("Precision, fall", pct(f.precision), "of rows predicted fall, share that were fall")
    + tile("Recall, fall", pct(f.recall), `of ${f.support} fall rows, share found`)
    + tile("F1-score, fall", pct(f.f1), "harmonic mean of precision and recall");
  const rp = r.reproduction;
  $("mlRepro").innerHTML = rp.identical
    ? `<b>Check passed:</b> retraining with the settings in train_random_forest.py on the same 75% training split gives identical predictions to the saved model on all ${sp.train_rows + sp.test_rows} rows. So the saved model was trained on this split, and these ${m.n_test} test rows were not used to train it.`
    : `<b>Check failed:</b> a retrain with the script's settings agrees with the saved model on ${pct(rp.agreement_all_rows)} of rows. The saved model may have been trained on different data, so some test rows may have been seen in training.`;
  // confusion matrix
  const L = m.labels, cm = m.confusion_matrix;
  let html = `<div></div>${L.map((l) => `<div class="h"><span>Predicted<br><b>${esc(l)}</b></span></div>`).join("")}`;
  L.forEach((tl, i) => {
    const rowSum = cm[i].reduce((a, b) => a + b, 0);
    html += `<div class="rh"><span>Actual<br><b>${esc(tl)}</b></span></div>` + L.map((pl, j) =>
      `<div class="c ${i === j ? "diag" : "off"}"><b>${cm[i][j]}</b><span>${rowSum ? ((cm[i][j] / rowSum) * 100).toFixed(1) : 0}% of actual ${esc(tl)}</span></div>`).join("");
  });
  $("cm").innerHTML = html;
  $("perClass").innerHTML = L.map((l) => { const c = m.per_class[l]; return `<tr><td>${esc(l)}</td><td class="num">${fx(c.precision, 3)}</td><td class="num">${fx(c.recall, 3)}</td><td class="num">${fx(c.f1, 3)}</td><td class="num">${c.support}</td></tr>`; }).join("")
    + `<tr><td>Macro average</td><td class="num">${fx(m.macro.precision, 3)}</td><td class="num">${fx(m.macro.recall, 3)}</td><td class="num">${fx(m.macro.f1, 3)}</td><td class="num">${m.n_test}</td></tr>`;
  // datasets
  $("dataTable").innerHTML = r.datasets.map((d) => `<tr><td class="mono small">${esc(d.file)}</td><td class="num">${d.rows}</td><td class="num">${d.labels.straight || 0}</td><td class="num">${d.labels.fall || 0}</td>
    <td class="small">${d.used_for_training ? "Used by train_random_forest.py" : d.subset_of_main ? "Same as the first rows of fall_dataset.csv (not used separately)" : "Not used for training"}</td></tr>`).join("");
  // config
  const p = r.model.params, ts = r.train_settings;
  $("cfgKv").innerHTML = [
    ["Model", `${r.model.type} (${r.model.file})`],
    ["Trees (n_estimators)", p.n_estimators],
    ["Split criterion", p.criterion],
    ["Features per split (max_features)", p.max_features],
    ["Max depth", p.max_depth === null ? `none (deepest tree: ${r.model.max_tree_depth})` : p.max_depth],
    ["Min samples per leaf", p.min_samples_leaf],
    ["Bootstrap", String(p.bootstrap)],
    ["Class weight", p.class_weight],
    ["Random state", p.random_state],
    ["Classes", r.model.classes.join(", ")],
    ["Input features (8)", r.model.feature_names.join(", ")],
    ["Train / test rows", `${sp.train_rows} / ${sp.test_rows}`],
    ["Test split", `${ts.test_size}, stratified by label, random_state ${ts.random_state}`],
    ["scikit-learn", `saved with ${r.model.trained_sklearn}, running ${r.model.running_sklearn}`],
  ].map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  $("mlCaveats").innerHTML = [
    "Each row is one sensor sample (about one per second from the firmware loop). The test split is random by row, so neighbouring samples from the same recording run are in both the training and test sets. Expect lower figures on new recordings or other wearers.",
    `These are per-sample results. The live system raises an alert only after ${N()} consecutive fall predictions with confidence of at least ${fx(THR())}, so event-level detection and false-alarm rates must be measured with hardware trials.`,
    "collect_data.py gives every row of a run the same label, so the fall class includes the rows recorded before and after each fall within a fall run.",
    "Inference runs on the laptop in Python, not on the ESP32. The ESP32 streams MLDATA over USB serial and acts on the PRED commands it receives.",
  ].map((t) => `<li>${esc(t)}</li>`).join("");
  renderMlCharts();
}
function renderMlCharts() {
  const r = S.ml; if (!r || !$("chImp")) return;
  ["imp", "data"].forEach((k) => charts[k] && charts[k].destroy());
  const tick = cssVar("--ink-3"), grid = cssVar("--line-2");
  const fi = r.feature_importance;
  charts.imp = new Chart($("chImp"), {
    type: "bar", data: { labels: fi.map((d) => d.feature), datasets: [{ data: fi.map((d) => d.importance), backgroundColor: cssVar("--info"), borderRadius: 3, barPercentage: .7 }] },
    options: { animation: false, indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${(c.parsed.x * 100).toFixed(1)}%` } } },
      scales: { x: { grid: { color: grid }, ticks: { color: tick, callback: (v) => (v * 100).toFixed(0) + "%" } }, y: { grid: { display: false }, ticks: { color: cssVar("--ink"), font: { family: "Plex Mono" } } } } },
  });
  const main = r.datasets[0];
  charts.data = new Chart($("chData"), {
    type: "bar", data: { labels: ["straight", "fall"], datasets: [{ data: [main.labels.straight || 0, main.labels.fall || 0], backgroundColor: [cssVar("--safe"), cssVar("--alarm")], borderRadius: 3, barPercentage: .6 }] },
    options: { animation: false, indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { x: { grid: { color: grid }, ticks: { color: tick } }, y: { grid: { display: false }, ticks: { color: cssVar("--ink") } } } },
  });
}

/* ------------------------------------------------------------ events */
const EV_TYPES = [["all", "All"], ["fall", "Fall"], ["cancel", "Cancel"], ["sensor", "Sensor"], ["gsm", "GSM"], ["room", "Room"], ["sd", "SD card"], ["system", "System"], ["demo", "Demo"]];
let evPending = false;
function scheduleEvents() { if (evPending) return; evPending = true; setTimeout(() => { evPending = false; renderEvents(); renderFallEvents(); }, 300); }
function renderEvents() {
  const counts = {}; S.events.forEach((e) => counts[e.type] = (counts[e.type] || 0) + 1);
  $("evFilters").innerHTML = EV_TYPES.map(([k, l]) => `<button class="chip" type="button" data-f="${k}" aria-pressed="${S.evFilter === k}">${l}<span class="n">${k === "all" ? S.events.length : (counts[k] || 0)}</span></button>`).join("");
  if (!$("view-events").classList.contains("active")) return;
  const q = S.evSearch.toLowerCase();
  const rows = S.events.filter((e) => (S.evFilter === "all" || e.type === S.evFilter) && (!q || (e.title + " " + e.detail + " " + e.source).toLowerCase().includes(q))).slice(-1000).reverse();
  $("evTable").innerHTML = rows.length ? rows.map((e) => `<tr class="sev-${e.severity}"><td class="time">${fmtTime(e.t)}</td><td><span class="pill ${e.severity === "critical" ? "bad" : e.severity === "warning" ? "warn" : ""}">${esc(e.type)}</span></td>
      <td><b>${esc(e.title)}</b>${e.detail ? `<div class="small muted">${esc(e.detail)}</div>` : ""}</td><td class="small muted">${esc(e.source)}</td><td class="small">${e.mode === "demo" ? '<span class="pill warn">demo</span>' : esc(e.mode)}</td></tr>`).join("")
    : `<tr><td colspan="5"><div class="empty"><b>No matching events</b>Events appear as the dashboard runs.</div></td></tr>`;
}

/* SD logs */
function parseCsv(text) {
  const rows = []; let row = [], cur = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) { if (ch === '"') { if (text[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += ch; }
    else if (ch === '"') q = true;
    else if (ch === ",") { row.push(cur); cur = ""; }
    else if (ch === "\n" || ch === "\r") { if (ch === "\r" && text[i + 1] === "\n") i++; row.push(cur); cur = ""; if (row.some((c) => c !== "")) rows.push(row); row = []; }
    else cur += ch;
  }
  if (cur !== "" || row.length) { row.push(cur); if (row.some((c) => c !== "")) rows.push(row); }
  return rows;
}
function renderSdFiles(files) {
  $("sdTables").innerHTML = files.length ? files.map((f) => {
    if (f.error) return `<h3>${esc(f.file)}</h3><p class="note">${esc(f.error)}</p>`;
    const rows = f.rows.slice(0, 2000);
    return `<h3>${esc(f.file)} <span class="faint small">${f.total_rows} rows</span></h3><div class="table-wrap" style="max-height:340px"><table class="data"><thead><tr><th>#</th>${f.header.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((r, i) => `<tr><td class="num">${i + 1}</td>${r.map((c) => `<td class="${/^-?\d+(\.\d+)?$/.test(c) ? "num" : ""}">${esc(maskNum(c))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }).join("") : `<div class="empty mt"><b>No CSV files found</b>Copy ai_fall_log.csv and gsm_log.csv from the MicroSD card into dashboard/sd_logs/.</div>`;
}
async function loadSdFolder() {
  $("sdMsg").textContent = "Loading…";
  try { const j = await (await fetch("/api/sdlogs")).json(); renderSdFiles(j.files); $("sdMsg").textContent = `Folder: ${j.folder}`; }
  catch (e) { $("sdMsg").textContent = "Could not reach the server."; }
}
function openSdFiles(list) {
  const files = [...list]; if (!files.length) return;
  Promise.all(files.map((f) => f.text().then((t) => { const rows = parseCsv(t); return { file: f.name, header: rows[0] || [], rows: rows.slice(1), total_rows: Math.max(0, rows.length - 1) }; })))
    .then((out) => { renderSdFiles(out); $("sdMsg").textContent = `Opened ${out.length} file(s) in the browser.`; });
}

/* recordings */
async function loadRecordings() {
  try {
    const j = await (await fetch("/api/recordings")).json();
    S.recordings = j.recordings || [];
    $("recTable").innerHTML = S.recordings.length ? S.recordings.map((r) => `<tr><td class="mono small">${esc(r.file)}</td><td class="small">${esc((r.created || "–").replace("T", " "))}</td><td>${esc(r.port || "–")}</td>
      <td class="num">${r.lines}</td><td class="num">${r.duration_s} s</td><td><button class="btn" type="button" data-replay="${esc(r.file)}">Replay in demo</button></td></tr>`).join("")
      : `<tr><td colspan="6"><div class="empty"><b>No recordings yet</b>Connect to the device once with recording on; the session is saved automatically.</div></td></tr>`;
    fillDemoSelect();
  } catch (e) { /* server offline */ }
}

/* ------------------------------------------------------------ drawer & controls */
function openDrawer(focus) {
  $("drawer").classList.add("open"); $("drawer").setAttribute("aria-hidden", "false"); $("scrim").classList.remove("hidden");
  loadPorts(); loadDefaults(); loadRecordings();
  setTimeout(() => (focus === "demo" ? $("demoSel") : $("portSel")).focus(), 250);
}
function closeDrawer() { $("drawer").classList.remove("open"); $("drawer").setAttribute("aria-hidden", "true"); $("scrim").classList.add("hidden"); }
async function loadPorts() {
  const sel = $("portSel"); sel.innerHTML = "<option>Searching…</option>";
  try {
    const j = await (await fetch("/api/ports")).json();
    sel.innerHTML = j.ports.length ? j.ports.map((p) => `<option value="${esc(p.device)}">${esc(p.device)}${p.description && p.description !== "n/a" ? " – " + esc(p.description) : ""}</option>`).join("")
      : `<option value="">No serial ports found</option>`;
    const cur = S.status?.serial?.port; if (cur) sel.value = cur;
  } catch (e) { sel.innerHTML = `<option value="">Server not reachable</option>`; }
}
async function loadDefaults() {
  if (S.defaults) return;
  try {
    const j = await (await fetch("/api/defaults")).json(); S.defaults = j.settings;
    const d = j.settings; $("s-thr").value = d.alert_threshold; $("s-n").value = d.fall_confirm_samples; $("s-int").value = d.send_interval;
    $("s-stale").value = d.stale_seconds; $("s-min").value = d.min_acc_mag; $("s-noalert").checked = d.no_board_alert; $("baud").value = d.baud;
  } catch (e) { }
}
function fillDemoSelect() {
  const sel = $("demoSel"), cur = sel.value;
  sel.innerHTML = `<option value="dataset">fall_dataset.csv (recorded training samples)</option>` + (S.recordings || []).map((r) => `<option value="${esc(r.file)}">Recorded session ${esc(r.file)}</option>`).join("");
  if (cur) sel.value = cur;
  $("demoNote").textContent = sel.value === "dataset"
    ? "fall_dataset.csv contains accelerometer and gyroscope rows only, so room and SMS panels stay empty. The board's 12 s cancel window is simulated and no SMS is sent."
    : "Replays every line received during that live session, including room and GSM lines, in real time. Predictions are recomputed with the current settings.";
}
function settingsFromForm() {
  return { alert_threshold: +$("s-thr").value, fall_confirm_samples: parseInt($("s-n").value, 10), send_interval: +$("s-int").value,
    stale_seconds: +$("s-stale").value, min_acc_mag: +$("s-min").value, no_board_alert: $("s-noalert").checked };
}
async function post(url, body) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  let j = {}; try { j = await res.json(); } catch (e) { }
  if (!res.ok) throw new Error(typeof j.detail === "string" ? j.detail : (j.detail ? JSON.stringify(j.detail) : res.statusText));
  return j;
}
async function startLive() {
  const port = $("portText").value.trim() || $("portSel").value;
  $("liveMsg").className = "form-msg"; $("liveMsg").textContent = "Connecting…";
  try {
    await loadDefaults();
    await post("/api/recording", { enabled: $("recordOn").checked });
    await post("/api/live/start", { port, baud: parseInt($("baud").value, 10) || 115200, settings: settingsFromForm() });
    $("liveMsg").textContent = ""; closeDrawer(); location.hash = "#live";
  } catch (e) { $("liveMsg").className = "form-msg err"; $("liveMsg").textContent = e.message; }
}
async function startDemo(source) {
  $("demoMsg").className = "form-msg"; $("demoMsg").textContent = "Starting…";
  try {
    await loadDefaults();
    await post("/api/demo/start", { source: source || $("demoSel").value, settings: settingsFromForm() });
    $("demoMsg").textContent = ""; closeDrawer(); location.hash = "#live";
  } catch (e) { $("demoMsg").className = "form-msg err"; $("demoMsg").textContent = e.message; }
}
async function demoButton() { try { await post("/api/demo/button"); } catch (e) { alertInline(e.message); } }
function alertInline(m) { $("bandSub").textContent = m; }

/* ------------------------------------------------------------ init */
function renderAll() { renderStatus(); renderLive(); renderBand(); renderFall(true); renderRoom(); renderEvents(); tickAges(); }

function init() {
  makeCharts();
  window.addEventListener("hashchange", route); route();
  $("openDrawer").onclick = () => openDrawer(); $("closeDrawer").onclick = closeDrawer; $("scrim").onclick = closeDrawer;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  document.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => openDrawer(b.dataset.open));
  $("portRefresh").onclick = loadPorts; $("liveStart").onclick = startLive; $("demoStart").onclick = () => startDemo();
  $("demoSel").onchange = fillDemoSelect;
  $("stopBtn").onclick = async () => { try { await post("/api/stop"); closeDrawer(); } catch (e) { } };
  $("bandCancel").onclick = demoButton; $("demoCancel").onclick = demoButton;
  $("hideMl").onchange = () => document.querySelectorAll("#console [data-ml='1']").forEach((d) => d.style.display = $("hideMl").checked ? "none" : "");
  $("pauseConsole").onchange = () => { if (!$("pauseConsole").checked) rebuildConsole(); };
  $("evFilters").onclick = (e) => { const b = e.target.closest("[data-f]"); if (b) { S.evFilter = b.dataset.f; renderEvents(); } };
  $("evSearch").oninput = (e) => { S.evSearch = e.target.value; renderEvents(); };
  $("sdLoad").onclick = loadSdFolder; $("sdFile").onchange = (e) => openSdFiles(e.target.files);
  $("recTable").onclick = (e) => { const b = e.target.closest("[data-replay]"); if (b) startDemo(b.dataset.replay); };
  $("mlRefresh").onclick = () => { S.ml = null; $("mlBody").classList.add("hidden"); $("mlLoading").classList.remove("hidden"); loadMl(true); };
  $("themeBtn").onclick = () => {
    const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark"; document.documentElement.dataset.theme = next;
    try { localStorage.setItem("raksha-theme", next); } catch (e) { }
    makeCharts(); scheduleCharts(true);
  };
  setInterval(() => { tickAges(); renderBand(); tickCountdown(); renderStepper(); }, 250);
  setInterval(() => scheduleCharts(), 1000);
  connectWs();
}
document.addEventListener("DOMContentLoaded", init);
