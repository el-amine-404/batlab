"""carwatch's decisions, on made-up looks: each test is a fault seen live.
Runs in Frigate's image (numpy, OpenCV, ffmpeg):

    docker run --rm -v ./compose/frigate/carwatch:/app:ro -v ./lib/batlab-common/batlab_common:/opt/batlab-common/batlab_common:ro \\
        -e PYTHONPATH=/opt/batlab-common -e PYTHONDONTWRITEBYTECODE=1 -w /app --entrypoint python3 <frigate image> -m unittest -v test_carwatch
"""
import os
import subprocess
import tempfile
import time
import unittest

import numpy

import carwatch as c


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        c.STATE = os.path.join(self.folder.name, "spots.json")
        c.log = lambda message: self.logged.append(message)
        c.picture = lambda: None
        self.logged, self.told = [], []
        self.watch = c.Watch()
        self.watch.near = lambda box: None
        self.watch.report = lambda title, line, kind, way, pictures, name, start, end: \
            self.told.append((title, line, start, end))
        c.threading.Thread = lambda target, args, daemon: type("T", (), {"start": lambda s: target(*args)})()
        self.spot = c.Spot("spot_2", "Ford spot", numpy.array([[540, 360], [640, 360], [640, 560], [540, 560]], float))
        self.watch.spots = [self.spot]

    def tearDown(self):
        self.folder.cleanup()

    def looks(self, start, end, car):
        """The model's looks every POLL seconds, from start to end."""
        for when in numpy.arange(start, end, c.POLL):
            self.spot.seen = [s for s in self.spot.seen if s[0] > when - c.DRIFT - 60] + [(float(when), car)]


class Drift(Base):
    def test_no_message_from_looks_before_a_change(self):
        # 2026-10-20 09:48: the Ford's departure was told, then drift read
        # the 5 min of looks before it and told "arrived".
        self.spot.held, self.spot.changed = True, 0.0
        self.looks(1000, 1300, True)
        self.watch.tell(self.spot, "left", 1290, 1300, None)
        self.watch.drift(self.spot, 1302)
        self.looks(1300, 1400, False)
        self.watch.drift(self.spot, 1400)
        self.assertEqual(len(self.told), 1)
        self.assertIs(self.spot.held, False)

    def test_tells_a_change_nobody_saw_once_seen_for_drift(self):
        self.spot.held, self.spot.changed = True, 0.0
        self.looks(1000, 1100, True)
        self.looks(1100, 1100 + c.DRIFT, False)
        self.watch.drift(self.spot, 1100 + c.DRIFT - c.POLL)
        self.assertEqual(len(self.told), 1)
        title, line, start, end = self.told[0]
        self.assertIn("car left", title)
        # Its clip is from when the model first saw it empty.
        self.assertEqual((start, end), (1100 - c.LOITER, 1100 + c.AFTER))


class Waiting(Base):
    def test_a_change_with_no_path_is_told_when_the_model_sees_it(self):
        # 2026-10-20 17:44: the Ford came back, Frigate followed no car, and
        # it was told 4 min later by drift, without a clip.
        self.spot.held, self.spot.changed = False, 0.0
        self.looks(900, 1000, False)
        self.watch.decide(self.spot, 990, 1000, 1000)
        self.assertEqual(self.spot.pending, 990)
        self.looks(1000, 1000 + c.WAIT + 2, True)
        self.watch.waiting(self.spot, 1000 + c.WAIT + 2)
        self.assertEqual(len(self.told), 1)
        self.assertIn("car arrived", self.told[0][0])
        self.assertEqual(self.told[0][2], 990 - c.BEFORE)

    def test_not_while_the_model_still_sees_it_as_before(self):
        self.spot.held, self.spot.changed = True, 0.0
        self.looks(900, 1000, True)
        self.watch.decide(self.spot, 990, 1000, 1000)
        self.looks(1000, 1000 + c.PENDING, True)
        self.watch.waiting(self.spot, 1000 + c.WAIT + 2)
        self.assertEqual(self.told, [])


class Veto(Base):
    def test_two_looks_since_it_settled_overrule_a_path(self):
        # 2026-10-20 15:21: the car beside the Clio left, its path started
        # near the Clio's spot; the model saw the Clio twice, too few looks
        # for 20 s.
        self.spot.held, self.spot.changed = True, 0.0
        self.watch.trails["x"] = [(985.0, *self.spot.middle), (995.0, 200.0, 100.0)]
        self.spot.seen = [(1002.0, True), (1006.0, True)]
        self.watch.decide(self.spot, 980, 1010, 1000)
        self.assertEqual(self.told, [])
        self.assertTrue(any("the model sees it taken" in m for m in self.logged))

    def test_a_path_is_told_when_the_model_agrees(self):
        self.spot.held, self.spot.changed = True, 0.0
        self.watch.trails["x"] = [(985.0, *self.spot.middle), (995.0, 200.0, 100.0)]
        self.spot.seen = [(1002.0, False), (1006.0, False)]
        self.watch.decide(self.spot, 980, 1010, 1000)
        self.assertEqual(len(self.told), 1)


