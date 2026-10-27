import os
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1]))

from scanloom.imaging import detect, normalize, order_corners, pixel_corners, quality, warp
from scanloom.security import Refused, inspect_container
from scanloom.settings import DEFAULTS, validate


class ImagingTest(unittest.TestCase):
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
        found, confidence, method = detect(image)
        self.assertEqual(method, "contour")
        self.assertGreater(confidence, 0.75)
        result = warp(image, found, 0, "color")
        self.assertGreater(result.shape[1], result.shape[0])
        self.assertGreater(result.shape[0], 500)

    def test_corner_validation(self):
        points = pixel_corners([[0.1, .1], [.9, .1], [.9, .9], [.1, .9]], 1000, 800)
        self.assertEqual(points.shape, (4, 2))
        with self.assertRaisesRegex(ValueError, "too small"):
            pixel_corners([[.1,.1],[.11,.1],[.11,.11],[.1,.11]], 1000, 800)

    def test_quality_warns_about_blur(self):
        image = np.full((1000, 1000, 3), 180, np.uint8)
        report = quality(image, .95)
        self.assertTrue(any("blurry" in warning for warning in report["warnings"]))


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


class SettingsTest(unittest.TestCase):
    def test_defaults_and_limits(self):
        self.assertEqual(validate({}), DEFAULTS)
        with self.assertRaisesRegex(ValueError, "retention_hours"):
            validate({"retention_hours": 0})
        with self.assertRaisesRegex(ValueError, "Unknown"):
            validate({"surprise": True})


if __name__ == "__main__":
    unittest.main()
