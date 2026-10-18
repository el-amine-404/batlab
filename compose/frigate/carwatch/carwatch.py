"""Says when the Clio 4 or the Ford leaves its parking spot on the street,
and when it is back.

Every CARWATCH_POLL seconds it looks at the spots itself: Frigate's latest
frame of the street, the area around the spots, through the same YOLO model
Frigate uses. A spot holds a car when a car's box covers CARWATCH_FILL of its
zone (Frigate's zones, drawn around each parked car). A spot that changes,
and stays changed for CARWATCH_CONFIRM seconds, is told: "left" or "is back",
with a close-up from the 4K stream and the street. Once the recording is
written, a second message says which way the car went (or came from), from
the path of the car Frigate followed near the spot then, and holds the clip,
made small enough for Discord to play.

Frigate's own tracking is not enough for this: from above it loses a parked
car every half hour and finds it again only when something moves near it,
and it hands a parked car's identity to a car driving past. Its events are
only used for the way a car went.

A car with no spot of its own (the Sandero) is known by its look instead:
Frigate's classifier (Settings > Classification) gives it a sub label, here
CARWATCH_FOLLOW's. It is told parked wherever it stops after driving in, and
gone when it drives a car length away from there.

It also says when someone stays at the cars: Frigate puts a person in the
CARWATCH_PEOPLE_ZONE only after its loitering time there (30 s). Then the
same: a close-up and the street at once, the clip after.

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
BEFORE, AFTER = 10, 15
# Frigate's model, read from its config folder.
MODEL = os.environ.get("CARWATCH_MODEL", "/models/yolov9-s-320.onnx")
MODEL_SIZE = 320
CAR = 2  # COCO's car
POLL = int(os.environ.get("CARWATCH_POLL", "3"))
CONFIRM = int(os.environ.get("CARWATCH_CONFIRM", "12"))
FILL = float(os.environ.get("CARWATCH_FILL", "0.4"))
# sub_label=name pairs of the cars followed wherever they park.
FOLLOW = dict(pair.split("=", 1) for pair in
              os.environ.get("CARWATCH_FOLLOW", "sandero=Sandero").split(",") if pair)
PEOPLE_ZONE = os.environ.get("CARWATCH_PEOPLE_ZONE", "near_cars")
# At most one "someone at the cars" in that many seconds: a family loading
# the boot for ten minutes is told once.
PEOPLE_QUIET = int(os.environ.get("CARWATCH_PEOPLE_QUIET", "600"))
LOITER = 30
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


def coordinates(zone):
    """A Frigate zone's polygon in detect pixels."""
    import numpy

    points = zone["coordinates"]
    points = points.split(",") if isinstance(points, str) else points
    return (numpy.array([float(v) for v in points], numpy.float32).reshape(-1, 2) * [WIDTH, HEIGHT])


class Spots:
    """Which spots hold a car right now, by Frigate's model on the frame."""

    def __init__(self):
        self.model = None
        self.zones = {}
        self.loaded = 0.0

    def load(self):
        import openvino

        if self.model is None:
            self.model = openvino.Core().compile_model(MODEL, "CPU")
        # The zones are drawn in Frigate's UI; read them again now and then.
        if time.time() - self.loaded > 600:
            zones = json.loads(get(f"{FRIGATE}/api/config") or b"{}")["cameras"][CAMERA]["zones"]
            self.zones = {spot: coordinates(zones[spot]) for spot in SPOTS if spot in zones}
            self.loaded = time.time()

    def cars(self, frame, polygon):
        """The cars' boxes in a square around one spot. The model is sure of a
        parked car from above only with room around it: a square twice the
        car, centred on it, scaled to the model (a tight crop loses it)."""
        import cv2
        import numpy

        (x1, y1), (x2, y2) = polygon.min(0), polygon.max(0)
        side = int(min(max(x2 - x1, y2 - y1) * 1.9, HEIGHT))
        left = int(min(max((x1 + x2 - side) / 2, 0), WIDTH - side))
        top = int(min(max((y1 + y2 - side) / 2, 0), HEIGHT - side))
        crop = cv2.resize(frame[top:top + side, left:left + side], (MODEL_SIZE, MODEL_SIZE))
        tensor = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)[None].astype(numpy.float32) / 255
        rows = self.model([tensor])[0][0].T
        rows = rows[rows[:, 4 + CAR] > 0.4]
        scale = side / MODEL_SIZE
        boxes = [((cx - w / 2) * scale + left, (cy - h / 2) * scale + top,
                  (cx + w / 2) * scale + left, (cy + h / 2) * scale + top)
                 for cx, cy, w, h in rows[:, :4]]
        keep = cv2.dnn.NMSBoxes([[b[0], b[1], b[2] - b[0], b[3] - b[1]] for b in boxes],
                                [float(v) for v in rows[:, 4 + CAR]], 0.4, 0.5) if boxes else []
        return [boxes[i] for i in numpy.array(keep).flatten()]

    def look(self):
        """spot -> (how much of it a car covers, that car's box), or None when
        there is no frame."""
        import cv2
        import numpy

        self.load()
        data = get(f"{FRIGATE}/api/{CAMERA}/latest.jpg")
        frame = data and cv2.imdecode(numpy.frombuffer(data, numpy.uint8), cv2.IMREAD_COLOR)
        if frame is None or not self.zones:
            return None
        frame = cv2.resize(frame, (WIDTH, HEIGHT)) if frame.shape[:2] != (HEIGHT, WIDTH) else frame
        seen = {}
        for spot, polygon in self.zones.items():
            boxes = self.cars(frame, polygon)
            zone = numpy.zeros((HEIGHT, WIDTH), numpy.uint8)
            cv2.fillPoly(zone, [polygon.astype(numpy.int32)], 1)
            best, where = 0.0, None
            for box in boxes:
                covered = zone[int(max(box[1], 0)):int(box[3]), int(max(box[0], 0)):int(box[2])].sum() / zone.sum()
                if covered > best:
                    best, where = covered, box
            seen[spot] = (best, where)
        return seen


