"""From a recording to words and a command, through tv-remote-speech."""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request

import voice
from config import SPEECH_URL

log = logging.getLogger("tv-remote")

VOICE_MAX_BYTES = 16000 * 2 * 30  # 30 s of 16 kHz mono 16-bit
VOICE_LANGUAGES = ("en", "fr", "ar")


def hear(pcm: bytes, language: str) -> dict:
    url = f"{SPEECH_URL}/transcribe?language={language}"
    req = urllib.request.Request(url, data=pcm, method="POST", headers={"Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except (OSError, ValueError, urllib.error.URLError) as err:
        raise ConnectionError("speech recognition is not answering") from err


def heard_speech(result: dict) -> bool:
    """Whisper writes words even for noise; these two numbers tell them apart."""
    return bool(result["text"]) and result["no_speech"] < 0.7 and result["logprob"] > -1.6


def understand(pcm: bytes, language: str, order: list[str], inputs: list[dict] | tuple = ()) -> dict:
    """The sentence and its command. Whisper's own language detection fails on
    a two-word command, so with "auto" the most recent language is tried first.

    If that gives no command, every other language is heard and the most
    confident one wins, not the first to match: given the wrong language, Whisper
    can repeat its vocabulary prompt, which is full of real commands (Arabic
    "raise the sound" came back from French as "éteins la télé").
    """
    if language in VOICE_LANGUAGES:
        order = [language]
    heard = [r for r in (hear(pcm, order[0]),) if heard_speech(r)]
    if heard and (intent := voice.parse(heard[0]["text"], inputs)):
        return {**heard[0], "intent": intent}
    heard += [r for r in map(lambda lang: hear(pcm, lang), order[1:]) if heard_speech(r)]
    if not heard:
        return {"text": "", "intent": None}
    best = max(heard, key=lambda r: r["logprob"])
    return {**best, "intent": voice.parse(best["text"], inputs)}


class Duck:
    """The TV lowered while listening, as voice remotes do: a phone near a loud
    TV hears the film. Comes back when listening ends, whatever the page does."""

    HOLD = 20  # seconds, if the page vanishes (phone locked, tab closed)
    QUIET = 0.35

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.saved: dict[str, tuple] = {}
        self.timers: dict[str, threading.Timer] = {}

    def lower(self, mac: str, tv) -> None:
        with self.lock:
            if mac in self.saved:
                self.timers[mac].cancel()
            else:
                level = tv.audio()
                if level["volume"] is None or level["mute"] or level["volume"] <= 8:
                    return
                self.saved[mac] = (tv, level["volume"])
                tv.set_volume(max(4, round(level["volume"] * self.QUIET)))
            timer = threading.Timer(self.HOLD, self.restore, args=(mac,))
            timer.daemon = True
            self.timers[mac] = timer
            timer.start()

    def restore(self, mac: str) -> None:
        with self.lock:
            saved = self.saved.pop(mac, None)
            timer = self.timers.pop(mac, None)
            if timer:
                timer.cancel()
            if saved:
                tv, level = saved
                try:
                    tv.set_volume(level)
                except (OSError, urllib.error.URLError) as err:
                    log.warning("%s: volume not restored: %s", mac, err)


duck = Duck()
