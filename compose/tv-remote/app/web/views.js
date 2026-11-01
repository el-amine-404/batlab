"use strict";

// Login, the TV list, pairing.

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
  $("#tv-model").textContent = "";
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
    const paired = await api("/api/tvs");
    const list = $("#tv-list");
    list.replaceChildren();
    // A paired TV stays listed when it is off: it can still be opened and woken.
    for (const tv of paired) {
      list.append(tvCard(tv, el("button", { class: "btn btn-primary", onclick: () => openRemote(tv) }, "Open")));
    }
    for (const tv of found.filter((t) => !t.paired)) {
      list.append(tvCard(tv, el("button", { class: "btn btn-primary", onclick: () => pair(tv) }, "Connect")));
    }
    const note = $("#scan-note");
    const newOnes = found.some((t) => !t.paired);
    note.hidden = newOnes;
    note.textContent = paired.length ? "No new TV found. A TV must be on to be found." : "No Samsung TV answered. Is it switched on and on this network?";
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