class Watch:
    def __init__(self):
        self.spots = Spots()
        self.alive, self.asked = False, float("-inf")
        self.held = {}     # spot -> True (a car) or False, as last told or first seen
        self.change = {}   # spot -> (True | False, since, box): what it looks like, not yet held
        self.boxes = {}    # spot -> the box of the car last seen in it
        self.trails = {}   # car (Frigate's id) -> [(when, x, y)] of its box's bottom middle
        self.last = {}     # spot or label -> ("arrived" | "parked" | "left", when), last told
        self.origin = {}   # car -> where it was first seen; kept after it ends, until told
        self.seen = {}     # car -> where it was last seen; the same
        self.stopped = {}  # followed car -> where it is parked
        self.people = set()  # the people told as staying at the cars
        self.people_told = float("-inf")

    def check(self, now):
        """Look at the spots; tell a spot that changed CONFIRM seconds ago and
        stayed so."""
        if not self.live(now):
            # A dark camera's last frame, or none, says nothing of the spots.
            self.change.clear()
            return
        try:
            seen = self.spots.look()
        except Exception as error:  # a missed look is caught up by the next
            log(f"look: {error}")
            return
        for spot, (covered, box) in (seen or {}).items():
            full = covered >= FILL
            if full:
                self.boxes[spot] = box
            if self.held.get(spot) == full:
                self.change.pop(spot, None)
                continue
            if spot not in self.change or self.change[spot][0] != full:
                self.change[spot] = (full, now, box or self.boxes.get(spot))
                continue
            _, since, where = self.change[spot]
            if now - since < CONFIRM:
                continue
            self.change.pop(spot)
            was = self.held.get(spot)
            self.held[spot] = full
            if was is None:
                log(f"{SPOTS[spot]}: {'a car' if full else 'empty'} ({covered:.2f})")
            else:
                self.tell(spot, SPOTS[spot], "arrived" if full else "left", None, since,
                          where or (0, 0, 0, 0))

    def live(self, now):
        """Whether the camera gives frames, from Frigate's stats every 30 s."""
        if now - self.asked >= 30:
            self.asked = now
            try:
                stats = json.loads(get(f"{FRIGATE}/api/stats") or b"{}")
                self.alive = bool(stats["cameras"][CAMERA].get("camera_fps"))
            except (ValueError, KeyError):
                self.alive = False
        return self.alive

    def way(self, spot, kind, when):
        """Which side the car went to (or came from): the car Frigate followed
        nearest the spot about then, its last point (or first); None if none."""
        polygon = self.spots.zones.get(spot)
        if polygon is None:
            return None
        (x1, y1), (x2, y2) = polygon.min(0) - 30, polygon.max(0) + 30
        middle = polygon.mean(0)
        best = None
        for trail in list(self.trails.values()):
            near = [p for p in trail if when - 20 <= p[0] <= when + 15
                    and x1 <= p[1] <= x2 and y1 <= p[2] <= y2]
            if not near:
                continue
            end = trail[-1] if kind == "left" else trail[0]
            far = math.dist(end[1:], middle)
            if far >= MOVE and (best is None or far > best[0]):
                best = (far, end)
        if not best:
            return None
        return WAYS["left" if best[1][1] < WIDTH / 2 else "right"]

    def event(self, message):
        after = message.get("after") or {}
        if after.get("camera") != CAMERA:
            return
        if after.get("label") == "person":
            return self.person(message, after)
        if after.get("label") != "car":
            return
        car, when = after["id"], after.get("frame_time") or time.time()
        box = after.get("box") or (0, 0, 0, 0)
        here = ((box[0] + box[2]) / 2, box[3])
        self.trails.setdefault(car, []).append((when, *here))
        self.origin.setdefault(car, here)
        self.seen[car] = here
        self.follow(after, car, when, here, box)
        if message.get("type") == "end":
            self.stopped.pop(car, None)
        # Ten minutes of paths: enough for the way of a car just told.
        for key in [k for k, t in self.trails.items() if t[-1][0] < when - 600]:
            self.trails.pop(key)
            self.origin.pop(key, None)
            self.seen.pop(key, None)

    def person(self, message, after):
        who, when = after["id"], after.get("frame_time") or time.time()
        if message.get("type") == "end":
            self.people.discard(who)
        elif (PEOPLE_ZONE in (after.get("current_zones") or []) and who not in self.people
              and when - self.people_told >= PEOPLE_QUIET):
            self.people.add(who)
            self.people_told = when
            box = after.get("box") or (0, 0, 0, 0)
            threading.Thread(target=self.someone, args=(who, when, box), daemon=True).start()

    def someone(self, who, when, box):
        at = datetime.fromtimestamp(when).strftime("%H:%M:%S")
        notify(f"***Someone at the cars***\n{at} · there {LOITER} s and more", "warning",
               [("person.jpg", self.near(box)), ("street.jpg", picture())])
        log(f"told: someone at the cars at {at} ({who})")
        # From when they came near, to a little after.
        video = self.later_clip(when - LOITER - BEFORE, when + AFTER)
        notify(f"***Someone at the cars***\n{at}" + ("" if video else " · no clip"), "warning",
               [("person.mp4", video)])

    @staticmethod
    def near(box):
        try:
            return closeup(box)
        except Exception as error:  # a picture less never loses the message
            log(f"closeup: {error}")
            return None

    @staticmethod
    def later_clip(start, end):
        """The clip, once Frigate has written it (in 10 s pieces, a little
        after); None if it never comes."""
        time.sleep(max(0, end + 25 - time.time()))
        for _ in range(3):
            video = clip(start, end)
            if video:
                return video
            time.sleep(20)
        return None

    def follow(self, after, car, when, here, box):
        """The cars known by their sub label, wherever they park."""
        label = after.get("sub_label")
        label = label[0] if isinstance(label, (list, tuple)) and label else label
        if label not in FOLLOW:
            return
        name = FOLLOW[label]
        if after.get("stationary") and car not in self.stopped:
            self.stopped[car] = here
            # Parked after driving in; found already parked says nothing.
            if math.dist(self.origin[car], here) >= MOVE:
                self.tell(label, name, "parked", car, when, box)
            else:
                log(f"{name}: found parked ({car})")
        elif (car in self.stopped and not after.get("stationary")
              and math.dist(self.stopped[car], here) >= MOVE):
            self.stopped.pop(car)
            self.tell(label, name, "left", car, when, box)

    def tell(self, key, name, kind, car, when, box):
        """kind: "arrived" (in its spot), "parked" (anywhere) or "left"."""
        before = self.last.get(key)
        self.last[key] = (kind, when)
        # Off the MQTT loop: the clip waits for the recording, and the next car
        # must not wait for it.
        threading.Thread(target=self.report, args=(key, name, kind, car, when, box, before),
                         daemon=True).start()

    def report(self, key, name, kind, car, when, box, before):
        at = datetime.fromtimestamp(when).strftime("%H:%M:%S")
        title = {"left": f"{name} left", "arrived": f"{name} is back", "parked": f"{name} parked"}[kind]
        line = at
        if before and (before[0] == "left") != (kind == "left"):
            line += f" · {'parked' if kind == 'left' else 'away'} {duration(when - before[1])}"
        notify(f"***{title}***\n{line}", "info" if kind == "left" else "success",
               [(f"{key}.jpg", self.near(box)), ("street.jpg", picture())])
        log(f"told: {name} {kind} at {at} ({car})")

        video = self.later_clip(when - BEFORE, when + AFTER)
        if car is None:
            way = self.way(key, kind, when)
        else:
            where = (self.seen if kind == "left" else self.origin).get(car)
            way = WAYS["left" if where and where[0] < WIDTH / 2 else "right"] if where else None
        if kind == "left":
            words = f"***{name} went {way}***" if way else f"***{title}***"
        else:
            words = f"***{name} came from the {way}***" if way else f"***{title}***"
        notify(f"{words}\n{at}" + ("" if video else " · no clip"), "info", [(f"{key}.mp4", video)])
        log(f"told: {name} {kind} {way}, clip {len(video or b'')} bytes")


def main():
    watch = Watch()
    log(f"watching {CAMERA} on {BROKER}:{PORT}; spots {SPOTS}")
    while True:
        try:
            mqtt = Mqtt(BROKER, PORT, "frigate/events")
            log("connected")
            looked = 0.0
            while True:
                for payload in mqtt.messages(1):
                    try:
                        watch.event(json.loads(payload))
                    except (ValueError, KeyError, TypeError) as error:
                        log(f"event: {error}")
                if time.time() - looked >= POLL:
                    looked = time.time()
                    watch.check(looked)
                with open(HEARTBEAT, "w") as out:
                    out.write(str(int(time.time())))
        except (OSError, ConnectionError) as error:
            log(f"mqtt: {error}; again in 10s")
            time.sleep(10)


if __name__ == "__main__":
    main()
