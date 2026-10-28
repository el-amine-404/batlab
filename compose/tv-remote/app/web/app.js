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

function buzz(pattern = 8) {
  if (navigator.vibrate) navigator.vibrate(pattern);
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
  mute.setAttribute("aria-label", on && state.mute ? "Unmute" : "Mute");
  mute.querySelector("use").setAttribute("href", silent ? "#i-mute" : "#i-volume");
  $("#vol-label").textContent = known ? `VOL ${state.volume}` : "VOL";
  $("#vol-num").textContent = known ? state.volume : "–";
  $("#vol-rocker").classList.toggle("muted", silent);
  $("#vol-row").classList.toggle("muted", silent);
  const slider = $("#vol-slider");
  slider.disabled = !known;
  if (known && !sliding) slider.value = state.volume;
  renderMedia();
  $("#tv-now").hidden = !(on && state.app);
  $("#tv-now").textContent = state.app ? `Now: ${state.app}` : "";

  // Update voice button state based on TV power
  const isOff = state.power === "off" || state.power === "standby";
  const voiceBtn = $("#voice-open");
  if (voiceBtn) {
    voiceBtn.setAttribute("aria-disabled", String(isOff));
    voiceBtn.title = isOff ? "TV is powered off" : "Voice control";
    voiceBtn.setAttribute("aria-label", isOff ? "TV is powered off" : "Voice control");
    const use = voiceBtn.querySelector("use");
    if (use) use.setAttribute("href", isOff ? "#i-mic-off" : "#i-mic");
  }

  // If TV powers off while voice modal is open, close it cleanly
  if (isOff) {
    const dlg = $("#voice");
    if (dlg && dlg.open) closeVoice();
  }

  updateVoiceTvDot();
}

async function refreshStatus() {
  if (!current || busy || volHeld || sliding) return;
  try {
    const s = await api(`/api/tv/${current.mac}/status`);
    const m = s.media;
    state = s;
    mergeMedia(m);
  } catch {
    state = {};
  }
  render();
}
// ------------------------------------------------------------- now playing

let seeking = false; // the timeline is being dragged
let pending = null;   // {state, until}: what a tap just asked for, until the TV says so

// The TV reports a change a moment after it happens; until then the page keeps
// what was asked for, instead of jumping back to the old state.
function mergeMedia(incoming) {
  if (incoming && pending && Date.now() < pending.until && incoming.state !== pending.state) {
    incoming.state = pending.state;
  } else {
    pending = null;
  }
  state.media = incoming;
}

