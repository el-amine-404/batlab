#!/usr/bin/env python3
"""Blank lab2's built-in panel while its lid is closed."""

from __future__ import annotations

import argparse
import glob
from pathlib import Path
import struct
import sys
import time


EVENT = struct.Struct("@llHHi")
EV_SW = 0x05
SW_LID = 0x00


def log(message: str) -> None:
    print(message, flush=True)


def read_int(path: Path) -> int:
    return int(path.read_text(encoding="ascii").strip())


def write_value(path: Path, value: int) -> None:
    path.write_text(f"{value}\n", encoding="ascii")


class ScreenController:
    def __init__(
        self,
        backlight: Path,
        framebuffer_blank: Path,
        saved_brightness: Path,
    ) -> None:
        self.backlight = backlight
        self.framebuffer_blank = framebuffer_blank
        self.saved_brightness = saved_brightness

    @property
    def brightness(self) -> Path:
        return self.backlight / "brightness"

    @property
    def max_brightness(self) -> Path:
        return self.backlight / "max_brightness"

    def close(self) -> None:
        if self.brightness.exists():
            brightness = read_int(self.brightness)
            if brightness > 0:
                self.saved_brightness.parent.mkdir(parents=True, exist_ok=True)
                write_value(self.saved_brightness, brightness)
        if self.framebuffer_blank.exists():
            write_value(self.framebuffer_blank, 1)
        if self.brightness.exists():
            write_value(self.brightness, 0)
        log("lid closed: built-in screen blanked")

    def open(self) -> None:
        if self.saved_brightness.exists() and self.brightness.exists():
            brightness = read_int(self.saved_brightness)
            maximum = read_int(self.max_brightness)
            if 0 < brightness <= maximum:
                write_value(self.brightness, brightness)
            self.saved_brightness.unlink(missing_ok=True)
        if self.framebuffer_blank.exists():
            write_value(self.framebuffer_blank, 0)
        log("lid open: built-in screen restored")


def current_lid_state(pattern: str) -> bool:
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise RuntimeError(f"no lid state matches {pattern}")
    state = Path(paths[0]).read_text(encoding="ascii").split()[-1]
    if state not in {"open", "closed"}:
        raise RuntimeError(f"unexpected lid state {state!r} in {paths[0]}")
    return state == "closed"


def find_lid_device(input_class: Path) -> Path:
    for event in sorted(input_class.glob("event*")):
        name = event / "device" / "name"
        try:
            if name.read_text(encoding="utf-8").strip() == "Lid Switch":
                return Path("/dev/input") / event.name
        except OSError:
            continue
    raise RuntimeError("kernel Lid Switch input device not found")


def read_event(stream) -> tuple[int, int, int]:
    payload = stream.read(EVENT.size)
    if len(payload) != EVENT.size:
        raise EOFError("lid input device disappeared")
    _seconds, _microseconds, event_type, code, value = EVENT.unpack(payload)
    return event_type, code, value


def watch(args: argparse.Namespace) -> None:
    screen = ScreenController(args.backlight, args.framebuffer_blank, args.state_file)
    while True:
        try:
            # Reconcile the real state every time the input device reappears or
            # a sysfs write is retried; an event can be lost during either gap.
            closed = current_lid_state(args.lid_state)
            screen.close() if closed else screen.open()
            device = args.input_device or find_lid_device(args.input_class)
            log(f"watching lid events from {device}")
            with device.open("rb", buffering=0) as stream:
                while True:
                    event_type, code, value = read_event(stream)
                    if event_type != EV_SW or code != SW_LID:
                        continue
                    new_closed = value == 1
                    if new_closed == closed:
                        continue
                    closed = new_closed
                    screen.close() if closed else screen.open()
        except (EOFError, OSError, RuntimeError) as error:
            log(f"lid event source unavailable: {error}; retrying")
            time.sleep(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", action="store_true", help="restore the screen and exit")
    parser.add_argument("--lid-state", default="/proc/acpi/button/lid/*/state")
    parser.add_argument("--input-class", type=Path, default=Path("/sys/class/input"))
    parser.add_argument("--input-device", type=Path)
    parser.add_argument(
        "--backlight", type=Path, default=Path("/sys/class/backlight/intel_backlight")
    )
    parser.add_argument(
        "--framebuffer-blank", type=Path, default=Path("/sys/class/graphics/fb0/blank")
    )
    parser.add_argument(
        "--state-file", type=Path, default=Path("/run/batlab-lid-screen/brightness")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    screen = ScreenController(args.backlight, args.framebuffer_blank, args.state_file)
    try:
        if args.restore:
            screen.open()
        else:
            watch(args)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"batlab-lid-screen: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
