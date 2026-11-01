"use strict";

// The YouTube card: search, results, history.

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
