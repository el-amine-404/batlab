"use strict";

const $ = (sel) => document.querySelector(sel);
const STORE_KEY = "tv-remote:current";

let current = null; // { mac, name, model, ip }
let statusTimer = null;

// ------------------------------------------------------------------ helpers

function remember(mac) {
  try { mac ? localStorage.setItem(STORE_KEY, mac) : localStorage.removeItem(STORE_KEY); } catch {}
}
function remembered() {
  try { return localStorage.getItem(STORE_KEY); } catch { return null; }
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Remote": "1" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== "/api/login") {
    showLogin(); // the session ran out, or was logged out elsewhere
    throw new Error("log in again");
  }
  if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
  return data;
}

let toastTimer;
function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", error);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

function buzz() {
  if (navigator.vibrate) navigator.vibrate(8);
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c);
  return node;
}

function show(view) {
  $("#view-login").hidden = view !== "login";
  $("#view-tvs").hidden = view !== "tvs";
  $("#view-remote").hidden = view !== "remote";
  $("#logout-wrap").hidden = view === "login";
}

// ---------------------------------------------------------------------- login

function showLogin(setup = false) {
  stopStatus();
  show("login");
  $("#login-setup").hidden = !setup;
  $("#login").hidden = setup;
  if (!setup) setTimeout(() => $("#login-pass").focus(), 50);
}

async function login(e) {
  e.preventDefault();
  const pass = $("#login-pass");
  try {
    await api("/api/login", { password: pass.value });
    pass.value = "";
    start();
  } catch (err) {
    toast(err.message, true);
    pass.select();
  }
}

async function start() {
  const me = await fetch("/api/me").then((r) => r.json().then((d) => ({ status: r.status, ...d }))).catch(() => ({ status: 0 }));
  if (me.setup) return showLogin(true);
  if (me.status !== 200) return showLogin();
  const tvs = await api("/api/tvs").catch(() => []);
  const saved = tvs.find((t) => t.mac === remembered()) || (tvs.length === 1 ? tvs[0] : null);
  saved ? openRemote(saved) : showTvs();
}

// ------------------------------------------------------------------ TV list

function icon(name) {
  // <svg><use> only renders when created in the SVG namespace.
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "ic");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function tvCard(tv, action) {
  return el("div", { class: "card tv-item" },
    icon("tv"),
    el("div", { class: "grow" },
      el("b", {}, tv.name),
      el("span", { class: "muted small" }, [tv.model, tv.ip].filter(Boolean).join(" · ")),
    ),
    action,
  );
}

async function showTvs() {
  show("tvs");
  stopStatus();
  const list = $("#tv-list");
  list.replaceChildren();
  const tvs = await api("/api/tvs").catch(() => []);
  for (const tv of tvs) {
    list.append(tvCard(tv, el("button", { class: "btn btn-primary", onclick: () => openRemote(tv) }, "Open")));
  }
  $("#scan-note").hidden = tvs.length > 0;
  $("#scan-note").textContent = "No TV yet. The TV must be switched on to be found.";
}

async function scan() {
  const btn = $("#scan");
  btn.disabled = true;
  btn.querySelector("span").textContent = "Scanning…";
  try {
    const found = await api("/api/discover", {});
    const list = $("#tv-list");
    list.replaceChildren();
    if (!found.length) {
      $("#scan-note").hidden = false;
      $("#scan-note").textContent = "No Samsung TV answered. Is it switched on and on this network?";
    }
    for (const tv of found) {
      const action = tv.paired
        ? el("button", { class: "btn btn-primary", onclick: () => openRemote(tv) }, "Open")
        : el("button", { class: "btn btn-primary", onclick: () => pair(tv) }, "Connect");
      list.append(tvCard(tv, action));
    }
      if (found.length) $("#scan-note").hidden = true;
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.querySelector("span").textContent = "Scan again";
  }
}

async function pair(tv) {
  const dlg = $("#pairing");
  dlg.showModal();
  try {
    const paired = await api("/api/pair", { mac: tv.mac, ip: tv.ip });
    dlg.close();
    toast("Connected");
    openRemote(paired);
  } catch (e) {
    dlg.close();
    toast(e.message, true);
  }
}

// ------------------------------------------------------------------- remote

