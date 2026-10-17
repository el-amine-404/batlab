"""Says when the Clio 4 or the Ford leaves its parking spot on the street,
and when it is back.

Frigate (config.yml, camera_2) follows the cars and has a zone per spot. It
publishes an event on MQTT each time a car changes zone or goes from moving to
parked. A car that drives into a spot has arrived; a car parked in a spot that
drives out of it has left. Each is told the moment Frigate sees it, with
Frigate's latest frame of the street (the cars boxed) and a close-up of the
car from the 4K stream. Once the recording is written, a second message says
which way the car went (or came from) and holds the clip, made small enough
for Discord to play.

Counting cars in a spot is not enough: Frigate looks for cars only where
something moves, so after a restart a parked car stays unseen until someone
walks past it, and would "arrive" then. Here a car first seen already in its
spot has not arrived; it can only leave. Frigate also boxes part of a parked
car at times, then all of it, and the box's bottom moves into or out of the
spot: a car has to have moved CARWATCH_MOVE pixels (of the 960 wide frame)
to arrive or leave.

Messages go through Apprise (compose/apprise) to the "cameras" channel. It
runs in Frigate's image, for OpenCV and ffmpeg; batlab_common
(lib/batlab-common) besides.
"""

import json
import math
import os
import select
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.request
from datetime import datetime

from batlab_common import notify as apprise

BROKER = os.environ.get("CARWATCH_MQTT_HOST", "mosquitto")
PORT = int(os.environ.get("CARWATCH_MQTT_PORT", "1883"))
FRIGATE = os.environ.get("CARWATCH_FRIGATE", "http://frigate:5000")
CAMERA = os.environ.get("CARWATCH_CAMERA", "camera_2")
# zone=name pairs: "spot_1=Car 1,spot_2=Car 2".
SPOTS = dict(pair.split("=", 1) for pair in
             os.environ.get("CARWATCH_SPOTS", "spot_1=Car 1,spot_2=Car 2").split(","))
MOVE = int(os.environ.get("CARWATCH_MOVE", "150"))
# Frigate's detect size, which its boxes are in.
WIDTH = int(os.environ.get("CARWATCH_DETECT_WIDTH", "960"))
HEIGHT = int(os.environ.get("CARWATCH_DETECT_HEIGHT", "576"))
# The full-size picture: go2rtc's frame of the main stream.
FRAME = os.environ.get("CARWATCH_FRAME", "http://frigate:1984/api/frame.jpeg?src=camera_2")
# Which way a car went: the side of the picture it was last seen on (first
# seen, for one arriving).
WAYS = dict(pair.split("=", 1) for pair in
            os.environ.get("CARWATCH_WAYS", "left=left,right=right").split(","))
# Seconds of the clip before and after the moment told.
BEFORE, AFTER = 5, 15
FFMPEG = os.environ.get("CARWATCH_FFMPEG", "/usr/lib/ffmpeg/8.0/bin/ffmpeg")
NOTIFY_URL = os.environ.get("CARWATCH_NOTIFY_URL", "")
NOTIFY_TAG = os.environ.get("CARWATCH_NOTIFY_TAG", "cameras")
KEEPALIVE = 60
HEARTBEAT = "/tmp/heartbeat"


def log(message):
    print(f"{datetime.now().isoformat(timespec='seconds')} {message}", flush=True)


