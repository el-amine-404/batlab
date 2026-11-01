"use strict";

// The remote itself: TV state, now playing, keys, power, volume.

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
  $("#power").classList.toggle("is-on", on);
  $("#power").setAttribute("aria-label", on ? "Turn off the TV" : "Turn on the TV");
  renderMedia();
  $("#tv-now").hidden = !(on && state.app);
  $("#tv-now").textContent = state.app ? `Now: ${state.app}` : "";
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
