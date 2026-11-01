"use strict";

// The voice sheet. The page records; lab2 recognises (compose/tv-remote/speech).

// ------------------------------------------------------------------- voice
// The page records and lab2 recognises (compose/tv-remote/speech): the same on
// every phone and browser, and it works inside a home-screen app, where iPhones
// offer no speech recognition of their own.

const SAMPLE_RATE = 16000;
const VOICE = { minSpeech: 0.2, endSilence: 0.8, noSpeech: 6, maxLength: 10 }; // seconds
const EXAMPLES = {
  en: ["Switch to HDMI 1", "Turn the volume down by 5", "Go to homepage", "Go to settings", "Open YouTube", "Pause"],
  fr: ["Passe en HDMI 1", "Baisse le volume de 5", "Accueil", "Ouvre les paramètres", "Ouvre Netflix", "Mets en pause"],
  ar: ["افتح يوتيوب", "ارفع الصوت", "وقف", "طفي التلفزيون", "شغل قرآن"],
};

let voiceLang = "auto";
let voiceRecent = "en";
try {
  voiceLang = localStorage.getItem("tv-remote:voice-lang") || "auto";
  voiceRecent = localStorage.getItem("tv-remote:voice-recent") || "en";
} catch {}
let voiceTarget = "command"; // "command", or the text field dictation goes to: "youtube", "keyboard"
let capture = null;           // the recording under way
let voiceRun = 0;             // a newer run makes an older answer irrelevant
let voiceCloseTimer = null;

function voiceState(name, title, detail = "") {
  $("#voice").dataset.state = name;
  $("#voice-title").textContent = title;
  $("#voice-detail").textContent = detail;
  $("#voice-mic-main").setAttribute("aria-label", name === "listening" ? "Finish speaking" : "Speak");
}

function renderExamples() {
  const lang = voiceLang === "auto" ? voiceRecent : voiceLang;
  $("#voice-examples").replaceChildren(...(EXAMPLES[lang] || EXAMPLES.en).map((text) =>
    el("button", { type: "button", class: "chip", dir: "auto", onclick: () => runCommand(text) }, text)));
}

function setVoiceLang(lang) {
  voiceLang = lang;
  try { localStorage.setItem("tv-remote:voice-lang", lang); } catch {}
  document.querySelectorAll("#voice-lang button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.lang === lang)));
  renderExamples();
}

function micProblem(err) {
  if (!window.isSecureContext || !navigator.mediaDevices) {
    return ["This phone doesn't trust the remote's certificate", "The microphone needs a trusted HTTPS page. Install the certificate from http://tv.homelab.lan/ca.crt, then open https://tv.homelab.lan."];
  }
  if (err && (err.name === "NotAllowedError" || err.name === "SecurityError")) {
    return ["Microphone is blocked", "On iPhone: Settings > Apps > Safari > Microphone > Ask. You can also type a command below."];
  }
  if (err && err.name === "NotFoundError") return ["No microphone found", "You can type a command below."];
  return ["The microphone didn't start", "Tap the microphone to try again, or type a command below."];
}

function setDuck(on) {
  if (current && state.power === "on") {
    api(`/api/tv/${current.mac}/listening`, { on }).catch(() => {});
  }
}

async function startCapture() {
  const run = ++voiceRun;
  clearTimeout(voiceCloseTimer);
  $("#voice-results").replaceChildren();
  $("#voice-results").hidden = true;
  $("#voice-heard").textContent = "";
  voiceState("starting", "Starting the microphone…");
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx || !navigator.mediaDevices || !window.isSecureContext) {
    return voiceState("error", ...micProblem());
  }
  // Created in the tap itself: an iPhone only lets sound start from a gesture.
  const ctx = new Ctx();
  ctx.resume().catch(() => {});
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    await ctx.resume();
    await ctx.audioWorklet.addModule($("#mic-worklet").href);
  } catch (err) {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    ctx.close().catch(() => {});
    if (run === voiceRun) voiceState("error", ...micProblem(err));
    return;
  }
  if (run !== voiceRun) { // closed while the permission was being asked
    stream.getTracks().forEach((t) => t.stop());
    ctx.close().catch(() => {});
    return;
  }

  const source = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "mic", { numberOfOutputs: 1 });
  source.connect(node);
  node.connect(ctx.destination); // silent: Safari only runs what reaches an output

  const c = capture = { ctx, stream, node, source, run, chunks: [], samples: 0, voiced: 0, quiet: 0, floor: 0.01, carry: 0, carryN: 0 };
  const ratio = ctx.sampleRate / SAMPLE_RATE;
  node.port.onmessage = (e) => {
    if (capture !== c) return;
    const input = e.data;
    const out = new Int16Array(Math.ceil(input.length / ratio) + 1);
    let n = 0, sum = 0;
    for (let i = 0; i < input.length; i++) {
      sum += input[i] * input[i];
      c.carry += input[i];
      c.carryN += 1;
      if (c.carryN >= ratio) {
        out[n++] = Math.max(-1, Math.min(1, c.carry / c.carryN)) * 32767;
        c.carry = 0;
        c.carryN -= ratio; // the fraction left carries over
        if (c.carryN < 0) c.carryN = 0;
      }
    }
    c.chunks.push(out.slice(0, n));
    c.samples += n;
    listenStep(c, Math.sqrt(sum / input.length), input.length / ctx.sampleRate);
  };
  setDuck(true);
  voiceState("listening", voiceTarget === "command" ? "Listening…" : "Say what to type…", "Tap the microphone when you've finished.");
  buzz(10);
}

