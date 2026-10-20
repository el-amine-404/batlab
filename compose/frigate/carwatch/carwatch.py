"""Says when a car leaves the Clio 4's or the Ford's parking spot on the
street, and when a car parks there: any car, the spot is what is watched.

Every CARWATCH_POLL seconds it looks at Frigate's latest frame of the street.
Each spot (Frigate's zones, drawn around each parked car) is a patch of that
frame. Once nothing has moved in it for CARWATCH_SETTLE seconds it is
compared with how it looked when last settled. When it stays different for
CARWATCH_CONFIRM seconds (CARWATCH_QUICK by day, when Frigate and the model
both saw the car do it), and a car drove in or out of it then, that is told:
"car left" when the car Frigate followed went from inside the spot to a car
length away, "car arrived" when it came from there and stopped inside.
Neither alone is enough. Frigate hands a parked car's identity to a car
driving past, and loses parked cars and finds them again, so it "drives out"
of a spot every few minutes. A spot changes its look with a person or a
shadow on it, or a door open. When the whole picture changes at once (the
camera's infrared at dusk and dawn, a cloud), that is the light: it is taken
as the spot's new look.

In daylight the model Frigate uses (YOLO, on 9 squares of three sizes around
the spots) also says whether a car covers CARWATCH_FILL of each spot. A look
that finds fewer than CARWATCH_MIN_CARS cars around the spots, where a row of
them is always parked, missed them and says nothing; at night, in the
infrared, it finds none at all. When it sees, it overrules the patches: no
"car left" while it sees the car there, and no "car arrived" while it sees
the spot empty. It also tells what Frigate missed, a car gone or come while
the camera was off or Frigate lost it: the spot then reads the other way on
90% of the model's looks for CARWATCH_DRIFT seconds.

Each message comes once the car has settled, with a close-up from the 4K
stream, the street, which way the car went (or came from), and the clip. The
clip is cut from carwatch's own recording of the sub stream, the last
CARWATCH_BUFFER seconds kept in /tmp: Frigate records the street only around
its detections, which a car leaving is not always one of. A message without
a clip says nothing of one.

A car with no spot of its own (the Sandero) is known by its look instead:
Frigate's classifier (Settings > Classification) gives it a sub label, here
CARWATCH_FOLLOW's. It is told parked wherever it stops after driving in, and
gone when it drives a car length away from there.

It also says when someone stays at the cars: Frigate puts a person in the
CARWATCH_PEOPLE_ZONE only after its loitering time there (30 s).

Messages go through Apprise (compose/apprise) to the "cameras" channel. It
runs in Frigate's image, for OpenVINO, OpenCV and ffmpeg; batlab_common
(lib/batlab-common) besides.
"""

import glob
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
# The stream recorded for the clips: go2rtc's sub stream, H.264 that Discord
# plays as it is, about 600 kbit/s.
STREAM = os.environ.get("CARWATCH_STREAM", "rtsp://frigate:8554/camera_2_sub")
BUFFER = int(os.environ.get("CARWATCH_BUFFER", "300"))
CLIPS = "/tmp/clips"
# Which way a car went: the side of the picture it was last seen on (first
# seen, for one arriving).
WAYS = dict(pair.split("=", 1) for pair in
            os.environ.get("CARWATCH_WAYS", "left=left,right=right").split(","))