function openRemote(tv) {
  current = tv;
  remember(tv.mac);
  $("#tv-name").textContent = tv.name;
  $("#tv-model").textContent = [tv.model, tv.ip].filter(Boolean).join(" · ");
  $("#kb").hidden = true;
  show("remote");
  startStatus();
  loadApps();
}

// The TV's state as last read: power on/standby/off, mute, volume, app in front.
let state = {};
let busy = false;     // a power change is running; its own result wins
let volHeld = false;  // a volume key is held: the page counts, polls wait
let sliding = false;  // the slider is being dragged: don't move it under the finger

function render() {
  const dot = $("#status-dot");
  const on = state.power === "on";
  dot.className = "dot " + (busy ? "busy" : on ? "on" : state.power ? "off" : "");
  dot.title = busy ? "working…" : on ? "on" : state.power === "standby" ? "standby" : "off";
  // Muted, or at 0, looks muted; the level stays visible so you know what returns.
  const known = on && state.volume != null;
  const silent = on && (state.mute || state.volume === 0);
  const mute = $("#mute");
  mute.setAttribute("aria-pressed", String(!!(on && state.mute)));
  mute.querySelector("use").setAttribute("href", silent ? "#i-mute" : "#i-volume");
  mute.querySelector("span").textContent = on && state.mute ? "Muted" : "Mute";
  $("#vol-label").textContent = known ? `VOL ${state.volume}` : "VOL";
  $("#vol-num").textContent = known ? state.volume : "–";
  $("#vol-icon use").setAttribute("href", silent ? "#i-mute" : "#i-volume");
  $("#vol-rocker").classList.toggle("muted", silent);
  $("#vol-row").classList.toggle("muted", silent);
  const slider = $("#vol-slider");
  slider.disabled = !known;
  if (known && !sliding) slider.value = state.volume;
  renderMedia();
  $("#tv-now").hidden = !(on && state.app);
  $("#tv-now").textContent = state.app ? `Now: ${state.app}` : "";
}

async function refreshStatus() {
  if (!current || busy || volHeld || sliding) return;
  try {
    state = await api(`/api/tv/${current.mac}/status`);
  } catch {
    state = {};
  }
  render();
}
// ------------------------------------------------------------- now playing

let seeking = false; // the timeline is being dragged

