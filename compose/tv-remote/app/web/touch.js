"use strict";

// Touchpad, scrolling, and physical keyboards.

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
    // Volume repeats quickly; moves wait first, so one tap is one step.
    const isVolume = key.startsWith("KEY_VOL");
    let timer = null, delay = null;
    const stop = () => {
      if (!timer && !delay) return;
      clearTimeout(delay); clearInterval(timer); timer = delay = null; b.classList.remove("pressed");
      if (isVolume) { volHeld = false; refreshSoon(1200); }
    };
    b.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      if (isVolume) volHeld = true;
      b.classList.add("pressed");
      send(key);
      delay = setTimeout(() => { timer = setInterval(() => send(key), isVolume ? 250 : 180); delay = null; }, isVolume ? 250 : 400);
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