# Seconds of the clip before and after the moment told; a spot's clip runs to
# when the car settled, at most LONGEST seconds.
BEFORE, AFTER, LONGEST = 10, 15, 60
# Frigate's model, read from its config folder.
MODEL = os.environ.get("CARWATCH_MODEL", "/models/yolov9-s-320.onnx")
MODEL_SIZE = 320
CAR = 2  # COCO's car
POLL = float(os.environ.get("CARWATCH_POLL", "2"))
SETTLE = float(os.environ.get("CARWATCH_SETTLE", "6"))
CONFIRM = float(os.environ.get("CARWATCH_CONFIRM", "20"))
# By day, a change Frigate followed a car making and the model sees too is
# told after QUICK seconds instead: both already vouch for it.
QUICK = float(os.environ.get("CARWATCH_QUICK", "2"))
# How far apart two looks at a spot may be (mean difference in grey levels,
# the light evened out): STILL between looks a few seconds apart, SAME against
# its settled look. A car coming or going changes it by CHANGE at least (25 to
# 36 on 2026-10-19; a person or a door, under 10).
STILL = float(os.environ.get("CARWATCH_STILL", "6"))
SAME = float(os.environ.get("CARWATCH_SAME", "14"))
CHANGE = float(os.environ.get("CARWATCH_CHANGE", "18"))
# When more than this share of the whole picture changed since the spot's
# settled look, the light changed (the infrared at dusk, a cloud); a car
# driving through changes under 15%.
LIGHT = float(os.environ.get("CARWATCH_LIGHT", "0.5"))
# A car's path at a spot: Frigate's points of a parked car fall within NEAR
# of the spot's middle (63 to 113 on 2026-10-18 and 19), and one that drove
# off is lost from AWAY on (142 at the least); pixels of the detect frame.
NEAR, AWAY = 120, 130
# At night (the camera in infrared, its frames without colour) Frigate
# follows almost no car and the model sees none. A spot is told changed then
# when its shape changed (likeness to its settled look under STRUCTURE), a
# car-sized patch moved around it first (CARSIZE of the spot's area, within
# BAND pixels of it), and the light went the way a car makes it: in the
# infrared the cars there are darker than the paving (76 to 96 against 100 to
# 120 on 2026-10-19), so a car gone leaves the spot DARKER brighter, a car come
# makes it that much darker. A car against the paving there: likeness 0.02 at
# most; headlights on a parked car change its light, not its shape (0.97). A
# white car coming or going at night is told in the morning, by the model.
STRUCTURE, CARSIZE, BAND, DARKER = 0.5, 0.4, 120, 0.05
# How long a change with no car seen driving waits for one: a car that pulls
# out, then stops in the street for someone, is followed only when it goes.
PENDING = int(os.environ.get("CARWATCH_PENDING", "300"))
DRIFT = int(os.environ.get("CARWATCH_DRIFT", "300"))
# OpenVINO's device for the model: the iGPU, else the CPU.
DEVICE = os.environ.get("CARWATCH_DEVICE", "GPU")
FILL = float(os.environ.get("CARWATCH_FILL", "0.4"))
# Fewer cars than that in a look: the model missed them, the spots are unknown.
MIN_CARS = int(os.environ.get("CARWATCH_MIN_CARS", "4"))
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
# Discord takes 10 MB a message.
LARGEST = 9_000_000
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


class Recorder:
    """The sub stream, copied into 5 s pieces named by their start time, the
    last BUFFER seconds of them kept: the clips are cut from these."""

    def __init__(self):
        self.process = None
        self.started = 0.0

    def check(self):
        """Starts ffmpeg, again when it stopped or stopped writing; drops old
        pieces."""
        os.makedirs(CLIPS, exist_ok=True)
        pieces = self.pieces()
        for when, path in pieces:
            if when < time.time() - BUFFER:
                os.remove(path)
        newest = pieces[-1][0] if pieces else 0
        stuck = time.time() - max(newest, self.started) > 60
        if self.process and self.process.poll() is None and not stuck:
            return
        if self.process and self.process.poll() is None:
            log("recorder: no piece written for a minute; starting it again")
            self.process.kill()
            self.process.wait()
        self.started = time.time()
        self.process = subprocess.Popen(
            [FFMPEG, "-v", "error", "-rtsp_transport", "tcp", "-i", STREAM, "-map", "0:v", "-c", "copy",
             "-f", "segment", "-segment_time", "5", "-reset_timestamps", "1", "-strftime", "1",
             f"{CLIPS}/%s.ts"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)

    @staticmethod
    def pieces():
        found = []
        for path in glob.glob(f"{CLIPS}/*.ts"):
            try:
                found.append((int(os.path.basename(path)[:-3]), path))
            except ValueError:
                pass
        return sorted(found)

    def clip(self, start, end):
        """The recording from start to end as an MP4 Discord plays, or None:
        waits for end to be written first."""
        time.sleep(max(0, end + 6 - time.time()))
        pieces = self.pieces()
        chosen = [(when, path) for i, (when, path) in enumerate(pieces)
                  if when <= end and (i + 1 == len(pieces) or pieces[i + 1][0] > start)]
        # The recording must reach both ends, without a gap of more than a piece.
        if (not chosen or chosen[0][0] > start + 2 or chosen[-1][0] + 5 < end
                or any(b[0] - a[0] > 8 for a, b in zip(chosen, chosen[1:]))):
            log(f"clip: no recording from {start:.0f} to {end:.0f}")
            return None
        with tempfile.TemporaryDirectory() as folder:
            made, pieces = os.path.join(folder, "clip.mp4"), os.path.join(folder, "pieces.txt")
            # The concat demuxer: each piece's times start at 0, and it lays
            # them end to end.
            with open(pieces, "w") as out:
                out.writelines(f"file '{path}'\n" for _, path in chosen)
            done = subprocess.run(
                [FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", pieces,
                 "-ss", str(max(0, start - chosen[0][0])), "-t", str(end - start), "-an", "-c", "copy",
                 "-movflags", "+faststart", made], capture_output=True)
            if not done.returncode and os.path.getsize(made) > LARGEST:
                small = os.path.join(folder, "small.mp4")
                done = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", made, "-c:v", "libx264", "-preset",
                                       "veryfast", "-crf", "32", "-movflags", "+faststart", small],
                                      capture_output=True)
                made = small
            if done.returncode:
                log(f"clip: {done.stderr.decode(errors='replace')[-300:]}")
                return None
            with open(made, "rb") as file:
                data = file.read()
        return data if len(data) <= LARGEST else None


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