function clock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(s).padStart(2, "0")}`;
}

function renderMedia() {
  const m = state.power === "on" ? state.media : null;
  const card = $("#media");
  card.hidden = !m;
  // One place for playback: the card when it shows, else the plain keys.
  $("#keys-row").hidden = !!m;
  if (!m) return;
  const playing = m.state === "playing";
  $("#media-title").textContent = m.title || "";
  $("#media-sub").textContent = m.subtitle || "";
  $("#media-source").textContent = m.source === "youtube" ? "YouTube" : "Jellyfin";
  // YouTube seeks exactly; Jellyfin's keys go back 10 s and forward 30 s.
  $("#seek-back").textContent = "−10s";
  $("#seek-fwd").textContent = m.source === "youtube" ? "+10s" : "+30s";
  $("#media-toggle use").setAttribute("href", playing ? "#i-pause" : "#i-play");
  document.querySelectorAll("#media .skip").forEach((b) => { b.hidden = !m.can_skip; });
  const seek = $("#media-seek");
  seek.disabled = !m.can_seek || !m.duration;
  seek.max = Math.max(1, Math.round(m.duration || 0));
  if (!seeking) seek.value = Math.round(m.position || 0);
  $("#media-pos").textContent = clock(seeking ? seek.value : m.position);
  $("#media-dur").textContent = clock(m.duration);
}

// While a card shows, its media is read every second (a light call), so play,
// pause and seeks show up at once; the full status stays at every 4 s.
setInterval(async () => {
  if (!current || document.hidden || !state.media || seeking || busy) return;
  try {
    const r = await api(`/api/tv/${current.mac}/media`);
    mergeMedia(r.media);
    renderMedia();
  } catch {}
}, 1000);

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
    pending = { state: state.media.state, until: Date.now() + 3000 };
    renderMedia();
  }
  try {
    const r = await api(`/api/tv/${current.mac}/media`, { action, value });
    if (r.media !== undefined) { mergeMedia(r.media); renderMedia(); }
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

// The same test as the server's: a link, or a bare ID that looks generated.
const YT_URL_ID = /(?:v=|youtu\.be\/|\/shorts\/|\/live\/)([A-Za-z0-9_-]{11})(?:[?&#]|$)/;
const YT_BARE_ID = /^(?=.*(?:[0-9_-]|[a-z].*[A-Z]))[A-Za-z0-9_-]{11}$/;
function videoId(text) {
  const m = text.match(YT_URL_ID);
  return m ? m[1] : (YT_BARE_ID.test(text) ? text : null);
}

async function playVideo(id, title) {
  buzz();
  toast(title ? `Playing: ${title}` : "Sending to YouTube…");
  try {
    await api(`/api/tv/${current.mac}/youtube`, { text: id });
    toast("Playing on the TV");
    refreshSoon(2500);
  } catch (err) {
    toast(err.message, true);
  }
}

async function sendYoutube(e) {
  if (e) e.preventDefault();
  const input = $("#yt-text");
  const text = input.value.trim();
  if (!text) return;
  rememberSearch(text);
  input.blur();
  const id = videoId(text);
  if (id) {
    $("#yt-results").hidden = true;
    await playVideo(id);
    input.value = "";
    syncClear();
    return;
  }
  // A search: results come from lab2; tapping one plays it on the TV.
  const box = $("#yt-results");
  box.hidden = false;
  box.replaceChildren(el("p", { class: "small muted" }, "Searching…"));
  try {
    const results = await api(`/api/youtube/search?q=${encodeURIComponent(text)}`);
    if (!results.length) {
      box.replaceChildren(el("p", { class: "small muted" }, "No videos found."));
      return;
    }
    box.replaceChildren(...results.map((v) =>
      el("button", { type: "button", class: "result", onclick: () => playVideo(v.id, v.title) },
        el("img", { src: `/api/youtube/thumb/${v.id}`, alt: "", loading: "lazy" }),
        el("div", { class: "meta" },
          el("b", {}, v.title),
          el("span", {}, [v.channel, v.length || "Live"].filter(Boolean).join(" · ")),
        ),
      )));
  } catch (err) {
    box.replaceChildren(el("p", { class: "small muted" }, err.message));
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

// ------------------------------------------------------------------- voice

let recognition = null;
let voiceActive = false;
let startWatchdog = null;
let silenceTimer = null;
let voiceLang = "auto";
try { voiceLang = localStorage.getItem("tv-remote:voice-lang") || "auto"; } catch {}
let voiceTarget = "assistant"; // "assistant" | "youtube" | "keyboard"
let pendingVoiceIntent = null;
let voiceCloseTimer = null;

function updateVoiceTvDot() {
  const dot = $("#voice-tv-dot");
  if (!dot) return;
  const on = state.power === "on";
  dot.className = "dot " + (busy ? "busy" : on ? "on" : state.power ? "off" : "");
  dot.title = busy ? "Working…" : on ? "TV is on" : "TV is off";
}

function normalizeVoiceText(str) {
  return (str || "")
    .toLowerCase()
    .replace(/[\.,\/#!$%\^&\*;:{}=\-_`~()?]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function parseVoiceIntent(rawText, target = voiceTarget) {
  const text = normalizeVoiceText(rawText);
  if (!text) return null;

  // 1. App Launching (YouTube, Netflix, Jellyfin, Spotify, Prime Video, Disney+, Internet)
  const appKeywords = [
    { id: "111299001912", name: "YouTube", match: /^(?:(?:go\s+to|open|launch|start|switch\s+to|play|ouvre|ouvrez|lance|lancer|mets|mettre|va\s+sur|افتح|شغل|حط|روح)\s+)?(?:ال|ل|لل)?(?:you\s*tube|يوتيوب)(?:\s+(?:app|application|تطبيق))?$/i },
    { id: "3201907018807", name: "Netflix", match: /^(?:(?:go\s+to|open|launch|start|switch\s+to|ouvre|ouvrez|lance|lancer|mets|mettre|va\s+sur|افتح|شغل|حط|روح)\s+)?(?:ال|ل|لل)?(?:net\s*flix|نتفليكس|نتفلكس)(?:\s+(?:app|application|تطبيق))?$/i },
    { id: "AprZAARz4r.Jellyfin", name: "Jellyfin", match: /^(?:(?:go\s+to|open|launch|start|switch\s+to|ouvre|ouvrez|lance|lancer|mets|mettre|va\s+sur|افتح|شغل|حط|روح)\s+)?(?:ال|ل|لل)?(?:jelly\s*fin|جيليفين)(?:\s+(?:app|application|تطبيق))?$/i },
    { id: "3201606009684", name: "Spotify", match: /^(?:(?:go\s+to|open|launch|start|switch\s+to|ouvre|ouvrez|lance|lancer|mets|mettre|va\s+sur|افتح|شغل|حط|روح)\s+)?(?:ال|ل|لل)?(?:spotify|سبوتيفاي)(?:\s+(?:app|application|تطبيق))?$/i },
    { id: "3201910019365", name: "Prime Video", match: /^(?:(?:go\s+to|open|launch|start|switch\s+to|ouvre|ouvrez|lance|lancer|mets|mettre|va\s+sur|افتح|شغل|حط|روح)\s+)?(?:ال|ل|لل)?(?:prime\s*video|amazon\s*prime|برايم)(?:\s+(?:app|application|تطبيق))?$/i },
    { id: "3201901017640", name: "Disney+", match: /^(?:(?:go\s+to|open|launch|start|switch\s+to|ouvre|ouvrez|lance|lancer|mets|mettre|va\s+sur|افتح|شغل|حط|روح)\s+)?(?:ال|ل|لل)?(?:disney\s*(?:plus|\+)?|ديزني)(?:\s+(?:app|application|تطبيق))?$/i },
    { id: "3202010022079", name: "Internet", match: /^(?:(?:go\s+to|open|launch|start|switch\s+to|ouvre|ouvrez|lance|lancer|mets|mettre|va\s+sur|افتح|شغل|حط|روح)\s+)?(?:ال|ل|لل)?(?:internet|browser|navigateur|المتصفح|انترنت)(?:\s+(?:app|application|تطبيق))?$/i },
  ];

  for (const a of appKeywords) {
    if (a.match.test(text)) {
      const isOff = state.power !== "on";
      return {
        type: "app",
        app: { id: a.id, name: a.name },
        label: isOff ? `⚡ Wake TV & Open ${a.name}` : `▶ Launch ${a.name}`,
      };
    }
  }

  // 2. Power Toggle / Off / On
  if (/^(?:turn\s+off|power\s+off|shut\s+down|switch\s+off|éteins|éteindre|arrête|arrêter|coupe|couper|طفي|اطفي|اقفل|إغلاق)(?:\s+(?:the|la|le|l')?\s*(?:tv|télé|téléviseur|التلفزيون|التلفاز|الشاشة))?$/i.test(text)) {
    return { type: "power", action: "off", label: "⏻ Turn Off TV" };
  }
  if (/^(?:turn\s+on|power\s+on|wake\s+up|allume|allumer|démarre|démarrer|شغل|ولع)(?:\s+(?:the|la|le|l')?\s*(?:tv|télé|téléviseur|التلفزيون|التلفاز|الشاشة))?$/i.test(text)) {
    return { type: "power", action: "on", label: "⏻ Turn On TV" };
  }

  // 3. Volume Up
  if (/^(?:volume\s*up|turn\s*(?:it\s*)?up|louder|increase\s+volume|sound\s*up|monte(?:r)?(?:\s+(?:le\s+)?(?:son|volume))?|augmente(?:r)?(?:\s+(?:le\s+)?(?:son|volume))?|plus\s+fort|un\s+peu\s+plus\s+fort|علي\s+الصوت|علّي|علّي\s+الصوت|ارفع\s+الصوت|صوت\s+اعلى|زيد\s+الصوت)$/i.test(text)) {
    return { type: "volume", dir: 1, label: "🔊 Volume Up" };
  }

  // 4. Volume Down
  if (/^(?:volume\s*down|turn\s*(?:it\s*)?down|quieter|decrease\s+volume|sound\s*down|lower\s+volume|baisse(?:r)?(?:\s+(?:le\s+)?(?:son|volume))?|diminue(?:r)?(?:\s+(?:le\s+)?(?:son|volume))?|moins\s+fort|un\s+peu\s+moins\s+fort|وطي\s+الصوت|وطّي|وطّي\s+الصوت|اخفض\s+الصوت|نزل\s+الصوت|صوت\s+اوطى|نقص\s+الصوت)$/i.test(text)) {
    return { type: "volume", dir: -1, label: "🔉 Volume Down" };
  }

  // 5. Mute / Unmute
  if (/^(?:mute|unmute|silence|coupe(?:r)?\s+(?:le\s+)?son|mets?\s+en\s+sourdine|mettre\s+en\s+sourdine|sourdine|remets?\s+(?:le\s+)?son|remettre\s+(?:le\s+)?son|démute|كتم|كتم\s+الصوت|صامت|الغاء\s+الكتم|فك\s+الكتم)$/i.test(text)) {
    return { type: "mute", label: "🔇 Toggle Mute" };
  }

  // 6. Play / Pause
  if (/^(?:pause|freeze|stop|mets?\s+en\s+pause|mettre\s+en\s+pause|arrêt|وقف|ايقاف\s+مؤقت|لحظة)$/i.test(text)) {
    return { type: "media", action: "pause", label: "⏸ Pause" };
  }
  if (/^(?:play|resume|continue|continuer|lecture|reprends|reprendre|relance|relancer|شغل|استئناف|كمل)$/i.test(text)) {
    return { type: "media", action: "play", label: "▶ Play" };
  }

  // 7. Navigation
  if (/^(?:home|go\s+home|main\s+menu|accueil|page\s+d'accueil|الرئيسية|الصفحة\s+الرئيسية)$/i.test(text)) {
    return { type: "key", key: "KEY_HOME", label: "⌂ Home" };
  }
  if (/^(?:back|go\s+back|return|retour|en\s+arrière|رجوع|ارجع|للخلف)$/i.test(text)) {
    return { type: "key", key: "KEY_RETURN", label: "↩ Back" };
  }
  if (/^(?:menu|settings|paramètres|réglages|القائمة|الإعدادات)$/i.test(text)) {
    return { type: "key", key: "KEY_MENU", label: "☰ Menu" };
  }
  if (/^(?:input|source|switch\s+input|changer\s+de\s+source|المصدر|المدخل)$/i.test(text)) {
    return { type: "key", key: "KEY_SOURCE", label: "⎘ Source" };
  }

  // 8. Search query stripping: "search for xyz", "cherche xyz", "ابحث عن xyz"
  const searchMatch = rawText.trim().match(/^(?:search(?:\s+for)?|find|cherche|recherche|trouve|ابحث\s+عن|دور\s+على|بحث)\s+(.+)$/i);
  if (searchMatch) {
    const query = searchMatch[1].trim();
    return { type: "search", query, label: `🔍 Search: "${query}"` };
  }

  // 9. Contextual targets
  if (target === "youtube") {
    return { type: "search", query: rawText.trim(), label: `🔍 YouTube: "${rawText.trim()}"` };
  }
  if (target === "keyboard") {
    return { type: "search", query: rawText.trim(), label: `⌨ Type: "${rawText.trim()}"` };
  }

  // General assistant unrecognized command
  return { type: "unknown", raw: rawText.trim(), label: "Say an app, volume, or power command" };
}

async function executeVoiceIntent(intent, target) {
  if (!intent) return;
  buzz([15, 40, 15]);

  const isTvOn = state.power === "on";

  // Handle app launch: if TV is off, wake the TV first then launch!
  if (intent.type === "app") {
    if (!isTvOn) {
      $("#voice-title").textContent = `Waking TV for ${intent.app.name}…`;
      toast(`Waking TV and opening ${intent.app.name}…`);
      closeVoice();
      try {
        await power(); // wakes TV via WOL & power toggle
        await new Promise((r) => setTimeout(r, 1500));
        await launch(intent.app);
      } catch (err) {
        toast(err.message, true);
      }
      return;
    }
    closeVoice();
    await launch(intent.app);
    return;
  }

  // Handle power commands
  if (intent.type === "power") {
    if (intent.action === "off" && !isTvOn) {
      toast("TV is already off");
      closeVoice();
      return;
    }
    if (intent.action === "on" && isTvOn) {
      toast("TV is already on");
      closeVoice();
      return;
    }
    closeVoice();
    await power();
    return;
  }

  // Other TV actions require TV to be ON
  if (!isTvOn) {
    closeVoice();
    toast("TV is powered off. Turn it on first.");
    return;
  }

  closeVoice();

  if (intent.type === "volume") {
    await send(intent.dir > 0 ? "KEY_VOLUP" : "KEY_VOLDOWN");
    toast(intent.dir > 0 ? "Volume Up" : "Volume Down");
    return;
  }
  if (intent.type === "mute") {
    await toggleMute();
    toast("Mute toggled");
    return;
  }
  if (intent.type === "media") {
    await mediaAction(intent.action);
    toast(intent.action === "pause" ? "Paused" : "Playing");
    return;
  }
  if (intent.type === "key") {
    await send(intent.key);
    toast(intent.label);
    return;
  }
  if (intent.type === "search") {
    if (target === "youtube" || state.app === "YouTube") {
      const input = $("#yt-text");
      input.value = intent.query;
      syncClear();
      await sendYoutube();
      toast(`YouTube: "${intent.query}"`);
    } else if (target === "keyboard" || !$("#kb").hidden) {
      if (current) {
        await api(`/api/tv/${current.mac}/text`, { text: intent.query }).catch(() => {});
        toast(`Typed: "${intent.query}"`);
      }
    } else {
      const input = $("#yt-text");
      input.value = intent.query;
      syncClear();
      await sendYoutube();
      toast(`YouTube: "${intent.query}"`);
    }
  }
}

// Tears down speech recognition and explicitly forces WebKit/iOS to release the microphone audio lock
function teardownVoice() {
  clearTimeout(voiceCloseTimer);
  clearTimeout(startWatchdog);
  clearTimeout(silenceTimer);
  silenceTimer = null;
  if (recognition) {
    const r = recognition;
    recognition = null;
    r.onstart = null;
    r.onresult = null;
    r.onerror = null;
    r.onspeechend = null;
    r.onend = null;
    try {
      r.abort();
    } catch {}
  }
  voiceActive = false;
  const ring = $("#voice-ring");
  const btn = $("#voice-toggle-btn");
  if (ring) ring.classList.remove("active");
  if (btn) btn.classList.remove("listening");
}

function stopListening() {
  teardownVoice();
  const title = $("#voice-title");
  if (title) title.textContent = "Tap mic to speak";
}

function setVoiceLang(lang) {
  voiceLang = lang;
  try { localStorage.setItem("tv-remote:voice-lang", lang); } catch {}
  document.querySelectorAll(".voice-lang-picker .lang-pill").forEach((b) => {
    b.setAttribute("aria-checked", String(b.dataset.lang === lang));
  });
  if (voiceActive) {
    teardownVoice();
    startListening();
  }
}

function openVoice(target = "assistant") {
  const isOff = state.power === "off" || state.power === "standby";
  if (isOff) {
    toast("TV is powered off. Turn it on first.");
    buzz([20, 30, 20]);
    return;
  }
  voiceTarget = target;
  teardownVoice();
  pendingVoiceIntent = null;

  const dlg = $("#voice");
  const title = $("#voice-title");
  const transcript = $("#voice-transcript");
  const heardLabel = $("#voice-heard-label");
  const badge = $("#voice-action-badge");
  const btn = $("#voice-toggle-btn");

  updateVoiceTvDot();

  const SpeechClass = window.SpeechRecognition || window.webkitSpeechRecognition;

  title.textContent = "Connecting…";
  transcript.textContent = target === "youtube" ? "Say what you want to watch…" : "Say an app, command, or search…";
  transcript.className = "voice-transcript";
  heardLabel.hidden = true;
  badge.hidden = true;
  btn.disabled = false;

  setVoiceLang(voiceLang);
  if (!dlg.open) dlg.showModal();

  if (!SpeechClass) {
    const isStandalone = ("standalone" in navigator && navigator.standalone) ||
                         window.matchMedia("(display-mode: standalone)").matches;
    title.textContent = "Voice Assistant";
    transcript.textContent = isStandalone
      ? "Apple restricts speech recognition inside Home Screen apps. Open tv.homelab.lan directly in Safari to use your microphone."
      : "Speech recognition is supported in Chrome, iOS Safari, and Android. In this browser, dictate directly using your keyboard mic.";
    badge.hidden = false;
    badge.className = "voice-badge error";
    badge.textContent = isStandalone ? "Open in Safari for voice" : "Speech API unavailable";
    btn.disabled = true;
    return;
  }

  startListening();
}

function startListening() {
  const SpeechClass = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechClass) return;

  teardownVoice();

  const ring = $("#voice-ring");
  const btn = $("#voice-toggle-btn");
  const title = $("#voice-title");
  const transcript = $("#voice-transcript");
  const heardLabel = $("#voice-heard-label");
  const badge = $("#voice-action-badge");

  title.textContent = "Connecting…";
  transcript.textContent = "Say an app, command, or search…";
  transcript.className = "voice-transcript";
  heardLabel.hidden = true;
  badge.hidden = true;

  try {
    recognition = new SpeechClass();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    let lang = voiceLang === "auto" ? (navigator.language || "en-US") : voiceLang;
    if (lang.startsWith("fr")) lang = "fr-FR";
    else if (lang.startsWith("ar")) lang = "ar-SA";
    else if (lang.startsWith("en")) lang = "en-US";
    recognition.lang = lang;

    recognition.onstart = () => {
      clearTimeout(startWatchdog);
      voiceActive = true;
      ring.classList.add("active");
      btn.classList.add("listening");
      title.textContent = "Listening… speak now";
      heardLabel.hidden = true;
      badge.hidden = true;
      buzz(10);
    };

    recognition.onresult = (event) => {
      let interim = "";
      let final = "";

      for (let i = event.resultIndex; i < event.results.length; ++i) {
        const part = event.results[i][0].transcript;
        if (event.results[i].isFinal) final += part;
        else interim += part;
      }

      const spoken = (final || interim).trim();
      if (spoken) {
        heardLabel.hidden = false;
        transcript.textContent = `"${spoken}"`;
        transcript.className = "voice-transcript" + (final ? "" : " interim");

        const intent = parseVoiceIntent(spoken);
        if (intent && intent.type !== "unknown") {
          badge.hidden = false;
          badge.className = "voice-badge";
          badge.textContent = intent.label;
          pendingVoiceIntent = intent; // Store intent even if isFinal is false (critical for iOS WebKit!)

          // Auto-execute after 1s of pause in speech
          clearTimeout(silenceTimer);
          silenceTimer = setTimeout(() => {
            if (pendingVoiceIntent && voiceActive) {
              const toRun = pendingVoiceIntent;
              const targetRun = voiceTarget;
              pendingVoiceIntent = null;
              title.textContent = "Executing…";
              teardownVoice();
              executeVoiceIntent(toRun, targetRun);
            }
          }, 1000);
        } else if (intent && intent.type === "unknown") {
          badge.hidden = false;
          badge.className = "voice-badge muted";
          badge.textContent = intent.label;
          pendingVoiceIntent = null;
          clearTimeout(silenceTimer);
        }
      }
    };

    recognition.onspeechend = () => {
      title.textContent = "Processing…";
      if (pendingVoiceIntent) {
        clearTimeout(silenceTimer);
        silenceTimer = setTimeout(() => {
          if (pendingVoiceIntent && voiceActive) {
            const toRun = pendingVoiceIntent;
            const targetRun = voiceTarget;
            pendingVoiceIntent = null;
            title.textContent = "Executing…";
            teardownVoice();
            executeVoiceIntent(toRun, targetRun);
          }
        }, 400);
      }
    };

    recognition.onerror = (event) => {
      clearTimeout(startWatchdog);
      clearTimeout(silenceTimer);
      const isAborted = event.error === "aborted";
      teardownVoice();
      buzz([30, 40, 30]);

      if (isAborted) {
        title.textContent = "Tap mic to speak";
        transcript.textContent = "Tap the microphone orb to speak.";
        return;
      }

      title.textContent = "Voice Assistant";
      badge.hidden = false;
      badge.className = "voice-badge error";
      heardLabel.hidden = true;

      const isStandalone = ("standalone" in navigator && navigator.standalone) ||
                           window.matchMedia("(display-mode: standalone)").matches;

      if (event.error === "not-allowed") {
        badge.textContent = "Microphone blocked";
        transcript.textContent = isStandalone
          ? "Microphone access blocked. Apple restricts voice in Home Screen web apps. Please open in Safari directly."
          : "Microphone blocked. Please allow microphone access in Safari settings.";
      } else if (event.error === "service-not-allowed") {
        badge.textContent = "Dictation disabled";
        transcript.textContent = "Please enable Dictation in iPhone Settings > General > Keyboard > Enable Dictation.";
      } else if (event.error === "no-speech") {
        badge.textContent = "No speech detected";
        transcript.textContent = "Didn't hear anything. Speak closer or tap the microphone orb to try again.";
      } else {
        badge.textContent = `Notice: ${event.error}`;
        transcript.textContent = "Tap the microphone orb to try speaking again.";
      }
    };

    recognition.onend = () => {
      clearTimeout(startWatchdog);
      clearTimeout(silenceTimer);
      const intentToRun = pendingVoiceIntent;
      const targetToRun = voiceTarget;
      pendingVoiceIntent = null;
      teardownVoice();

      if (intentToRun) {
        title.textContent = "Executing…";
        clearTimeout(voiceCloseTimer);
        voiceCloseTimer = setTimeout(async () => {
          await executeVoiceIntent(intentToRun, targetToRun);
        }, 300);
      } else {
        title.textContent = "Tap mic to speak";
      }
    };

    recognition.start();

    // Guard watchdog: if onstart does not fire within 4s, reset cleanly
    clearTimeout(startWatchdog);
    startWatchdog = setTimeout(() => {
      if (!voiceActive) {
        teardownVoice();
        title.textContent = "Tap mic to speak";
        transcript.textContent = "Ready. Tap the microphone orb to speak.";
      }
    }, 4000);
  } catch (err) {
    clearTimeout(startWatchdog);
    teardownVoice();
    title.textContent = "Voice Assistant";
    transcript.textContent = "Tap the microphone orb to speak.";
  }
}

function closeVoice() {
  clearTimeout(voiceCloseTimer);
  teardownVoice();
  const dlg = $("#voice");
  if (dlg && dlg.open) dlg.close();
}

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

  // Voice assistant bindings
  $("#voice-open").addEventListener("click", () => openVoice("assistant"));
  $("#yt-voice").addEventListener("click", () => openVoice("youtube"));
  $("#kb-voice").addEventListener("click", () => openVoice("keyboard"));
  $("#voice-close").addEventListener("click", closeVoice);
  $("#voice-toggle-btn").addEventListener("click", () => {
    if (voiceActive) {
      if (pendingVoiceIntent) {
        clearTimeout(silenceTimer);
        const toRun = pendingVoiceIntent;
        const targetRun = voiceTarget;
        pendingVoiceIntent = null;
        title.textContent = "Executing…";
        teardownVoice();
        executeVoiceIntent(toRun, targetRun);
        return;
      }
      stopListening();
    } else {
      startListening();
    }
  });
  $("#voice").addEventListener("click", (e) => { if (e.target === $("#voice")) closeVoice(); });
  const voiceDlg = $("#voice");
  if (voiceDlg) voiceDlg.addEventListener("close", closeVoice);
  document.querySelectorAll(".voice-lang-picker .lang-pill").forEach((b) =>
    b.addEventListener("click", () => setVoiceLang(b.dataset.lang)));

  $("#yt").addEventListener("submit", sendYoutube);
  $("#yt-text").addEventListener("input", syncClear);
  $("#yt-clear").addEventListener("click", () => {
    $("#yt-text").value = ""; syncClear(); $("#yt-results").hidden = true; $("#yt-text").focus();
  });
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
    if (state.app === "YouTube" && !$("#yt").hidden) {
      // YouTube ignores text sent to the TV's keyboard; its box searches instead.
      $("#yt").scrollIntoView({ behavior: "smooth", block: "center" });
      $("#yt-text").focus();
      toast("YouTube has its own keyboard: type here");
      return;
    }
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