function clock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(s).padStart(2, "0")}`;
}

function renderMedia() {
  const m = state.power === "on" ? state.media : null;
  const card = $("#media");
  card.hidden = !m;
  const playing = !!m && m.state === "playing";
  // The play/pause key in the main rows follows what plays, when it is known.
  const pp = $("#playpause");
  pp.querySelector("use").setAttribute("href", playing ? "#i-pause" : "#i-play");
  pp.querySelector("span").textContent = m ? (playing ? "Pause" : "Play") : "Play/Pause";
  if (!m) return;
  $("#media-title").textContent = m.title || "";
  $("#media-sub").textContent = m.subtitle || "";
  $("#media-source").textContent = m.source === "youtube" ? "YouTube" : "Jellyfin";
  $("#media-toggle use").setAttribute("href", playing ? "#i-pause" : "#i-play");
  document.querySelectorAll("#media .skip").forEach((b) => { b.hidden = !m.can_skip; });
  const seek = $("#media-seek");
  seek.disabled = !m.can_seek || !m.duration;
  seek.max = Math.max(1, Math.round(m.duration || 0));
  if (!seeking) seek.value = Math.round(m.position || 0);
  $("#media-pos").textContent = clock(seeking ? seek.value : m.position);
  $("#media-dur").textContent = clock(m.duration);
}

// Between polls the position advances on the page, one second at a time.
setInterval(() => {
  const m = state.media;
  if (!m || m.state !== "playing" || seeking) return;
  m.position = Math.min(m.duration || Infinity, (m.position || 0) + 1);
  renderMedia();
}, 1000);

async function mediaAction(action, value = 0) {
  if (!current) return;
  buzz();
  if (action === "toggle" && state.media) {
    state.media.state = state.media.state === "playing" ? "paused" : "playing"; // at once
    renderMedia();
  }
  try {
    const r = await api(`/api/tv/${current.mac}/media`, { action, value });
    if (r.media !== undefined) { state.media = r.media; renderMedia(); }
    refreshSoon(1200);
  } catch (e) {
    toast(e.message, true);
    refreshSoon(300);
  }
}

function bindSeek(seek) {
  seek.addEventListener("input", () => { seeking = true; $("#media-pos").textContent = clock(seek.value); });
  seek.addEventListener("change", async () => {
    if (state.media) state.media.position = Number(seek.value);
    await mediaAction("seek", Number(seek.value));
    seeking = false;
  });
}

// Soon after an action, so the page follows the TV without a reload.
let soonTimer;
function refreshSoon(ms = 700) {
  clearTimeout(soonTimer);
  soonTimer = setTimeout(refreshStatus, ms);
}
function startStatus() {
  stopStatus();
  refreshStatus();
  statusTimer = setInterval(() => { if (!document.hidden) refreshStatus(); }, 4000);
}
function stopStatus() {
  clearInterval(statusTimer);
  statusTimer = null;
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshStatus(); });

async function loadApps() {
  const box = $("#apps");
  box.replaceChildren();
  box.hidden = true;
  const apps = await api(`/api/tv/${current.mac}/apps`).catch(() => []);
  for (const app of apps) {
    box.append(el("button", { class: "btn", onclick: () => launch(app) }, app.name));
  }
  box.hidden = apps.length === 0;
  $("#yt").hidden = !apps.some((a) => a.name === "YouTube");
}

async function send(key) {
  if (!current) return;
  buzz();
  try {
    if (key.startsWith("KEY_VOL") && state.volume != null) {
      // Count on the page at once; the TV is read again after release.
      state.volume = Math.max(0, Math.min(100, state.volume + (key === "KEY_VOLUP" ? 1 : -1)));
      state.mute = false; // a volume key unmutes the TV
      render();
    }
    await api(`/api/tv/${current.mac}/key`, { key });
    if (key.startsWith("KEY_VOL")) refreshSoon(1200);
  } catch (e) {
    toast(e.message, true);
  }
}

async function launch(app) {
  buzz();
  try {
    await api(`/api/tv/${current.mac}/app`, { id: app.id });
    toast(`Opening ${app.name}`);
    refreshSoon(2500);
  } catch (e) {
    toast(e.message, true);
  }
}

// One click does the whole job; the server waits until the TV has changed.
async function power() {
  if (busy) return;
  buzz();
  busy = true;
  const btn = $("#power");
  btn.classList.add("busy");
  render();
  toast(state.power === "on" ? "Turning off…" : "Turning on…");
  try {
    state = await api(`/api/tv/${current.mac}/power`, {});
  } catch (e) {
    toast(e.message, true);
  } finally {
    busy = false;
    btn.classList.remove("busy");
    render();
    refreshSoon(1500);
  }
}

async function toggleMute() {
  buzz();
  try {
    Object.assign(state, await api(`/api/tv/${current.mac}/mute`, {}));
    render();
  } catch (e) {
    toast(e.message, true);
  }
}

// ------------------------------------------------------------ volume slider

function bindSlider(slider) {
  let timer;
  const sendLevel = async () => {
    try {
      Object.assign(state, await api(`/api/tv/${current.mac}/volume`, { level: Number(slider.value) }));
    } catch (e) {
      toast(e.message, true);
    }
  };
  slider.addEventListener("input", () => {
    sliding = true;
    state.volume = Number(slider.value);
    state.mute = false;
    render();
    clearTimeout(timer);
    timer = setTimeout(sendLevel, 150); // follow the finger, not every pixel
  });
  slider.addEventListener("change", async () => {
    clearTimeout(timer);
    await sendLevel();
    sliding = false;
    render();
  });
}

// ------------------------------------------------------------------ YouTube

const HISTORY_KEY = "tv-remote:yt-history";
const HISTORY_MAX = 30; // newest first; the oldest drops off

function history() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY)) || []; } catch { return []; }
}
function saveHistory(list) {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, HISTORY_MAX))); } catch {}
  renderHistory();
}
function rememberSearch(text) {
  saveHistory([text, ...history().filter((t) => t !== text)]);
}
function renderHistory() {
  const list = history();
  $("#yt-history").hidden = list.length === 0;
  $("#yt-history-list").replaceChildren(...list.map((t) =>
    el("button", { type: "button", class: "history-item", title: t, onclick: () => { $("#yt-text").value = t; syncClear(); sendYoutube(); } }, t)));
}
function syncClear() {
  $("#yt-clear").hidden = !$("#yt-text").value;
}

async function sendYoutube(e) {
  if (e) e.preventDefault();
  const input = $("#yt-text");
  const text = input.value.trim();
  if (!text) return;
  buzz();
  rememberSearch(text);
  toast("Sending to YouTube…");
  try {
    const r = await api(`/api/tv/${current.mac}/youtube`, { text });
    toast(r.did === "video" ? "Playing on the TV" : "Searching on the TV");
    input.value = "";
    syncClear();
    input.blur();
    refreshSoon(3000);
  } catch (err) {
    toast(err.message, true);
  }
}

// ---------------------------------------------------------------- scrolling

// The cursor protocol has no wheel. Up/Down scroll lists and menus; in the
// browser CH+/CH- scroll the page. Natural direction: drag up to read further down.
function scrollStep(down) {
  const browser = state.app === "Internet";
  send(browser ? (down ? "KEY_CHDOWN" : "KEY_CHUP") : (down ? "KEY_DOWN" : "KEY_UP"));
}

function bindScrollStrip(strip) {
  const STEP = 28; // finger pixels per scroll step
  let lastY = null, acc = 0;
  strip.addEventListener("pointerdown", (e) => {
    strip.setPointerCapture(e.pointerId);
    strip.classList.add("active");
    lastY = e.clientY; acc = 0;
  });
  strip.addEventListener("pointermove", (e) => {
    if (lastY === null) return;
    acc += lastY - e.clientY; // up is positive
    lastY = e.clientY;
    while (Math.abs(acc) >= STEP) {
      scrollStep(acc > 0);
      acc -= Math.sign(acc) * STEP;
    }
  });
  const end = () => { lastY = null; strip.classList.remove("active"); };
  strip.addEventListener("pointerup", end);
  strip.addEventListener("pointercancel", end);
}

// ----------------------------------------------------------------- touchpad

function setMode(mode) {
  document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.mode === mode)));
  $("#pad").hidden = mode !== "pad";
  $("#dpad").hidden = mode === "pad";
  try { localStorage.setItem("tv-remote:mode", mode); } catch {}
}

function bindPad(pad) {
  const SPEED = 1.6; // TV pixels per finger pixel
  const SCROLL_STEP = 28;
  let last = null, moved = 0, downAt = 0, dx = 0, dy = 0, sending = false;
  const fingers = new Map(); // pointerId -> y, for two-finger scrolling
  let scrollAcc = 0, scrolled = false;

  async function flush() {
    if (sending || (!dx && !dy)) return;
    const step = { dx: Math.round(dx), dy: Math.round(dy) };
    dx -= step.dx; dy -= step.dy;
    sending = true;
    try { await api(`/api/tv/${current.mac}/cursor`, step); } catch {}
    sending = false;
    if (dx || dy) flush();
  }

  pad.addEventListener("pointerdown", (e) => {
    pad.setPointerCapture(e.pointerId);
    pad.classList.add("active");
    fingers.set(e.pointerId, e.clientY);
    if (fingers.size === 2) { scrollAcc = 0; scrolled = true; return; }
    last = { x: e.clientX, y: e.clientY };
    moved = 0;
    scrolled = false;
    downAt = performance.now();
  });
  pad.addEventListener("pointermove", (e) => {
    if (fingers.size >= 2) {
      const prev = fingers.get(e.pointerId);
      if (prev === undefined) return;
      fingers.set(e.pointerId, e.clientY);
      scrollAcc += (prev - e.clientY) / 2; // two fingers each report the move
      while (Math.abs(scrollAcc) >= SCROLL_STEP) {
        scrollStep(scrollAcc > 0);
        scrollAcc -= Math.sign(scrollAcc) * SCROLL_STEP;
      }
      return;
    }
    if (!last) return;
    const mx = e.clientX - last.x, my = e.clientY - last.y;
    last = { x: e.clientX, y: e.clientY };
    moved += Math.abs(mx) + Math.abs(my);
    dx += mx * SPEED;
    dy += my * SPEED;
    flush();
  });
  const end = async (e) => {
    fingers.delete(e.pointerId);
    if (fingers.size) return;
    pad.classList.remove("active");
    if (!last) return;
    last = null;
    if (!scrolled && moved < 6 && performance.now() - downAt < 300) {
      buzz();
      try { await api(`/api/tv/${current.mac}/click`, {}); } catch (e) { toast(e.message, true); }
    }
  };
  pad.addEventListener("pointerup", end);
  pad.addEventListener("pointercancel", (e) => { fingers.delete(e.pointerId); last = null; pad.classList.remove("active"); });
}

// Press-and-hold repeats keys marked data-repeat (volume).
function bindKeys(root) {
  root.querySelectorAll("[data-key]").forEach((b) => {
    const key = b.dataset.key;
    if (!("repeat" in b.dataset)) {
      b.addEventListener("click", () => send(key));
      return;
    }
    let timer = null;
    const stop = () => {
      if (!timer) return;
      clearInterval(timer); timer = null; b.classList.remove("pressed");
      volHeld = false;
      refreshSoon(1200);
    };
    b.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      volHeld = true;
      b.classList.add("pressed");
      send(key);
      timer = setInterval(() => send(key), 250);
    });
    ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => b.addEventListener(ev, stop));
    b.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") send(key); });
  });
}

// Physical keyboard on a computer.
const HOTKEYS = {
  ArrowUp: "KEY_UP", ArrowDown: "KEY_DOWN", ArrowLeft: "KEY_LEFT", ArrowRight: "KEY_RIGHT",
  Enter: "KEY_ENTER", Backspace: "KEY_RETURN", Escape: "KEY_RETURN",
  h: "KEY_HOME", "+": "KEY_VOLUP", "=": "KEY_VOLUP", "-": "KEY_VOLDOWN",

};
document.addEventListener("keydown", (e) => {
  if ($("#view-remote").hidden || e.target.closest("input, textarea, dialog")) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const key = HOTKEYS[e.key];
  if (e.key === "m") { e.preventDefault(); return toggleMute(); }
  if (e.key === " ") { e.preventDefault(); return mediaAction("toggle"); }
  if (!key) return;
  e.preventDefault();
  send(key);
});

// ------------------------------------------------------------------- wiring

document.addEventListener("DOMContentLoaded", async () => {
  bindKeys(document);
  $("#scan").addEventListener("click", scan);
  $("#switch").addEventListener("click", () => { current = null; remember(null); showTvs(); });
  $("#power").addEventListener("click", power);
  $("#mute").addEventListener("click", toggleMute);
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  bindPad($("#pad-area"));
  bindScrollStrip($("#pad-scroll"));
  bindSlider($("#vol-slider"));
  $("#yt").addEventListener("submit", sendYoutube);
  $("#yt-text").addEventListener("input", syncClear);
  $("#yt-clear").addEventListener("click", () => { $("#yt-text").value = ""; syncClear(); $("#yt-text").focus(); });
  $("#yt-history-clear").addEventListener("click", () => { saveHistory([]); toast("History cleared"); });
  renderHistory();
  bindSeek($("#media-seek"));
  $("#media-toggle").addEventListener("click", () => mediaAction("toggle"));
  $("#playpause").addEventListener("click", () => mediaAction("toggle"));
  document.querySelectorAll("[data-media]").forEach((b) =>
    b.addEventListener("click", () => mediaAction(b.dataset.media, Number(b.dataset.value || 0))));
  // No copy menu on a long press over the controls.
  document.addEventListener("contextmenu", (e) => { if (e.target.closest("button, .pad, .dpad, .rocker")) e.preventDefault(); });
  let mode = "buttons";
  try { mode = localStorage.getItem("tv-remote:mode") || "buttons"; } catch {}
  setMode(mode);
  api("/api/info").then((i) => { $("#version").textContent = `TV Remote v${i.version}`; }).catch(() => {});
  $("#kb-toggle").addEventListener("click", () => {
    const kb = $("#kb");
    kb.hidden = !kb.hidden;
    if (!kb.hidden) $("#kb-text").focus();
  });
  $("#kb").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#kb-text");
    if (!input.value) return;
    try {
      await api(`/api/tv/${current.mac}/text`, { text: input.value });
      input.value = "";
    } catch (err) {
      toast(err.message, true);
    }
  });
  $("#kb-done").addEventListener("click", async () => {
    try {
      await api(`/api/tv/${current.mac}/enter-text`, {});
      $("#kb").hidden = true;
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("#login").addEventListener("submit", login);
  $("#logout").addEventListener("click", async (e) => {
    e.preventDefault();
    await api("/api/logout", {}).catch(() => {});
    showLogin();
  });
  start();
});
