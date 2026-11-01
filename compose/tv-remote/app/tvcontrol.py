"""One TV, as everything above it sees it. commands.py and the web API use this
and nothing lower, so they can be tested with a fake and a different make of TV
would only need another class with these methods.
"""

from __future__ import annotations

import threading
import time
import urllib.error
from typing import Protocol

import playback
import samsung
import tvstate
import youtube
from profiles import profile_for
from store import load_tvs

# Seconds between keys while walking the TV's source list.
KEY_GAP = 0.45
HOME_SETTLE = 2.5
LIST_SETTLE = 1.5
# Two walks at once would mix their keys and select the wrong thing.
_walking = threading.Lock()


class TvControl(Protocol):
    def inputs(self) -> list[dict]: ...
    def status(self) -> dict: ...
    def media(self) -> dict | None: ...
    def toggle_power(self) -> dict: ...
    def press(self, key: str) -> None: ...
    def open_app(self, app_id: str) -> None: ...
    def audio(self) -> dict: ...
    def set_volume(self, level: int) -> None: ...
    def set_mute(self, mute: bool) -> None: ...
    def media_action(self, action: str, value: float = 0) -> None: ...
    def play_video(self, video_id: str) -> None: ...
    def select_input(self, input_id: str) -> str | None: ...


class SamsungTv:
    """A paired Samsung Tizen TV."""

    def __init__(self, mac: str) -> None:
        self.mac = mac

    @property
    def _ip(self) -> str:
        return load_tvs()[self.mac]["ip"]

    def inputs(self) -> list[dict]:
        return profile_for(self.mac, load_tvs()[self.mac].get("model", ""))["inputs"]

    def status(self) -> dict:
        return tvstate.status(self.mac)

    def media(self) -> dict | None:
        return playback.media(self.mac)

    def toggle_power(self) -> dict:
        return tvstate.power(self.mac)

    def press(self, key: str) -> None:
        samsung.remote(self.mac).run(lambda tv: tv.send_key(key))

    def open_app(self, app_id: str) -> None:
        try:
            samsung.rest(self._ip, f"applications/{app_id}", "POST", timeout=6)
        except (OSError, urllib.error.URLError) as err:
            raise ConnectionError("the TV did not open the app") from err

    def audio(self) -> dict:
        return samsung.audio(self._ip)

    def set_volume(self, level: int) -> None:
        samsung.set_volume(self._ip, level)

    def set_mute(self, mute: bool) -> None:
        samsung.set_mute(self._ip, mute)

    def media_action(self, action: str, value: float = 0) -> None:
        playback.media_action(self.mac, action, value)

    def play_video(self, video_id: str) -> None:
        try:
            youtube.youtube_link(self.mac).play_video(video_id)
        except (OSError, urllib.error.URLError, TimeoutError) as err:
            raise ConnectionError("YouTube on the TV did not answer") from err

    def select_input(self, input_id: str) -> str | None:
        """The input's name once selected; None if this TV has no such input.

        The Q8F has no key for one input: KEY_HDMI1 does nothing, KEY_HDMI only
        moves to the next one, and nothing reports which is showing. So its own
        source list is walked, confirmed on the TV on 2026-11-01:
        - Home first: from inside an app the list never gets the keys.
        - Up to the top of the column; LEFT would leave the list for the side
          menu, and the OK below would then open Privacy Choices.
        """
        inputs = self.inputs()
        target = next((i for i in inputs if i["id"] == input_id), None)
        if not target:
            return None
        if not _walking.acquire(blocking=False):
            raise ConnectionError("still switching input; try again in a moment")
        try:
            self._walk("KEY_HOME", HOME_SETTLE)
            self._walk("KEY_SOURCE", LIST_SETTLE)
            for _ in range(max(i["row"] for i in inputs)):
                self._walk("KEY_UP")
            for _ in range(target["row"]):
                self._walk("KEY_DOWN")
            self._walk("KEY_ENTER", 0.2)
        finally:
            _walking.release()
        return target["name"]

    def _walk(self, key: str, wait: float = KEY_GAP) -> None:
        self.press(key)
        time.sleep(wait)


def control(mac: str) -> TvControl:
    """The TV with this MAC; KeyError if it is not paired."""
    if mac not in load_tvs():
        raise KeyError(mac)
    return SamsungTv(mac)
