import logging
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1]))

from scanloom.imaging import (detect, detect_orientation, normalize, order_corners,
                              pixel_corners, quality, warp, write_png)
from scanloom.security import Refused, inspect_container
from scanloom.settings import DEFAULTS, validate


class PageSpecTest(unittest.TestCase):
    def test_what_people_write(self):
        from scanloom.pagespec import parse_pages
        self.assertEqual(parse_pages("1-3,6,7-9", 10), [1, 2, 3, 6, 7, 8, 9])
        self.assertEqual(parse_pages(" 1 - 2 , 4 ,", 4), [1, 2, 4])
        self.assertEqual(parse_pages("5,1", 5), [5, 1])
        self.assertEqual(parse_pages("9-7", 9), [9, 8, 7])
        self.assertEqual(parse_pages("1-3,2,3-4", 4), [1, 2, 3, 4])
        self.assertEqual(parse_pages("4", 4), [4])

    def test_what_is_refused_says_why(self):
        from scanloom.pagespec import parse_pages
        for spec, message in (("", "which pages"), (" , ", "which pages"), ("0", "no page 0"), ("11", "no page 11"), ("3-11", "no page 11"),
                              ("a", "isn't a page"), ("1-", "isn't a page"), ("-3", "isn't a page"), ("1--2", "isn't a page"),
                              ("1-2-3", "isn't a page"), ("1.5", "isn't a page"), ("1;2", "isn't a page"), ("9" * 9, "isn't a page")):
            with self.assertRaisesRegex(ValueError, message):
                parse_pages(spec, 10)