def gray(frame):
    """A frame as the spots are compared: grey, smoothed of the stream's noise."""
    import cv2

    frame = cv2.resize(frame, (WIDTH, HEIGHT)) if frame.shape[:2] != (HEIGHT, WIDTH) else frame
    return cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (5, 5), 0)


def scene(grey):
    """The whole picture, small, to tell the light changing from a spot."""
    import cv2
    import numpy

    return cv2.resize(grey, (96, 58), interpolation=cv2.INTER_AREA).astype(numpy.float32)


def colourless(frame):
    """Whether the camera is in infrared: its frames have no colour then."""
    import numpy

    f = frame[::4, ::4].astype(numpy.int16)
    return float(numpy.abs(f[..., 0] - f[..., 1]).mean() + numpy.abs(f[..., 1] - f[..., 2]).mean()) < 2


def likeness(a, b):
    """How alike two looks are in shape, the light aside: 1 the same, 0 unrelated."""
    import numpy

    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (numpy.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))


def changed(a, b):
    """The share of two small pictures' pixels that changed by more than a
    little: almost all when the light did."""
    import numpy

    return float((numpy.abs(a - b) > 12).mean())


def difference(a, b):
    """The mean difference of two looks at a spot in grey levels, b's light
    brought to a's."""
    import numpy

    return float(numpy.abs(a - b * (a.mean() / max(b.mean(), 1.0))).mean())


class Spot:
    """One parking spot: its look when last settled, and whether a car is in it."""

    def __init__(self, zone, name, polygon):
        import cv2
        import numpy

        polygon = polygon.astype(numpy.float32)  # what OpenCV takes
        self.zone, self.name, self.polygon = zone, name, polygon
        x, y, w, h = cv2.boundingRect(polygon.astype(numpy.int32))
        self.box = (x, y, x + w, y + h)
        mask = numpy.zeros((HEIGHT, WIDTH), numpy.uint8)
        cv2.fillPoly(mask, [polygon.astype(numpy.int32)], 1)
        self.mask = mask[y:y + h, x:x + w].astype(bool)
        self.middle = polygon.mean(0)
        self.area = int(self.mask.sum())
        # Around it, where a car coming or going moves.
        self.band = (max(x - BAND, 0), max(y - BAND, 0), min(x + w + BAND, WIDTH), min(y + h + BAND, HEIGHT))
        self.held = None      # True: a car, False: empty, None: not known yet
        self.ref = None       # its pixels when last settled
        self.scene = None     # the whole picture then
        self.calm = None      # when it last looked as settled
        self.recent = []      # [(when, pixels)] of the last SETTLE seconds
        self.since = None     # when it first looked settled and different
        self.pending = None   # when a change began that no car was seen making
        self.stir = 0         # the most pixels around it that moved between two looks, since calm
        self.seen = []        # [(when, a car)] of the model's looks that saw
        self.told = None      # ("arrived" | "left", when), last told

    def pixels(self, grey):
        import numpy

        x1, y1, x2, y2 = self.box
        return grey[y1:y2, x1:x2][self.mask].astype(numpy.float32)

    def model(self, now, seconds):
        """True or False when the model's looks of the last seconds agree on it
        (90%, and enough of them), None otherwise."""
        looks = [car for when, car in self.seen if when > now - seconds]
        if len(looks) < max(4, seconds / POLL / 4):
            return None
        share = sum(looks) / len(looks)
        return True if share >= 0.9 else False if share <= 0.1 else None