class Mqtt:
    """A subscriber to one topic, QoS 0: MQTT 3.1.1, the little of it needed."""

    def __init__(self, host, port, topic):
        self.sock = socket.create_connection((host, port), timeout=30)
        # Unique per container: the broker drops a client when another one
        # connects with the same id, and every container's process is pid 1.
        client = f"carwatch-{socket.gethostname()}"
        self.send(0x10, self.text("MQTT") + bytes([4, 0x02]) + struct.pack("!H", KEEPALIVE) + self.text(client))
        kind, body = self.read()
        if kind != 0x20 or body[1:2] != b"\x00":
            raise ConnectionError(f"the broker refused the connection ({body.hex()})")
        self.send(0x82, struct.pack("!H", 1) + self.text(topic) + b"\x00")
        self.sent = time.time()

    @staticmethod
    def text(value):
        data = value.encode()
        return struct.pack("!H", len(data)) + data

    def send(self, kind, body):
        size, length = len(body), b""
        while True:
            byte, size = size % 128, size // 128
            length += bytes([byte | (0x80 if size else 0)])
            if not size:
                break
        self.sock.sendall(bytes([kind]) + length + body)
        self.sent = time.time()

    def exactly(self, count):
        data = b""
        while len(data) < count:
            chunk = self.sock.recv(count - len(data))
            if not chunk:
                raise ConnectionError("the broker closed the connection")
            data += chunk
        return data

    def read(self):
        kind = self.exactly(1)[0]
        size, shift = 0, 0
        while True:
            byte = self.exactly(1)[0]
            size += (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                break
        return kind & 0xF0, self.exactly(size)

    def messages(self, wait):
        """The payloads that arrive within wait seconds; pings the broker on
        the way."""
        if time.time() - self.sent > KEEPALIVE / 2:
            self.send(0xC0, b"")
        if not select.select([self.sock], [], [], wait)[0]:
            return []
        kind, body = self.read()
        if kind != 0x30:  # SUBACK, PINGRESP
            return []
        length = struct.unpack("!H", body[:2])[0]
        return [body[2 + length:]]


def get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except OSError as error:
        log(f"{url}: {error}")
        return None


def picture():
    """Frigate's latest frame of the street, the cars boxed; None if it has none."""
    return get(f"{FRIGATE}/api/{CAMERA}/latest.jpg?bbox=1")


def closeup(box):
    """The car at box (detect pixels) and around it, cut from the 4K frame."""
    import cv2
    import numpy

    data = get(FRAME)
    frame = data and cv2.imdecode(numpy.frombuffer(data, numpy.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return None
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = box
    # 1.6 times the car, square: a moving car is still in it a second later.
    side = max(x2 - x1, y2 - y1) * 1.6 * width / WIDTH
    middle_x, middle_y = (x1 + x2) / 2 * width / WIDTH, (y1 + y2) / 2 * height / HEIGHT
    side = min(max(side, 600), height)
    left = int(min(max(middle_x - side / 2, 0), width - side))
    top = int(min(max(middle_y - side / 2, 0), height - side))
    crop = frame[top:top + int(side), left:left + int(side)]
    ok, jpeg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return jpeg.tobytes() if ok else None


def clip(start, end):
    """The recording from start to end as a small H.264 file, or None."""
    data = get(f"{FRIGATE}/api/{CAMERA}/start/{int(start)}/end/{int(end)}/clip.mp4", timeout=60)
    if not data:
        return None
    with tempfile.TemporaryDirectory() as folder:
        source, made = os.path.join(folder, "in.mp4"), os.path.join(folder, "out.mp4")
        with open(source, "wb") as out:
            out.write(data)
        done = subprocess.run([FFMPEG, "-v", "error", "-y", "-threads", "3", "-i", source, "-an",
                               "-vf", "scale=1280:-2,fps=10", "-c:v", "libx264", "-preset", "veryfast",
                               "-crf", "27", "-movflags", "+faststart", made], capture_output=True)
        if done.returncode:
            log(f"clip: {done.stderr.decode(errors='replace')[-300:]}")
            return None
        with open(made, "rb") as file:
            return file.read()


def notify(text, kind, files=()):
    """One message, Markdown, with files ((name, bytes), None skipped) under it."""
    if not NOTIFY_URL:
        return
    attach = [(name, data) for name, data in files if data]
    try:
        apprise.send(NOTIFY_URL, "", text, tag=NOTIFY_TAG, kind=kind, attach=attach, timeout=60)
    except apprise.NotSent as error:  # a lost message never stops the watch
        log(f"notification: {error}")


def duration(seconds):
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d}"


class Watch:
    def __init__(self):
        self.first = {}    # car (Frigate's id) -> the spot it was first seen in, or None
        self.start = {}    # car -> where it was first seen, or parked: (x, y) of its box's bottom
        self.parked = {}   # car -> the spot it is parked in
        self.arrived = {}  # car -> the spot it was told arriving in
        self.last = {}     # spot -> ("arrived" | "left", when), what was last told
        self.origin = {}   # car -> where it was first seen; kept after it ends, until told
        self.seen = {}     # car -> where it was last seen; the same

    def spot(self, zones):
        return next((zone for zone in zones if zone in SPOTS), None)

    def event(self, message):
        after = message.get("after") or {}
        if after.get("camera") != CAMERA or after.get("label") != "car":
            return
        car, when = after["id"], after.get("frame_time") or time.time()
        spot = self.spot(after.get("current_zones") or [])
        x1, _, x2, y2 = after.get("box") or (0, 0, 0, 0)
        here = ((x1 + x2) / 2, y2)
        self.first.setdefault(car, spot)
        self.start.setdefault(car, here)
        self.origin.setdefault(car, here)
        self.seen[car] = here
        box = after.get("box") or (0, 0, 0, 0)
        moved = math.dist(self.start[car], here) >= MOVE
        # A parked car Frigate had not seen is often found only as it starts
        # to move: first seen in a spot is as good as parked there.
        was = self.parked.get(car) or self.first[car]
        if was and spot != was and not after.get("stationary") and moved:
            self.parked.pop(car, None)
            self.first[car] = None
            self.start[car] = here
            self.arrived.pop(car, None)
            self.tell(was, "left", car, when, box)
        elif (spot and self.first[car] != spot and self.arrived.get(car) != spot
              and spot not in self.parked.values() and moved):
            # Driven in from the street, the spot empty (a car passing close
            # to a parked one is not arriving). Told once: a car manoeuvring
            # in and out of the spot before it parks says nothing more.
            self.arrived[car] = spot
            self.tell(spot, "arrived", car, when, box)
        if spot and after.get("stationary") and car not in self.parked:
            self.parked[car] = spot
            self.start[car] = here
            log(f"{SPOTS[spot]}: {car} parked")
        if message.get("type") == "end":
            # Gone from the picture: driven off (told above, as it left its
            # spot), or lost while parked, which says nothing.
            for seen in (self.first, self.start, self.parked, self.arrived):
                seen.pop(car, None)

    def tell(self, spot, kind, car, when, box):
        before = self.last.get(spot)
        self.last[spot] = (kind, when)
        # Off the MQTT loop: the clip waits for the recording, and the next car
        # must not wait for it.
        threading.Thread(target=self.report, args=(spot, kind, car, when, box, before), daemon=True).start()

    def report(self, spot, kind, car, when, box, before):
        name, at = SPOTS[spot], datetime.fromtimestamp(when).strftime("%H:%M:%S")
        title = f"{name} left" if kind == "left" else f"{name} is back"
        line = at
        if before and before[0] != kind:
            line += f" · {'away' if kind == 'arrived' else 'parked'} {duration(when - before[1])}"
        try:
            near = closeup(box)
        except Exception as error:  # a picture less never loses the message
            log(f"closeup: {error}")
            near = None
        notify(f"***{title}***\n{line}", "info" if kind == "left" else "success",
               [(f"{spot}.jpg", near), ("street.jpg", picture())])
        log(f"told: {name} {kind} at {at} ({car})")

        # Frigate writes the recording in 10 s pieces, a little after.
        time.sleep(max(0, when + AFTER + 25 - time.time()))
        where = (self.seen if kind == "left" else self.origin).get(car)
        way = WAYS["left" if where and where[0] < WIDTH / 2 else "right"] if where else None
        video = None
        for _ in range(3):
            video = clip(when - BEFORE, when + AFTER)
            if video:
                break
            time.sleep(20)
        if kind == "left":
            words = f"***{name} went {way}***" if way else f"***{name} left***"
        else:
            words = f"***{name} came from the {way}***" if way else f"***{name} is back***"
        notify(f"{words}\n{at}" + ("" if video else " · no clip"), "info", [(f"{spot}.mp4", video)])
        log(f"told: {name} {kind} {way}, clip {len(video or b'')} bytes")
        if car not in self.parked and car not in self.first:
            self.origin.pop(car, None)
            self.seen.pop(car, None)


def main():
    watch = Watch()
    log(f"watching {CAMERA} on {BROKER}:{PORT}; spots {SPOTS}")
    while True:
        try:
            mqtt = Mqtt(BROKER, PORT, "frigate/events")
            log("connected")
            while True:
                for payload in mqtt.messages(5):
                    try:
                        watch.event(json.loads(payload))
                    except (ValueError, KeyError, TypeError) as error:
                        log(f"event: {error}")
                with open(HEARTBEAT, "w") as out:
                    out.write(str(int(time.time())))
        except (OSError, ConnectionError) as error:
            log(f"mqtt: {error}; again in 10s")
            time.sleep(10)


if __name__ == "__main__":
    main()
