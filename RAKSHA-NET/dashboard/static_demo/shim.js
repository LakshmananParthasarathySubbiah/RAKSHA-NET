/* Hosted demo only: stands in for the Python backend so the normal dashboard UI can run
   as a static page. It replays precomputed results of the real prediction engine
   (window.__RAKSHA_DATA.rows, made by build_static_demo.py) one row per second and applies
   the firmware's alert rules (12 s cancel window) like the dashboard's demo emulator.
   No serial port, no hardware, no SMS. */
(function () {
  "use strict";
  const D = window.__RAKSHA_DATA;
  const WINDOW_S = 12;
  const STATE = ["normal", "check", "suspected", "fall"];
  const LABEL = { s: "straight", f: "fall" };
  const SRC = { kind: "dataset", label: "fall_dataset.csv (recorded training samples)" };
  const now = () => Date.now() / 1000;
  let sockets = [];
  let evId = 1, alertId = 1, stepTimer = null;
  const H = {};

  function reset(mode) {
    Object.assign(H, {
      mode, source: mode === "demo" ? { ...SRC } : {}, samples: [], console: [],
      alerts: { current: null, history: [], board_last_pred_fall: false },
      emu: { last: false, active: false }, episode: null, latest: null, last_command: null, pos: 0,
      stats: { lines_rx: 0, mldata: 0, sensor_text: 0, invalid: 0, malformed: 0, commands_sent: 0,
        last_line_at: null, last_sample_at: null, started_at: now() },
    });
  }
  function send(msg) { const s = JSON.stringify(msg); sockets.forEach((w) => w._deliver(s)); }
  function event(type, severity, title, detail, source) {
    const ev = { id: evId++, mode: H.mode, source_name: H.source.label || null, t: now(), type, severity, title,
      detail: detail || "", source: source || "dashboard" };
    H.events.push(ev); if (H.events.length > 3000) H.events.shift();
    send({ type: "event", event: ev });
  }
  function consoleLine(dir, text) {
    const item = { t: now(), dir, text };
    H.console.push(item); if (H.console.length > 500) H.console.shift();
    send({ type: "console", item });
  }
  function status() {
    const t = now(), recent = H.samples.filter((s) => t - s.t <= 10);
    let rate = null;
    if (recent.length >= 2) { const span = recent[recent.length - 1].t - recent[0].t; rate = span > 0 ? (recent.length - 1) / span : null; }
    return {
      mode: H.mode, source: H.source,
      serial: { state: "disconnected", port: null, baud: null, error: null, connected_at: null },
      board: { booted_seen: false, sd: "unknown", gsm: "unknown", gsm_baud: null, mpu: "unknown", beacon_scanner: "unknown", event_reporting: H.mode === "demo" },
      stats: { ...H.stats }, sample_rate_hz: rate, stream_stalled: false, settings: D.settings, model: D.model,
      recording: { enabled: false, file: null, lines: 0 }, latest: H.latest, last_command: H.last_command, server_time: t,
    };
  }
  function snapshot() {
    return { type: "snapshot", server_time: now(), mode: H.mode, source: H.source, status: status(),
      samples: H.samples.slice(-300), console: H.console.slice(-300),
      room: { available: false, room: null, rssi: null, at: null, since: null, known_rooms: ["Bedroom", "Hallroom", "Washroom"], counts: {} },
      room_history: [], alerts: { current: H.alerts.current, history: H.alerts.history.slice(-50), board_last_pred_fall: H.emu.last },
      events: H.events.slice(-600) };
  }

  // ----- firmware alert rules (handlePredictionCommand / handleFallAlert), demo only -----
  function startAlert(t, conf) {
    const a = { id: alertId++, started_at: t, start_known: true, window_s: WINDOW_S, window_end: t + WINDOW_S, confidence: conf,
      room: null, rssi: null, start_source: "dashboard (PRED,FALL sent to board)", start_confirmed_by_firmware: true, demo: true,
      phase: "window", sms: [], gsm_not_ready: false, closed_at: null, outcome: null, outcome_text: null, outcome_source: null };
    H.alerts.current = a; H.alerts.history.push(a);
    event("fall", "critical", "Fall alert started on board",
      `PRED,FALL sent (confidence ${conf.toFixed(2)}). Board starts buzzer and 12 s cancel window. Room: unknown.`, a.start_source);
    consoleLine("rx", "[demo emulator] EVENT,FALL_DETECTED_WAITING");
    send({ type: "alert", alert: a, server_time: now() });
  }
  function closeAlert(outcome, text, etype, severity, title) {
    const a = H.alerts.current; if (!a) return;
    Object.assign(a, { phase: "closed", closed_at: now(), outcome, outcome_text: text, outcome_source: "demo firmware emulator" });
    event(etype, severity, title, text, "demo firmware emulator");
    send({ type: "alert", alert: a, server_time: now() });
    H.alerts.current = null;
  }
  function onTx(cmd) {
    const parts = cmd.split(","), st = (parts[1] || "").toUpperCase(), conf = parseFloat(parts[2]);
    if (st === "FALL") {
      if (!H.emu.last && !H.emu.active) { H.emu.active = true; startAlert(now(), conf); }
      H.emu.last = true;
    } else H.emu.last = false;
  }
  function pressButton() {
    if (!H.emu.active || !H.alerts.current) return false;
    H.emu.active = false;
    consoleLine("rx", "[demo emulator] EVENT,FALL_CANCELLED_BUTTON");
    closeAlert("cancelled", "Alert cancelled with the button during the cancel window. No SMS sent.", "cancel", "info", "Fall alert cancelled (button)");
    return true;
  }
  function tick() {
    const a = H.alerts.current;
    if (H.emu.active && a && a.phase === "window" && now() >= a.window_end) {
      H.emu.active = false;
      consoleLine("rx", "[demo emulator] GSMLOG,DEMO_NO_MODEM");
      consoleLine("rx", "[demo emulator] EVENT,FALL_CONFIRMED_SMS_SENT");
      closeAlert("demo_no_modem", "Cancel window elapsed with no cancellation. Demo mode: no GSM modem, no SMS attempted.", "fall", "critical", "Fall confirmed (demo, no SMS)");
    }
    if (a && a.phase !== "closed") send({ type: "tick", server_time: now() });
  }

  // ----- one recorded row per second -----
  function step() {
    const r = D.rows[H.pos];
    const [idx, lab, ax, ay, az, gx, gy, gz, pf, conf, pfall, cnt, stc, cmd, sent] = r;
    const t = now();
    consoleLine("rx", `MLDATA,${ax.toFixed(4)},${ay.toFixed(4)},${az.toFixed(4)},${gx.toFixed(4)},${gy.toFixed(4)},${gz.toFixed(4)}`);
    H.stats.lines_rx++; H.stats.last_line_at = t;
    const sample = { t, ax, ay, az, gx, gy, gz, acc_mag: Math.hypot(ax, ay, az), gyro_mag: Math.hypot(gx, gy, gz),
      prediction: pf ? "fall" : "straight", confidence: conf, p_fall: pfall, fall_count: cnt, state: STATE[stc], command: cmd, sent: !!sent };
    H.samples.push(sample); if (H.samples.length > 900) H.samples.shift();
    H.latest = sample; H.stats.mldata++; H.stats.last_sample_at = t;
    const n = D.settings.fall_confirm_samples;
    if (cnt >= 1 && !H.episode) {
      H.episode = { max: cnt };
      event("fall", "warning", "Suspected fall", `Model predicted fall with confidence ${conf.toFixed(2)} (1/${n} consecutive samples).`, "dashboard (model)");
    } else if (H.episode) {
      if (cnt === 0) {
        if (H.episode.max < n) event("fall", "info", "Suspected fall cleared", `Reached ${H.episode.max}/${n} consecutive fall samples; no alert sent.`, "dashboard (model)");
        H.episode = null;
      } else H.episode.max = Math.max(H.episode.max, cnt);
    }
    send({ type: "sample", sample });
    if (sent) {
      H.stats.commands_sent++; H.last_command = { command: cmd, t };
      consoleLine("tx", cmd); onTx(cmd);
    }
    Object.assign(H.source, { position: H.pos + 1, total: D.rows.length, row_index: idx, row_label: LABEL[lab] });
    H.pos += 1;
    if (H.pos >= D.rows.length) { H.pos = 0; event("demo", "info", "Demo replay restarted from the first row", "", "demo"); }
  }

  function startDemo() {
    stop(true);
    reset("demo");
    event("demo", "info", "Demo replay started",
      `Replaying ${D.rows.length} recorded rows from fall_dataset.csv at 1 row/s. Predictions were computed by the saved Random Forest with the predict_live.py logic. Most rows were used to train the model.`, "demo");
    stepTimer = setInterval(step, 1000);
    send(snapshot());
  }
  function stop(silent) {
    if (stepTimer) { clearInterval(stepTimer); stepTimer = null; }
    if (!silent && H.mode === "demo") event("system", "info", "Demo replay stopped", "Stopped by user", "dashboard");
    const keep = H.events;
    reset("idle"); H.events = keep;
    if (!silent) send(snapshot());
  }

  // ----- WebSocket and fetch stand-ins used by app.js -----
  class FakeSocket {
    constructor() { this.readyState = 0; sockets.push(this);
      setTimeout(() => { this.readyState = 1; this.onopen && this.onopen(); this._deliver(JSON.stringify(snapshot())); }, 30); }
    _deliver(s) { if (this.readyState === 1 && this.onmessage) this.onmessage({ data: s }); }
    send() {}
    close() { this.readyState = 3; sockets = sockets.filter((x) => x !== this); }
  }
  window.WebSocket = FakeSocket;
  const realFetch = window.fetch.bind(window);
  const J = (o, st = 200) => new Response(JSON.stringify(o), { status: st, headers: { "Content-Type": "application/json" } });
  window.fetch = async (url, opts = {}) => {
    const u = typeof url === "string" ? url : url.url;
    if (!u.startsWith("/api/")) return realFetch(url, opts);
    switch (u.split("?")[0]) {
      case "/api/ml": return J({ ready: true, report: D.ml });
      case "/api/defaults": return J({ settings: D.defaults, model: D.model });
      case "/api/ports": return J({ ports: [] });
      case "/api/recordings": return J({ recordings: [] });
      case "/api/sdlogs": return J({ folder: "not available in the online demo", files: [] });
      case "/api/recording": return J({ ok: true });
      case "/api/state": return J(snapshot());
      case "/api/live/start": return J({ detail: "Live hardware needs the dashboard running on the laptop that is connected to the ESP32 (start_dashboard.bat). This online copy only replays recorded data." }, 400);
      case "/api/demo/start": startDemo(); return J({ ok: true });
      case "/api/stop": stop(false); return J({ ok: true });
      case "/api/demo/button": return H.mode === "demo" ? J({ ok: pressButton() }) : J({ detail: "Start the demo first." }, 400);
    }
    return J({ detail: "Not available in the online demo" }, 404);
  };

  // ----- page changes for the hosted copy -----
  document.addEventListener("DOMContentLoaded", () => {
    const sub = document.querySelector(".brand-sub");
    if (sub) sub.textContent = "Online demo of the monitoring dashboard";
    const live = document.querySelector("#liveStart")?.closest("fieldset");
    if (live) {
      live.style.display = "none";
      const note = document.createElement("p");
      note.className = "note hosted-note";
      note.textContent = "This online copy replays recorded data only. To monitor the ESP32 live, run the dashboard on the laptop connected to the board (dashboard/start_dashboard.bat).";
      live.parentNode.insertBefore(note, live);
    }
    document.querySelectorAll('a[href="/api/events.csv"], #sdLoad').forEach((el) => (el.style.display = "none"));
    const rec = document.getElementById("recTable")?.closest(".panel");
    if (rec) rec.style.display = "none";
    const wl = document.querySelector('[data-open="live"]');
    if (wl) wl.closest(".panel").style.display = "none";
  });

  H.events = [];
  reset("idle");
  setInterval(tick, 200);
  setInterval(() => send({ type: "status", status: status() }), 1000);
  startDemo();
})();