class Texture(Base):
    def textures(self):
        """The spot's paving and a car in it, both under the sun's edge
        across the spot: its left half in the shade."""
        rng = numpy.random.default_rng(1)
        shape = self.spot.mask.shape
        light = numpy.where(numpy.arange(shape[1]) < shape[1] // 2, 60.0, 180.0)[None, :]
        paving = (light + rng.normal(0, 8, shape)).astype(numpy.float32)
        car = (light * 0.6 + rng.normal(0, 8, shape)).astype(numpy.float32)
        return paving, car

    def test_a_shadow_keeps_the_texture(self):
        paving, _ = self.textures()
        mask = self.spot.mask
        shaded = paving.copy()
        shaded[: shaded.shape[0] // 2] *= 0.5  # the car beside's shadow over half of it
        self.assertGreater(c.likeness(shaded[mask], paving[mask], mask), c.STRUCTURE)

    def test_a_car_gone_under_the_suns_edge_is_not_a_shadow(self):
        # 2026-10-21 09:37 and 16:45: the Ford left, the sun's edge across the
        # spot stayed; the whole spot read 0.64 and 0.56 alike, a shadow, and
        # the departures came 4 and 22 min late.
        paving, car = self.textures()
        mask = self.spot.mask
        self.assertGreater(c.likeness(paving[mask], car[mask]), c.STRUCTURE)
        self.assertLess(c.likeness(paving[mask], car[mask], mask), c.STRUCTURE)

    def test_no_path_counts_for_a_shadow(self):
        # 2026-10-20 15:21: the car beside the Clio drove off near its spot.
        paving, _ = self.textures()
        mask = self.spot.mask
        shaded = paving.copy()
        shaded[: shaded.shape[0] // 2] *= 0.5
        whole = numpy.zeros((58, 96), numpy.float32)
        self.spot.held, self.spot.changed = True, 0.0
        for when in range(1000, 1020, 2):
            self.watch.spot(self.spot, when, paving[mask], whole)
        self.watch.trails["x"] = [(1025.0, *self.spot.middle), (1030.0, 200.0, 100.0)]
        for when in range(1030, 1060, 2):
            self.watch.spot(self.spot, when, shaded[mask], whole)
        self.assertEqual(self.told, [])
        self.assertTrue(any("its texture is the same" in m for m in self.logged))


class Model(Base):
    def look(self, boxes):
        self.watch.detector.cars = lambda frame, spots: boxes
        return self.watch.detector.look(None, [self.clio, self.spot])

    def setUp(self):
        super().setUp()
        self.clio = c.Spot("spot_1", "Clio 4 spot", numpy.array([[420, 360], [520, 360], [520, 560], [420, 560]], float))

    def test_sees_with_few_cars_when_one_is_in_a_spot(self):
        # 2026-10-21 16:45: the Ford gone, it found 1 to 3 cars, the Clio
        # among them, and the Ford spot empty; 4 were needed, and the
        # departure was told 22 min late.
        self.assertEqual(self.look([(420, 360, 520, 560), (0, 0, 50, 50)]), {"spot_1": True, "spot_2": False})

    def test_blind_with_few_cars_and_none_in_a_spot(self):
        self.assertIsNone(self.look([(0, 0, 50, 50)]))

    def test_one_box_over_both_spots_is_not_a_car(self):
        # 2026-10-21 16:45:13, in the sun: one box over the row read the
        # Ford's empty spot taken.
        self.assertIsNone(self.look([(400, 340, 660, 580), (420, 360, 520, 560)] + [(0, 0, 50, 50)] * 3))


class State(Base):
    def test_a_restart_keeps_what_each_spot_holds(self):
        self.spot.held = True
        self.watch.tell(self.spot, "left", 1000, 1010, None)
        again = c.Spot("spot_2", "Ford spot", self.spot.polygon.copy())
        self.watch.restore(again)
        self.assertIs(again.held, False)
        self.assertEqual(again.changed, 1010)
        self.assertEqual(again.told, ("left", 1000))

    def test_not_when_its_zone_moved(self):
        self.spot.held = True
        self.watch.tell(self.spot, "left", 1000, 1010, None)
        moved = c.Spot("spot_2", "Ford spot", self.spot.polygon + 30)
        self.watch.restore(moved)
        self.assertIsNone(moved.held)


class Clip(unittest.TestCase):
    def test_made_to_fit_the_room_left(self):
        # 2026-10-20: clips of 1.6 to 3.6 MB, with the pictures, were more
        # than Apprise took, and the message was lost.
        with tempfile.TemporaryDirectory() as folder:
            c.CLIPS = folder
            now = int(time.time()) - 60
            for start in range(now, now + 30, 5):
                subprocess.run([c.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=960x576:rate=12",
                                "-t", "5", "-c:v", "libx264", "-b:v", "4M", "-f", "mpegts",
                                f"{folder}/{start}.ts"], check=True)
            whole = c.Recorder().clip(now, now + 30)
            self.assertGreater(len(whole), 6_000_000)
            small = c.Recorder().clip(now, now + 30, 2_000_000)
            self.assertLessEqual(len(small), 2_000_000)
            self.assertGreater(len(small), 500_000)


if __name__ == "__main__":
    unittest.main()
