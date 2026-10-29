import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1]))

from scanloom.imaging import (detect, detect_orientation, normalize, order_corners,
                              pixel_corners, quality, warp, write_png)
from scanloom.security import Refused, inspect_container
from scanloom.settings import DEFAULTS, validate


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
            path.write_bytes(encoded.tobytes())
            self.assertEqual(inspect_container(path)[0], "image/jpeg")
            path.write_bytes(path.read_bytes() + b"PK\x03\x04hidden")
            with self.assertRaisesRegex(Refused, "after"):
                inspect_container(path)

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
        with self.assertRaisesRegex(ValueError, "retention_hours"):
            validate({"retention_hours": 0})
        with self.assertRaisesRegex(ValueError, "Unknown"):
            validate({"surprise": True})


if __name__ == "__main__":
    unittest.main()