class ImagingTest(unittest.TestCase):
    def test_detection_cascade_prefers_neural_and_uses_segmentation_on_abstention(self):
        image = np.full((500, 800, 3), 80, np.uint8)
        neural = np.array([[20, 30], [780, 25], [770, 470], [25, 480]], np.float32)
        segmented = np.array([[100, 90], [700, 90], [700, 410], [100, 410]], np.float32)
        with mock.patch("scanloom.imaging._neural_boundary", return_value=(neural, .91)):
            found, confidence, method = detect(image)
        self.assertTrue(np.array_equal(found, neural))
        self.assertEqual((confidence, method), (.91, "DocAligner neural boundary"))
        with (mock.patch("scanloom.imaging._neural_boundary", return_value=None),
              mock.patch("scanloom.imaging._detect_classical",
                         return_value=(neural, .70, "minimum-area fallback")),
              mock.patch("scanloom.imaging._grabcut_boundary", return_value=(segmented, .81))):
            found, confidence, method = detect(image)
        self.assertTrue(np.array_equal(found, segmented))
        self.assertEqual((confidence, method), (.81, "GrabCut foreground fallback"))

    def test_a_sliver_is_not_a_page(self):
        image = np.full((1000, 1400, 3), 90, np.uint8)
        for bad in ([[460, 250], [1180, 270], [1184, 329], [456, 304]],  # a stray line in a photo
                    [[0, 24], [770, 62], [770, 62], [0, 73]]):           # two corners on one point
            quad = np.array(bad, np.float32)
            with mock.patch("scanloom.imaging._neural_boundary", return_value=(quad, .9)):
                found, confidence, method = detect(image)
            self.assertEqual(method, "no document found: whole photo")
            self.assertLess(confidence, .72)
            self.assertEqual(found[2].tolist(), [1399, 999])
        # Unsure, but a large four-sided thing: better than a fallback's guess inside it.
        phone = np.array([[300, 100], [1100, 110], [1080, 900], [280, 890]], np.float32)
        with (mock.patch("scanloom.imaging._neural_boundary", return_value=(phone, .25)),
              mock.patch("scanloom.imaging._edge_support", return_value=.2)):
            self.assertEqual(detect(image)[2], "DocAligner neural boundary")
        receipt = np.array([[600, 50], [760, 50], [760, 950], [600, 950]], np.float32)
        with mock.patch("scanloom.imaging._neural_boundary", return_value=(receipt, .9)):
            self.assertEqual(detect(image)[2], "DocAligner neural boundary")

    def test_orientation_changes_only_with_strong_text_evidence(self):
        image = np.full((300, 500, 3), 220, np.uint8)
        header = b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        weak = mock.Mock(stdout=header + b"5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t80\ttest\n")
        strong = mock.Mock(stdout=header + b"5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t95\torientationtest\n" * 3)
        with mock.patch("scanloom.imaging.subprocess.run",
                        side_effect=[weak, weak, weak, strong]):
            rotation, confidence = detect_orientation(image)
        self.assertEqual(rotation, 90)
        self.assertGreater(confidence, 1.75)
        with mock.patch("scanloom.imaging.subprocess.run", return_value=weak):
            self.assertEqual(detect_orientation(image), (0, 0.0))

    def test_normalizes_a_real_jpeg_loader_path(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.jpg"
            destination = Path(folder) / "normalized.png"
            source.write_bytes(cv2.imencode(".jpg", np.full((120, 200, 3), 180, np.uint8))[1].tobytes())
            self.assertEqual(normalize(source, destination, 1_000_000), (200, 120))
            self.assertEqual(cv2.imread(str(destination)).shape, (120, 200, 3))

    def test_normalizes_rotated_exif_jpeg(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "rotated.jpg"
            destination = Path(folder) / "normalized.png"
            img = Image.new("RGB", (600, 400), color=(180, 180, 180))
            exif = img.getexif()
            exif[0x0112] = 6  # 90 degrees CCW (requires 90 CW rotation)
            img.save(str(source), format="JPEG", exif=exif)
            w, h = normalize(source, destination, 5_000_000)
            self.assertEqual((w, h), (400, 600))
            self.assertTrue(destination.is_file())

    def test_orders_and_warps_a_document(self):
        image = np.full((900, 1200, 3), 35, np.uint8)
        quad = np.array([[180, 120], [1030, 190], [950, 760], [120, 700]], np.int32)
        cv2.fillConvexPoly(image, quad, (245, 245, 238))
        cv2.putText(image, "SCANLOOM", (330, 450), cv2.FONT_HERSHEY_SIMPLEX, 2, (20, 40, 35), 5)
        with (mock.patch("scanloom.imaging._neural_boundary", return_value=None),
              mock.patch("scanloom.imaging._grabcut_boundary", return_value=None)):
            found, confidence, method = detect(image)
        self.assertIn(method, {"contour", "full-resolution foreground"})
        self.assertGreater(confidence, 0.75)
        result = warp(image, found, 0, "color")
        self.assertGreater(result.shape[1], result.shape[0])
        self.assertGreater(result.shape[0], 500)

    def test_outer_card_wins_over_strong_internal_edges(self):
        image = np.full((1400, 700, 3), 28, np.uint8)
        cv2.rectangle(image, (70, 430), (640, 900), (145, 145, 145), -1)
        cv2.rectangle(image, (70, 430), (640, 590), (245, 245, 245), -1)
        with (mock.patch("scanloom.imaging._neural_boundary", return_value=None),
              mock.patch("scanloom.imaging._grabcut_boundary", return_value=None)):
            found, confidence, method = detect(image)
        self.assertEqual(method, "full-resolution foreground")
        self.assertGreater(confidence, .75)
        self.assertLess(found[:, 0].min(), 85)
        self.assertGreater(found[:, 0].max(), 625)
        self.assertLess(found[:, 1].min(), 445)
        self.assertGreater(found[:, 1].max(), 885)

    def test_dark_color_card_wins_over_bright_background(self):
        image = np.full((720, 1280, 3), (225, 225, 215), np.uint8)
        # Dark foreground at the top and bottom makes the bright wall own only
        # the side edges, matching a card held in front of a lit room.
        image[:105] = (35, 35, 35)
        image[675:] = (45, 45, 45)
        quad = np.array([[245, 140], [1040, 75], [1080, 655], [270, 625]], np.int32)
        cv2.fillConvexPoly(image, quad, (85, 35, 15))
        cv2.fillConvexPoly(image, np.array([[270, 475], [1065, 455], [1080, 655], [270, 625]]), (55, 150, 80))
        with (mock.patch("scanloom.imaging._neural_boundary", return_value=None),
              mock.patch("scanloom.imaging._grabcut_boundary", return_value=None)):
            found, confidence, method = detect(image)
        self.assertEqual(method, "color foreground")
        self.assertGreater(confidence, .8)
        self.assertLess(np.abs(found - quad).mean(), 45)

    def test_corner_validation(self):
        points = pixel_corners([[0.1, .1], [.9, .1], [.9, .9], [.1, .9]], 1000, 800)
        self.assertEqual(points.shape, (4, 2))
        outside = pixel_corners([[-.01, .1], [.9, .1], [.9, .9], [.1, .9]], 1000, 800)
        self.assertLess(outside[:, 0].min(), 0)
        with self.assertRaisesRegex(ValueError, "too small"):
            pixel_corners([[.1,.1],[.11,.1],[.11,.11],[.1,.11]], 1000, 800)
        with self.assertRaisesRegex(ValueError, "5%"):
            pixel_corners([[-.06,.1],[.9,.1],[.9,.9],[.1,.9]], 1000, 800)

    def test_quality_warns_about_blur(self):
        image = np.full((1000, 1000, 3), 180, np.uint8)
        report = quality(image, .95)
        self.assertTrue(any("blurry" in warning for warning in report["warnings"]))

    def test_rounded_card_is_written_as_lossless_alpha_png(self):
        image = np.full((500, 800, 3), (40, 180, 220), np.uint8)
        corners = np.array([[0, 0], [799, 0], [799, 499], [0, 499]], np.float32)
        result = warp(image, corners, 0, "color", .08)
        self.assertEqual(result.shape[2], 4)
        self.assertEqual(result[0, 0, 3], 0)
        self.assertEqual(result[250, 400, 3], 255)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rounded.png"
            write_png(result, path)
            decoded = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            self.assertEqual(decoded.shape, result.shape)


class ContainerTest(unittest.TestCase):
    def test_jpeg_exact_end_and_trailing_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "photo.jpg"
            ok, encoded = cv2.imencode(".jpg", np.full((100, 100, 3), 200, np.uint8))
            self.assertTrue(ok)
            clean_bytes = encoded.tobytes()
            path.write_bytes(clean_bytes)
            self.assertEqual(inspect_container(path)[0], "image/jpeg")

            # Trailing ZIP payload must be rejected
            path.write_bytes(clean_bytes + b"PK\x03\x04hidden")
            with self.assertRaisesRegex(Refused, "after"):
                inspect_container(path)

            # Trailing PDF payload must be rejected
            path.write_bytes(clean_bytes + b"%PDF-1.4malicious")
            with self.assertRaisesRegex(Refused, "after"):
                inspect_container(path)

            # Samsung SEFT camera trailer must be accepted and sanitized
            samsung_trailer = b"\x00\x00\x01\nSamsung_Capture_Info\x00\x00SEFT"
            path.write_bytes(clean_bytes + samsung_trailer)
            mime, report = inspect_container(path)
            self.assertEqual(mime, "image/jpeg")
            self.assertEqual(report["trailing_bytes"], len(samsung_trailer))
            self.assertTrue(path.read_bytes().endswith(b"\xff\xd9"))

            # Null byte padding must be accepted and sanitized
            null_padding = b"\x00" * 64
            path.write_bytes(clean_bytes + null_padding)
            mime, report = inspect_container(path)
            self.assertEqual(mime, "image/jpeg")
            self.assertEqual(report["trailing_bytes"], len(null_padding))
            self.assertTrue(path.read_bytes().endswith(b"\xff\xd9"))

    def test_png_exact_end(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "photo.png"
            path.write_bytes(cv2.imencode(".png", np.zeros((64,64,3),np.uint8))[1].tobytes())
            self.assertEqual(inspect_container(path)[0], "image/png")

    def test_heic_box_boundaries_and_brand(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "photo.heic"
            ftyp = b"heic" + b"\0\0\0\0" + b"mif1" + b"heic"
            path.write_bytes(struct.pack(">I4s", len(ftyp) + 8, b"ftyp") + ftyp
                             + struct.pack(">I4s", 12, b"free") + b"safe")
            media, report = inspect_container(path)
            self.assertEqual(media, "image/heic")
            self.assertEqual(report["trailing_bytes"], 0)
            path.write_bytes(path.read_bytes() + b"hidden")
            with self.assertRaisesRegex(Refused, "truncated|box size"):
                inspect_container(path)


class SettingsTest(unittest.TestCase):
    def test_defaults_and_limits(self):
        self.assertEqual(validate({}), DEFAULTS)
        with self.assertRaisesRegex(ValueError, "retention_days"):
            validate({"retention_days": -1})
        with self.assertRaisesRegex(ValueError, "Unknown"):
            validate({"surprise": True})


class LogsTest(unittest.TestCase):
    def test_logs_setup_and_recent_buffer(self):
        from scanloom import logs
        with tempfile.TemporaryDirectory() as folder:
            log_dir = Path(folder) / "logs"
            logs.setup(component="test-worker", logs_dir=log_dir)
            test_logger = logging.getLogger("scanloom.test")
            test_logger.info("Hello Scanloom test message")
            lines = logs.RECENT.lines(level="info", limit=10)
            self.assertTrue(any("Hello Scanloom test message" in l["message"] for l in lines))
            log_file = log_dir / "test-worker.log"
            self.assertTrue(log_file.is_file())
            content = log_file.read_text(encoding="utf-8")
            self.assertIn("Hello Scanloom test message", content)

    def test_log_rotation(self):
        from logging.handlers import RotatingFileHandler
        with tempfile.TemporaryDirectory() as folder:
            log_file = Path(folder) / "rot.log"
            handler = RotatingFileHandler(str(log_file), maxBytes=200, backupCount=2)
            logger = logging.getLogger("rot.test")
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            for i in range(20):
                logger.info("Message number %d with sufficient padding to exceed 200 bytes", i)
            handler.close()
            logger.removeHandler(handler)
            self.assertTrue(log_file.is_file())
            self.assertTrue(Path(folder, "rot.log.1").is_file())


if __name__ == "__main__":
    unittest.main()
