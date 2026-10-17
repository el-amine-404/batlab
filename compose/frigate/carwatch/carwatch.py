"""Says when the Clio 4 or the Ford leaves its parking spot on the street,
and when it is back, with a picture of the street.

Frigate (config.yml, camera_2) follows the cars and has a zone per spot. It
publishes an event on MQTT each time a car changes zone or goes from moving to
parked. A car that drives into a spot has arrived; a car parked in a spot that
drives out of it has left. Each is told the moment Frigate sees it, with
Frigate's latest frame of the street, the car boxed.

Counting cars in a spot is not enough: Frigate looks for cars only where
something moves, so after a restart a parked car stays unseen until someone
walks past it, and would "arrive" then. Here a car first seen already in its
spot has not arrived; it can only leave.

Messages go through Apprise (compose/apprise) to the "cameras" channel.
Standard library and batlab_common (lib/batlab-common) only.
"""

import json
import os
import select
import socket
import struct
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


def picture():
    """Frigate's latest frame of the street, the cars boxed; None if it has none."""
    try:
        with urllib.request.urlopen(f"{FRIGATE}/api/{CAMERA}/latest.jpg?bbox=1", timeout=10) as response:
            return response.read()
    except OSError as error:
        log(f"picture: {error}")
        return None


def notify(text, kind, image=None):
    """One message, Markdown, with the picture under it."""
    if not NOTIFY_URL:
        return
    attach = [(f"{CAMERA}.jpg", image)] if image else ()
    try:
        apprise.send(NOTIFY_URL, "", text, tag=NOTIFY_TAG, kind=kind, attach=attach)
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
        self.parked = {}   # car -> the spot it is parked in
        self.arrived = {}  # car -> the spot it was told arriving in
        self.last = {}     # spot -> ("arrived" | "left", when), what was last told

    def spot(self, zones):
        return next((zone for zone in zones if zone in SPOTS), None)

    def event(self, message):
        after = message.get("after") or {}
        if after.get("camera") != CAMERA or after.get("label") != "car":
            return
        car, when = after["id"], after.get("frame_time") or time.time()
        spot = self.spot(after.get("current_zones") or [])
        self.first.setdefault(car, spot)
        # A parked car Frigate had not seen is often found only as it starts
        # to move: first seen in a spot is as good as parked there.
        was = self.parked.get(car) or self.first[car]
        if was and spot != was and not after.get("stationary"):
            self.parked.pop(car, None)
            self.first[car] = None
            self.arrived.pop(car, None)
            self.tell(was, "left", car, when)
        elif (spot and self.first[car] != spot and self.arrived.get(car) != spot
              and spot not in self.parked.values()):
            # Driven in from the street, the spot empty (a car passing close
            # to a parked one is not arriving). Told once: a car manoeuvring
            # in and out of the spot before it parks says nothing more.
            self.arrived[car] = spot
            self.tell(spot, "arrived", car, when)
        if spot and after.get("stationary") and car not in self.parked:
            self.parked[car] = spot
            log(f"{SPOTS[spot]}: {car} parked")
        if message.get("type") == "end":
            # Gone from the picture: driven off (told above, as it left its
            # spot), or lost while parked, which says nothing.
            for seen in (self.first, self.parked, self.arrived):
                seen.pop(car, None)

    def tell(self, spot, kind, car, when):
        name, at = SPOTS[spot], datetime.fromtimestamp(when).strftime("%H:%M:%S")
        line = at
        before = self.last.get(spot)
        if before and before[0] != kind:
            line += f" · {'away' if kind == 'arrived' else 'parked'} {duration(when - before[1])}"
        title = f"{name} left" if kind == "left" else f"{name} is back"
        notify(f"***{title}***\n{line}", "info" if kind == "left" else "success", picture())
        log(f"told: {name} {kind} at {at} ({car})")
        self.last[spot] = (kind, when)


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