// Ends the sentence by itself: a pause after speech. The threshold follows the
// room's level, so a TV playing nearby is not taken for someone speaking.
function listenStep(c, rms, seconds) {
  c.floor = rms < c.floor ? c.floor * 0.9 + rms * 0.1 : c.floor * 0.998 + rms * 0.002;
  const speaking = rms > Math.max(0.012, c.floor * 2.5);
  if (speaking) { c.voiced += seconds; c.quiet = 0; } else { c.quiet += seconds; }
  $("#voice-orb").style.setProperty("--level", Math.min(1, rms * 9).toFixed(3));
  const elapsed = c.samples / SAMPLE_RATE;
  if (c.voiced >= VOICE.minSpeech && c.quiet >= VOICE.endSilence) return finishCapture();
  if (elapsed >= VOICE.maxLength) return finishCapture();
  if (c.voiced < VOICE.minSpeech && elapsed >= VOICE.noSpeech) {
    stopCapture();
    setDuck(false);
    voiceState("error", "I didn't hear anything", "Tap the microphone and speak close to the phone.");
  }
}

function stopCapture() {
  const c = capture;
  capture = null;
  if (!c) return null;
  c.node.port.onmessage = null;
  try { c.source.disconnect(); c.node.disconnect(); } catch {}
  c.stream.getTracks().forEach((t) => t.stop()); // lets the iPhone's audio go back to normal
  c.ctx.close().catch(() => {});
  $("#voice-orb").style.setProperty("--level", 0);
  return c;
}

