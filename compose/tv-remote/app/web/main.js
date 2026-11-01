"use strict";

// Connects the page to the scripts above; loaded last.

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

  bindVoice();
  bindInputs();
  bindSleep();
  $("#offline-retry").addEventListener("click", () => { setOffline(false); start(); });
  $("#logout-all").addEventListener("click", async (e) => {
    e.preventDefault();
    if (!confirm("Log out every phone and computer using this remote?")) return;
    await api("/api/logout-all", {}).catch(() => {});
    showLogin();
  });
  // Fails harmlessly where the certificate is not trusted; the page just loads normally.
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});

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
