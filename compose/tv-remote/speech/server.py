"""Speech to text for the TV remote, on this machine: no audio leaves the house.

POST /transcribe?language=en|fr|ar|auto with 16 kHz mono 16-bit PCM. Listens on
loopback only; the remote is the one client. One request at a time: a second
would only slow both on this CPU.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from faster_whisper import WhisperModel

PORT = int(os.environ.get("PORT", "8766"))
THREADS = int(os.environ.get("THREADS", "4"))
MAX_BYTES = 16000 * 2 * 30
LANGUAGES = ("en", "fr", "ar")
# The words a TV is told, so Whisper spells them right on a one-second clip.
PROMPTS = {
    "en": "Open YouTube, Netflix, Jellyfin, Spotify. Volume up, volume down, mute, pause, play, next, back, home. Turn off the TV.",
    "fr": "Ouvre YouTube, Netflix, Jellyfin, Spotify. Monte le son, baisse le volume, coupe le son, mets en pause, retour, accueil. Éteins la télé, allume la télé.",
    "ar": "افتح يوتيوب، نتفليكس، جيليفين، سبوتيفاي. ارفع الصوت، نقص الصوت، كتم الصوت، وقف، شغل، رجوع، الرئيسية. طفي التلفزيون، شعل التلفزيون.",
}

log = logging.getLogger("tv-remote-speech")
model = WhisperModel(os.environ.get("MODEL", "/model"), device="cpu", compute_type="int8", cpu_threads=THREADS)
lock = threading.Lock()


def transcribe(pcm: bytes, language: str) -> dict:
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
    with lock:
        started = time.monotonic()
        if language not in LANGUAGES:
            _, _, probs = model.detect_language(audio)
            language = max((p for p in probs if p[0] in LANGUAGES), key=lambda p: p[1])[0]
        segments, _ = model.transcribe(
            audio, language=language, beam_size=1, temperature=0, condition_on_previous_text=False,
            initial_prompt=PROMPTS[language],
            max_new_tokens=48, without_timestamps=True,
        )
        segments = list(segments)
    return {
        "text": "".join(s.text for s in segments).strip(),
        "language": language,
        "no_speech": max((s.no_speech_prob for s in segments), default=1.0),
        "logprob": min((s.avg_logprob for s in segments), default=-9.0),
        "ms": round((time.monotonic() - started) * 1000),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def _json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            return self._json(200, {"ok": True})
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if url.path != "/transcribe" or not 0 < length <= MAX_BYTES or length % 2:
            return self._json(400, {"error": "16 kHz mono PCM, at most 30 s"})
        language = urllib.parse.parse_qs(url.query).get("language", ["auto"])[0]
        result = transcribe(self.rfile.read(length), language)
        log.info("%.1fs of audio, %s, %d ms", length / 32000, result["language"], result["ms"])
        self._json(200, result)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    log.info("listening on 127.0.0.1:%d", PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
