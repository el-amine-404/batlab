"use strict";

// The sleep timer. It runs on lab2, so it works with the phone locked; the page
// only sets it and shows what is left.

function renderSleep() {
  const chip = $("#sleep-chip");
  const left = state.sleep_at ? state.sleep_at - Date.now() / 1000 : 0;
  chip.hidden = left <= 0;
  if (left > 0) {
    const m = Math.ceil(left / 60);
    chip.textContent = m >= 60 ? `Off in ${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}` : `Off in ${m} min`;
  }
  $("#sleep-open").classList.toggle("active", left > 0);
}

setInterval(renderSleep, 1000);

function openSleep() {
  buzz();
  $("#sleep-cancel").hidden = !state.sleep_at;
  $("#sleep").showModal();
}

async function setSleep(minutes) {
  buzz();
  $("#sleep").close();
  try {
    await api(`/api/tv/${current.mac}/sleep`, { minutes });
    state.sleep_at = minutes ? Date.now() / 1000 + minutes * 60 : undefined; // at once
    renderSleep();
    toast(minutes ? `Turning off in ${minutes} minutes` : "Sleep timer cancelled");
    refreshSoon(300);
  } catch (err) {
    toast(err.message, true);
  }
}

function bindSleep() {
  $("#sleep-open").addEventListener("click", openSleep);
  $("#sleep-chip").addEventListener("click", openSleep);
  $("#sleep-close").addEventListener("click", () => $("#sleep").close());
  $("#sleep-cancel").addEventListener("click", () => setSleep(0));
  document.querySelectorAll("#sleep [data-minutes]").forEach((b) =>
    b.addEventListener("click", () => setSleep(Number(b.dataset.minutes))));
  $("#sleep").addEventListener("click", (e) => { if (e.target === $("#sleep")) $("#sleep").close(); });
}