class Detector:
    """Whether a car covers each spot, by Frigate's model on the frame."""

    def __init__(self):
        self.model = None
        self.blind = None

    def load(self):
        import openvino

        if self.model is None:
            try:
                self.model = openvino.Core().compile_model(MODEL, DEVICE)
            except RuntimeError as error:
                log(f"model on {DEVICE}: {error}; on the CPU")
                self.model = openvino.Core().compile_model(MODEL, "CPU")

    def cars(self, frame, spots):
        """The cars' boxes around the spots: the model on squares of three
        sizes, each at three places, merged."""
        import cv2
        import numpy

        self.load()
        points = numpy.concatenate([spot.polygon for spot in spots])
        middle = (points.min(0) + points.max(0)) / 2
        found, scores = [], []
        for side in (400, 480, 576):
            side = min(side, HEIGHT)
            for shift in (-60, 0, 60):
                left = int(min(max(middle[0] - side / 2 + shift, 0), WIDTH - side))
                top = int(min(max(middle[1] - side / 2, 0), HEIGHT - side))
                crop = cv2.resize(frame[top:top + side, left:left + side], (MODEL_SIZE, MODEL_SIZE))
                tensor = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)[None].astype(numpy.float32) / 255
                rows = self.model([tensor])[0][0].T
                rows = rows[rows[:, 4 + CAR] > 0.4]
                scale = side / MODEL_SIZE
                for cx, cy, w, h, score in zip(*rows[:, :4].T, rows[:, 4 + CAR]):
                    found.append([(cx - w / 2) * scale + left, (cy - h / 2) * scale + top, w * scale, h * scale])
                    scores.append(float(score))
        keep = cv2.dnn.NMSBoxes(found, scores, 0.4, 0.5) if found else []
        return [(x, y, x + w, y + h) for x, y, w, h in (found[i] for i in numpy.array(keep).flatten())]

    def look(self, frame, spots):
        """spot zone -> whether a car covers FILL of it; None when the model
        missed the cars."""
        boxes = self.cars(frame, spots)
        blind = len(boxes) < MIN_CARS
        if blind != self.blind:
            log(f"the model {'misses the cars' if blind else 'sees the cars again'} ({len(boxes)} found)")
            self.blind = blind
        if blind:
            return None
        seen = {}
        for spot in spots:
            x1, y1, _, _ = spot.box
            best = 0.0
            for box in boxes:
                part = spot.mask[max(int(box[1]) - y1, 0):max(int(box[3]) - y1, 0),
                                 max(int(box[0]) - x1, 0):max(int(box[2]) - x1, 0)]
                best = max(best, part.sum() / spot.mask.sum())
            seen[spot.zone] = best >= FILL
        return seen


