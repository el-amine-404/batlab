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
  $("#view-tvs").hidden = view !== "tvs";
  $("#view-remote").hidden = view !== "remote";
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

async function refreshStatus() {
  if (!current) return;
  const dot = $("#status-dot");
  try {
    const s = await api(`/api/tv/${current.mac}/status`);
    const on = s.online && s.power === "on";
    dot.className = "dot " + (on ? "on" : "off");
    dot.title = on ? "on" : "off or asleep";
  } catch {
    dot.className = "dot";
  }
}
function startStatus() {
  stopStatus();
  refreshStatus();
  statusTimer = setInterval(refreshStatus, 10000);
}
function stopStatus() {
  clearInterval(statusTimer);
  statusTimer = null;
}

async function loadApps() {
  const box = $("#apps");
  box.replaceChildren();
  box.hidden = true;
  const apps = await api(`/api/tv/${current.mac}/apps`).catch(() => []);
  for (const app of apps) {
    box.append(el("button", { class: "btn", onclick: () => launch(app) }, app.name));
  }
  box.hidden = apps.length === 0;
}

async function send(key) {
  if (!current) return;
  buzz();
  try {
    await api(`/api/tv/${current.mac}/key`, { key });
  } catch (e) {
    toast(e.message, true);
  }
}

async function launch(app) {
  buzz();
  try {
    await api(`/api/tv/${current.mac}/app`, { id: app.id });
    toast(`Opening ${app.name}`);
  } catch (e) {
    toast(e.message, true);
  }
}

async function power() {
  buzz();
  const dot = $("#status-dot");
  if (dot.classList.contains("on")) return send("KEY_POWER");
  // Off or asleep: the control channel is closed, so wake it over the network.
  try {
    await api(`/api/tv/${current.mac}/wake`, {});
    toast("Waking the TV…");
    setTimeout(refreshStatus, 6000);
  } catch (e) {
    toast(e.message, true);
  }
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
    const stop = () => { clearInterval(timer); timer = null; b.classList.remove("pressed"); };
    b.addEventListener("pointerdown", (e) => {
      e.preventDefault();
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
  h: "KEY_HOME", m: "KEY_MUTE", "+": "KEY_VOLUP", "=": "KEY_VOLUP", "-": "KEY_VOLDOWN",
  " ": "KEY_PLAY",
};
document.addEventListener("keydown", (e) => {
  if ($("#view-remote").hidden || e.target.closest("input, textarea, dialog")) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const key = HOTKEYS[e.key];
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

  const tvs = await api("/api/tvs").catch(() => []);
  const saved = tvs.find((t) => t.mac === remembered()) || (tvs.length === 1 ? tvs[0] : null);
  saved ? openRemote(saved) : showTvs();
});