async function finishCapture() {
  const c = stopCapture();
  if (!c) return;
  const run = c.run;
  const pcm = new Int16Array(c.samples);
  let at = 0;
  for (const chunk of c.chunks) { pcm.set(chunk, at); at += chunk.length; }
  voiceState("thinking", "Working on it…");
  buzz(10);
  const target = voiceTarget;
  const query = new URLSearchParams({ language: voiceLang, recent: voiceRecent });
  if (target !== "command") query.set("mode", "dictate");
  try {
    const res = await fetch(`/api/tv/${current.mac}/voice?${query}`, {
      method: "POST", headers: { "Content-Type": "audio/l16", "X-Remote": "1" }, body: pcm.buffer,
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) { closeVoice(); showLogin(); return; }
    if (!res.ok) throw new Error(data.error || "something went wrong");
    if (run !== voiceRun) return;
    if (target !== "command") {
      setDuck(false);
      return dictated(data, target);
    }
    voiceAnswer(data);
  } catch (err) {
    setDuck(false);
    if (run === voiceRun) voiceState("error", "That didn't work", err.message === "Failed to fetch" ? "The remote can't reach the server. Try again." : capitalize(err.message));
  }
}

function capitalize(text) {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

function dictated(data, target) {
  if (!data.text) return voiceState("error", "I didn't catch that", "Tap the microphone and try again.");
  closeVoice();
  if (target === "youtube") {
    $("#yt-text").value = data.text;
    syncClear();
    sendYoutube();
  } else {
    $("#kb-text").value = data.text;
    $("#kb-text").focus();
  }
}

function voiceAnswer(data) {
  $("#voice-heard").textContent = data.text ? `“${data.text}”` : "";
  if (!data.text) return voiceState("error", "I didn't catch that", "Tap the microphone and try again.");
  if (!data.intent) {
    return voiceState("error", "That isn't a command I know", "Try one of these, or start with “play” or “search”.");
  }
  if (data.language) {
    voiceRecent = data.language;
    try { localStorage.setItem("tv-remote:voice-recent", voiceRecent); } catch {}
    renderExamples();
  }
  if (data.failed) return voiceState("error", data.say);
  if (data.detail) { // stays up long enough to read
    buzz([15, 40, 15]);
    voiceState("done", data.say, data.detail);
    voiceCloseTimer = setTimeout(closeVoice, 4000);
    return;
  }
  buzz([15, 40, 15]);
  voiceState("done", data.say);
  refreshSoon(data.intent.type === "power" || data.intent.type === "app" ? 2000 : 500);
  if (data.results) return showVoiceResults(data.results);
  voiceCloseTimer = setTimeout(closeVoice, 1300);
}

function showVoiceResults(results) {
  const box = $("#voice-results");
  if (!results.length) { voiceState("error", "No videos found"); return; }
  box.hidden = false;
  box.replaceChildren(...results.map((v) =>
    el("button", { type: "button", class: "result", onclick: () => { closeVoice(); playVideo(v.id, v.title); } },
      el("img", { src: `/api/youtube/thumb/${v.id}`, alt: "", loading: "lazy" }),
      el("div", { class: "meta" },
        el("b", { dir: "auto" }, v.title),
        el("span", { dir: "auto" }, [v.channel, v.length || "Live"].filter(Boolean).join(" · ")),
      ),
    )));
}

// A typed or tapped command: everything after the recording is the same.
async function runCommand(text) {
  const run = ++voiceRun;
  if (stopCapture()) setDuck(false);
  clearTimeout(voiceCloseTimer);
  $("#voice-results").hidden = true;
  $("#voice-heard").textContent = `“${text}”`;
  voiceState("thinking", "Working on it…");
  try {
    const data = await api(`/api/tv/${current.mac}/command`, { text });
    if (run === voiceRun) voiceAnswer(data);
  } catch (err) {
    if (run === voiceRun) voiceState("error", "That didn't work", capitalize(err.message));
  }
}

function openVoice(target = "command") {
  voiceTarget = target;
  const dlg = $("#voice");
  $("#voice").classList.toggle("dictating", target !== "command");
  setVoiceLang(voiceLang);
  $("#voice-text").value = "";
  if (!dlg.open) dlg.showModal();
  $("#voice-mic-main").focus();
  startCapture();
}

function closeVoice() {
  voiceRun++;
  clearTimeout(voiceCloseTimer);
  const wasListening = !!stopCapture();
  const dlg = $("#voice");
  const waiting = dlg.dataset.state === "thinking" && voiceTarget !== "command";
  if (wasListening || waiting) setDuck(false);
  if (dlg.open) dlg.close();
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden && capture) closeVoice(); // the phone was locked mid-sentence
});

function bindVoice() {
  $("#voice-open").addEventListener("click", () => openVoice("command"));
  $("#yt-voice").addEventListener("click", () => openVoice("youtube"));
  $("#kb-voice").addEventListener("click", () => openVoice("keyboard"));
  $("#voice-close").addEventListener("click", closeVoice);
  $("#voice-mic-main").addEventListener("click", () => {
    if (capture) finishCapture();
    else if ($("#voice").dataset.state !== "thinking" && $("#voice").dataset.state !== "starting") startCapture();
  });
  const dlg = $("#voice");
  dlg.addEventListener("click", (e) => { if (e.target === dlg) closeVoice(); });
  dlg.addEventListener("close", closeVoice);
  document.querySelectorAll("#voice-lang button").forEach((b) => b.addEventListener("click", () => setVoiceLang(b.dataset.lang)));
  $("#voice-type").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("#voice-text").value.trim();
    if (text) { $("#voice-text").value = ""; $("#voice-text").blur(); runCommand(text); }
  });
}
