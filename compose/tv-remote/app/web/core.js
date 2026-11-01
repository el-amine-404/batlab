"use strict";

// Shared by every script: the current TV, the API call, small page helpers.

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
