"""What each voice.py intent does to a TV. To add a command: a phrase in voice.py,
and, if it is a new kind of action, a function here with @handles("its-type").

A handler returns {"say": ...}, plus "failed", "detail" or "results" as needed.
"""

from __future__ import annotations

import time
from typing import Callable

from tvcontrol import TvControl
from youtube import youtube_search

VOLUME_STEP = 5
WAKE_SETTLE = 2  # seconds for a TV just woken to accept apps and keys
KEY_REPEAT_GAP = 0.25

Handler = Callable[[TvControl, dict], dict]
_HANDLERS: dict[str, Handler] = {}
WAKES_THE_TV = {"app", "play", "input"}  # others are refused, or need no TV
NEEDS_TV_ON = {"input_next", "input_list", "settings_hint", "channel", "volume", "volume_set", "mute", "media", "key"}


def handles(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn
    return register


def run_intent(tv: TvControl, intent: dict) -> dict:
    """Does what was asked and says what happened. Raises ConnectionError when
    the TV cannot do it."""
    kind = intent["type"]
    if kind not in _HANDLERS:
        raise ValueError(f"unknown intent {kind}")
    if kind in WAKES_THE_TV or kind in NEEDS_TV_ON:
        if tv.status().get("power") != "on":
            if kind in NEEDS_TV_ON:
                return {"say": "The TV is off. Say “turn on the TV” first", "failed": True}
            tv.toggle_power()
            time.sleep(WAKE_SETTLE)
    return _HANDLERS[kind](tv, intent)


@handles("power")
def _power(tv: TvControl, intent: dict) -> dict:
    want_on = intent["action"] == "on"
    if (tv.status().get("power") == "on") == want_on:
        return {"say": f"The TV is already {'on' if want_on else 'off'}"}
    tv.toggle_power()
    return {"say": f"TV {'on' if want_on else 'off'}"}


@handles("input")
def _input(tv: TvControl, intent: dict) -> dict:
    name = tv.select_input(intent["id"])
    if not name:
        return {"say": f"Nothing is plugged into {intent['label']}", "failed": True}
    return {"say": f"Switching to {name}"}


@handles("app")
def _app(tv: TvControl, intent: dict) -> dict:
    tv.open_app(intent["id"])
    return {"say": f"Opening {intent['name']}"}


@handles("search")
def _search(tv: TvControl, intent: dict) -> dict:
    return {"say": f"Results for {intent['query']}", "results": youtube_search(intent["query"])}


@handles("play")
def _play(tv: TvControl, intent: dict) -> dict:
    found = youtube_search(intent["query"], limit=1)
    if not found:
        return {"say": f"No video found for {intent['query']}", "failed": True}
    tv.play_video(found[0]["id"])
    return {"say": f"Playing {found[0]['title']}"}


@handles("input_next")
def _input_next(tv: TvControl, intent: dict) -> dict:
    tv.press("KEY_HDMI")
    return {"say": "Next HDMI"}


@handles("input_list")
def _input_list(tv: TvControl, intent: dict) -> dict:
    tv.press("KEY_SOURCE")
    return {"say": "Source list open"}


@handles("settings_hint")
def _settings_hint(tv: TvControl, intent: dict) -> dict:
    tv.press("KEY_MENU")
    return {"say": "Settings open", "detail": intent["where"]}


@handles("channel")
def _channel(tv: TvControl, intent: dict) -> dict:
    for digit in intent["number"]:
        tv.press(f"KEY_{digit}")
        time.sleep(KEY_REPEAT_GAP)
    tv.press("KEY_ENTER")
    return {"say": intent["label"]}


@handles("volume")
def _volume(tv: TvControl, intent: dict) -> dict:
    level = tv.audio()["volume"]
    if level is None:  # UPnP is not answering: the keys still work
        tv.press("KEY_VOLUP" if intent["dir"] > 0 else "KEY_VOLDOWN")
        return {"say": intent["label"]}
    level = max(0, min(100, level + intent["dir"] * intent.get("by", VOLUME_STEP)))
    tv.set_volume(level)
    return {"say": f"Volume {level}"}


@handles("volume_set")
def _volume_set(tv: TvControl, intent: dict) -> dict:
    tv.set_volume(intent["level"])
    return {"say": f"Volume {intent['level']}"}


@handles("mute")
def _mute(tv: TvControl, intent: dict) -> dict:
    tv.set_mute(intent["on"])
    return {"say": "Muted" if intent["on"] else "Sound on"}


@handles("media")
def _media(tv: TvControl, intent: dict) -> dict:
    tv.media_action(intent["action"], float(intent.get("value", 0)))
    return {"say": intent["label"]}


@handles("key")
def _key(tv: TvControl, intent: dict) -> dict:
    for _ in range(intent.get("times", 1)):
        tv.press(intent["key"])
        time.sleep(KEY_REPEAT_GAP)
    return {"say": intent["label"]}