class Watch:
    def __init__(self):
        self.detector = Detector()
        self.recorder = Recorder()
        self.spots = []
        self.loaded = 0.0
        self.previous = None  # (when, the grey frame) of the last look
        self.alive, self.asked = False, float("-inf")
        self.trails = {}   # car (Frigate's id) -> [(when, x, y)] of its box's bottom middle
        self.last = {}     # followed car's label -> ("parked" | "left", when), last told
        self.origin = {}   # car -> where it was first seen; kept after it ends, until told
        self.seen = {}     # car -> where it was last seen; the same
        self.stopped = {}  # followed car -> where it is parked
        self.people = set()  # the people told as staying at the cars
        self.people_told = float("-inf")

    def zones(self):
        """The spots, from Frigate's zones; drawn in its UI, so read again now
        and then. A spot keeps what it knows when its zone stays the same."""
        import numpy

        if time.time() - self.loaded < 600 and self.spots:
            return
        config = json.loads(get(f"{FRIGATE}/api/config") or b"{}")
        zones = config["cameras"][CAMERA]["zones"]
        before = {spot.zone: spot for spot in self.spots}
        spots = []
        for zone, name in SPOTS.items():
            if zone not in zones:
                continue
            polygon = coordinates(zones[zone])
            old = before.get(zone)
            same = old is not None and old.polygon.shape == polygon.shape and numpy.allclose(old.polygon, polygon)
            spots.append(old if same else Spot(zone, name, polygon))
        self.spots = spots
        self.loaded = time.time()

    def check(self, now, frame=None):
        """Look at the spots; tell a spot that changed and settled, when a car
        drove in or out of it."""
        import cv2
        import numpy

        if frame is None:
            if not self.live(now):
                # A dark camera's last frame, or none, says nothing of the spots.
                for spot in self.spots:
                    spot.recent, spot.since = [], None
                return
            self.zones()
            data = get(f"{FRIGATE}/api/{CAMERA}/latest.jpg")
            frame = data and cv2.imdecode(numpy.frombuffer(data, numpy.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                return
        if not self.spots:
            return
        frame = cv2.resize(frame, (WIDTH, HEIGHT)) if frame.shape[:2] != (HEIGHT, WIDTH) else frame
        grey = gray(frame)
        whole = scene(grey)
        night = colourless(frame)
        model = None if night else self.detector.look(frame, self.spots)
        moved = None
        if self.previous is not None and now - self.previous[0] <= 3 * POLL:
            moved = cv2.absdiff(grey, self.previous[1]) > 25
        self.previous = (now, grey)
        for spot in self.spots:
            if model is not None:
                spot.seen = [s for s in spot.seen if s[0] > now - DRIFT] + [(now, model[spot.zone])]
            if moved is not None:
                x1, y1, x2, y2 = spot.band
                spot.stir = max(spot.stir, int(moved[y1:y2, x1:x2].sum()))
            self.spot(spot, now, spot.pixels(grey), whole, night)

    def spot(self, spot, now, pixels, whole, night=False):
        spot.recent = [r for r in spot.recent if r[0] > now - SETTLE] + [(now, pixels)]
        if any(difference(pixels, p) > STILL for _, p in spot.recent):
            spot.since = None  # something moves in it: wait for it to stop
            return
        if now - spot.recent[0][0] < SETTLE - POLL:
            return  # not still for long enough yet
        if spot.held is None:
            spot.held = spot.model(now, 60)
            if spot.held is not None:
                log(f"{spot.name}: {'a car' if spot.held else 'empty'}")
        if spot.ref is None or difference(pixels, spot.ref) <= SAME:
            spot.ref, spot.scene, spot.calm, spot.since, spot.stir = pixels, whole, now, None, 0
            self.waiting(spot, now)
            self.drift(spot, now)
            return
        if changed(whole, spot.scene) > LIGHT:
            log(f"{spot.name}: the light changed")
            spot.ref, spot.scene, spot.calm, spot.since, spot.stir = pixels, whole, now, None, 0
            return
        spot.since = spot.since or now
        if now - spot.since < CONFIRM and not (now - spot.since >= QUICK and not night
                                                and self.vouched(spot, now, pixels)):
            return
        start, before, stir = spot.calm or spot.since, spot.ref, spot.stir
        # Its new look, whatever made it.
        spot.ref, spot.scene, spot.calm, spot.since, spot.stir = pixels, whole, now, None, 0
        if difference(pixels, before) < CHANGE:
            return
        if night:
            self.night(spot, start, now, before, pixels, stir)
        else:
            self.decide(spot, start, now)

    def vouched(self, spot, now, pixels):
        """Whether Frigate followed a car into (or out of) the spot as it
        changed, and the model sees it so since it settled. When the model
        sees nothing (at dusk), CONFIRM it is: one of Frigate's tracks can
        jump from a parked car to the street in a second."""
        if spot.held is None or difference(pixels, spot.ref) < CHANGE:
            return False
        kind, _ = self.path(spot, (spot.calm or spot.since) - 30, now)
        return kind is not None and (kind == "arrived") != spot.held \
            and spot.model(now, SETTLE + QUICK) == (kind == "arrived")

    def decide(self, spot, start, now):
        """The spot changed from start to now: tell it when a car drove in or
        out then, and the model does not see otherwise."""
        kind, far = self.path(spot, start - 30, now)
        model = spot.model(now, CONFIRM)
        if kind is None and model is not None and spot.held is not None and model != spot.held:
            # Frigate missed the car moving; the model saw the spot change.
            kind = "arrived" if model else "left"
        if kind is None:
            spot.pending = start
            log(f"{spot.name}: looks different; waiting for a car seen driving in or out")
            return
        why = None
        if (kind == "arrived") == spot.held:
            why = f"a car {'came' if kind == 'arrived' else 'went'}, but it already {'held one' if spot.held else 'was empty'}"
        elif model is not None and model != (kind == "arrived"):
            why = f"the model sees it {'taken' if model else 'empty'}"
        if why:
            log(f"{spot.name}: looks different; not told, {why}")
            return
        spot.pending = None
        spot.held = kind == "arrived"
        self.tell(spot, kind, start, now, far)

    def night(self, spot, start, now, before, pixels, stir):
        """The spot changed from start to now, in the infrared: told when its
        shape changed, a car-sized patch moved around it, and it got brighter
        (the car left) or darker (one arrived)."""
        light = pixels.mean() / max(before.mean(), 1.0) - 1
        kind = "left" if light >= DARKER else "arrived" if light <= -DARKER else None
        why = None
        if likeness(pixels, before) > STRUCTURE:
            why = "its shape is the same (headlights?)"
        elif stir < CARSIZE * spot.area:
            why = "nothing car-sized moved"
        elif kind is None:
            why = "it got neither darker nor brighter"
        elif (kind == "arrived") == spot.held:
            why = f"it {'held a car' if spot.held else 'was empty'} already"
        else:
            path, far = self.path(spot, start - 30, now)
            if path is not None and path != kind:
                why = f"Frigate saw a car {'come' if path == 'arrived' else 'go'}"
        if why:
            log(f"{spot.name}: looks different at night; not told, {why}")
            return
        spot.pending = None
        spot.held = kind == "arrived"
        self.tell(spot, kind, start, now, self.path(spot, start - 30, now)[1])

    def waiting(self, spot, now):
        """A change no car was seen making: told when one is seen driving off
        (or in) within PENDING seconds."""
        if spot.pending is None:
            return
        if now - spot.pending > PENDING:
            log(f"{spot.name}: no car seen driving in or out; not told")
            spot.pending = None
            return
        kind, far = self.path(spot, spot.pending - 30, now)
        if kind is None or (kind == "arrived") == spot.held:
            return
        model = spot.model(now, CONFIRM)
        if model is not None and model != (kind == "arrived"):
            return
        start, spot.pending = spot.pending, None
        spot.held = kind == "arrived"
        self.tell(spot, kind, start, now, far)

    def drift(self, spot, now):
        """A change Frigate and the patches missed (the camera was off, or the
        car left at night unfollowed), told once the model sees it for DRIFT
        seconds."""
        model = spot.model(now, DRIFT)
        if model is None or spot.held is None or model == spot.held:
            return
        if now - spot.seen[0][0] < DRIFT - 2 * POLL:
            return  # not DRIFT seconds of looks yet
        spot.held, spot.pending = model, None
        kind = "arrived" if model else "left"
        log(f"{spot.name}: the model sees it {'taken' if model else 'empty'} for {DRIFT // 60} min")
        self.tell(spot, kind, None, now, None)

    def path(self, spot, start, end):
        """("left" | "arrived", the far end of its path) for a car Frigate
        followed from the spot to AWAY from it, or the other way, between start
        and end; (None, None) if none. A car driving past starts and ends away."""
        found = {}
        for trail in list(self.trails.values()):
            points = [p for p in trail if start <= p[0] <= end]
            if len(points) < 2:
                continue
            away = [math.dist((x, y), spot.middle) for _, x, y in points]
            if away[0] <= NEAR and max(away) >= AWAY:
                found["left"] = points[-1][1:]
            if away[-1] <= NEAR and away[0] >= AWAY:
                found["arrived"] = points[0][1:]
        if len(found) == 1:
            return next(iter(found.items()))
        if len(found) == 2:
            # Both (a car out and another in): the one that changes the spot.
            kind = "arrived" if spot.held is False else "left"
            return kind, found[kind]
        return None, None

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
        # Ten minutes of paths: enough for a spot's change, told after it settled.
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
        pictures = [("person.jpg", self.near(box)), ("street.jpg", picture())]
        # From when they came near, to a little after.
        video = self.recorder.clip(when - LOITER - BEFORE, when + AFTER)
        notify(f"***Someone at the cars***\n{at} · there {LOITER} s and more", "warning",
               pictures + [("person.mp4", video)])
        log(f"told: someone at the cars at {at} ({who}), clip {len(video or b'')} bytes")

    @staticmethod
    def near(box):
        try:
            return closeup(box)
        except Exception as error:  # a picture less never loses the message
            log(f"closeup: {error}")
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
                self.followed(label, name, "parked", car, when, box)
            else:
                log(f"{name}: found parked ({car})")
        elif (car in self.stopped and not after.get("stationary")
              and math.dist(self.stopped[car], here) >= MOVE):
            self.stopped.pop(car)
            self.followed(label, name, "left", car, when, box)

    def followed(self, label, name, kind, car, when, box):
        """kind: "parked" (anywhere) or "left"."""
        before = self.last.get(label)
        self.last[label] = (kind, when)
        title = f"{name} {kind}"
        line = datetime.fromtimestamp(when).strftime("%H:%M:%S")
        if before and (before[0] == "left") != (kind == "left"):
            line += f" · {'parked' if kind == 'left' else 'away'} {duration(when - before[1])}"
        where = (self.seen if kind == "left" else self.origin).get(car)
        way = WAYS["left" if where[0] < WIDTH / 2 else "right"] if where else None
        pictures = [(f"{label}.jpg", self.near(box)), ("street.jpg", picture())]
        # Off the MQTT loop: the clip waits for the recording.
        threading.Thread(target=self.report, args=(title, line, kind, way, pictures, label, when - BEFORE,
                                                   when + AFTER), daemon=True).start()

    def tell(self, spot, kind, start, end, far):
        """A spot's car arrived or left: start is when the spot began to
        change (None when not seen), end when it settled, far where the car
        came from or went."""
        before = spot.told
        when = start or end
        spot.told = (kind, when)
        title = f"{spot.name} · car {kind}"
        line = datetime.fromtimestamp(when).strftime("%H:%M:%S")
        if start is None:
            line = f"seen {line}, not seen {'leaving' if kind == 'left' else 'arriving'}"
        if before and before[0] != kind:
            line += f" · {'taken' if kind == 'left' else 'empty'} for {duration(when - before[1])}"
        way = WAYS["left" if far[0] < WIDTH / 2 else "right"] if far else None
        x1, y1, x2, y2 = spot.box
        pictures = [(f"{spot.zone}.jpg", self.near((x1, y1, x2, y2))), ("street.jpg", picture())]
        clip = (start - BEFORE, min(end, start + LONGEST)) if start else None
        threading.Thread(target=self.report, args=(title, line, kind, way, pictures, spot.zone) + (clip or (None, None)),
                         daemon=True).start()

    def report(self, title, line, kind, way, pictures, name, start, end):
        """One message: what happened, the pictures and the clip."""
        if way:
            title += f" · went {way}" if kind == "left" else f" · came from the {way}"
        video = self.recorder.clip(start, end) if start is not None else None
        notify(f"***{title}***\n{line}", "info" if kind == "left" else "success",
               pictures + [(f"{name}.mp4", video)])
        log(f"told: {title} · {line}, clip {len(video or b'')} bytes")


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
                    watch.recorder.check()
                    try:
                        watch.check(looked)
                    except Exception as error:  # a missed look is caught up by the next
                        log(f"look: {error}")
                with open(HEARTBEAT, "w") as out:
                    out.write(str(int(time.time())))
        except (OSError, ConnectionError) as error:
            log(f"mqtt: {error}; again in 10s")
            time.sleep(10)


if __name__ == "__main__":
    main()
