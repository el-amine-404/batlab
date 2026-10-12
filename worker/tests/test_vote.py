import importlib.util
import os
import sys
import unittest

# The pipeline module without the heavy imports of the package around it.
here = os.path.join(os.path.dirname(__file__), "..", "stanzarr_worker")
sys.modules.setdefault("stanzarr_worker", type(sys)("stanzarr_worker"))
gpu_spec = importlib.util.spec_from_file_location("stanzarr_worker.gpu", os.path.join(here, "gpu.py"))
sys.modules["stanzarr_worker.gpu"] = importlib.util.module_from_spec(gpu_spec)
gpu_spec.loader.exec_module(sys.modules["stanzarr_worker.gpu"])
spec = importlib.util.spec_from_file_location("stanzarr_worker.pipeline", os.path.join(here, "pipeline.py"))
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class Vote(unittest.TestCase):
    def test_median_wins_and_agreement_is_sure(self):
        out = pipeline.vote([[1.0, 5.0], [1.2, 9.0], [1.1, 5.1]], 2)
        self.assertEqual(out[0], {"t": 1.1, "u": False})
        self.assertEqual(out[1]["t"], 5.1)
        self.assertTrue(out[1]["u"], "one method 4 s away: unsure")

    def test_a_line_nobody_placed_goes_between_its_neighbours(self):
        out = pipeline.vote([[1.0, None, 5.0], [1.0, None, 5.0], [1.0, None, 5.0]], 3)
        self.assertEqual(out[1], {"t": 3.0, "u": True})


if __name__ == "__main__":
    unittest.main()
