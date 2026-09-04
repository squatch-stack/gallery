"""Offline numerical and CLI checks for field capture plans."""
import importlib.util
import json
import math
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("capture_plan", ROOT / "tools/capture_plan.py")
planner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(planner)


class CapturePlanTests(unittest.TestCase):
    def test_hand_computed_overlap(self):
        # HFOV 90 degrees, r=2, d=1 => W=2. At 60 degrees chord=2,
        # which exceeds the allowed chord 1 for 50% overlap. The limiting
        # angle is 2 asin(1/4)=28.955 degrees => ceil(360/28.955)=13 stations.
        ring = planner.ring_geometry(2, 1, math.pi / 2, .5, 60)
        self.assertEqual(ring["stations"], 13)
        self.assertAlmostEqual(ring["footprint_m"], 2)
        self.assertAlmostEqual(ring["angular_step_deg"], 360 / 13)
        self.assertAlmostEqual(ring["overlap_fraction"], 1 - 2 * math.sin(math.pi / 13))
        self.assertGreaterEqual(ring["overlap_fraction"], .5)

    def test_ring_growth_and_overlap(self):
        small = planner.make_plan("object", 2)
        large = planner.make_plan("building", 12)
        self.assertGreater(len(large["rings"]), len(small["rings"]))
        for plan in (small, large, planner.make_plan("object", 2, 20)):
            self.assertGreaterEqual(len({r["radius_from_centre_m"] for r in plan["rings"]}), 2)
            self.assertGreaterEqual(len({r["camera_height_m"] for r in plan["rings"]}), 2)
            for ring in plan["rings"]:
                self.assertGreaterEqual(ring["overlap_fraction"], plan["geometry"]["overlap_target"])
                self.assertGreater(ring["overlap_fraction"], plan["geometry"]["guide_overlap_floor"])
                self.assertLessEqual(ring["angular_step_deg"], 15)
            self.assertEqual(plan["total_photographs"], sum(r["stations"] for r in plan["rings"]))

    def test_type_warnings(self):
        for subject_type in planner.TYPES:
            warnings = " ".join(planner.make_plan(subject_type, 2)["warnings"])
            for term in ("Vegetation in wind", "subject masks", "Thin orbits", "symmetric", "incremental", "global"):
                self.assertIn(term, warnings)
            self.assertIn("docs/capture.md > At the subject", warnings)
            if subject_type == "interior":
                self.assertIn("start with a connected overview", warnings)
                self.assertIn("operator default", warnings)
            if subject_type in ("building", "site"):
                self.assertIn("close-ups of walls alone", warnings)

    def test_defaults_have_sources(self):
        guide = (ROOT / "docs/capture.md").read_text()
        for key, item in planner.load_defaults().items():
            with self.subTest(key=key):
                self.assertIn("value", item)
                self.assertTrue(item["source"])
                self.assertTrue(item["rationale"])
                if item["source"] != "operator default":
                    self.assertIn("## " + item["source"], guide)
        plan = planner.make_plan("object", 2)
        self.assertEqual(set(plan["operator_defaults"]), {
            key for key, item in plan["defaults"].items() if item["source"] == "operator default"
        })

    def test_cli_json_and_file(self):
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "plan.json"
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/capture_plan.py"), "--type", "building", "--size", "12",
                 "--height", "4", "--json", "--out", str(dest)],
                check=True, capture_output=True, text=True, cwd=tmp,
            )
            self.assertEqual(result.stdout, dest.read_text())
            plan = json.loads(result.stdout)
            self.assertEqual(plan["subject_type"], "building")
            self.assertEqual(plan["height_m"], 4)
            self.assertFalse(plan["height_assumed"])
            for key in ("rings", "warnings", "checklist", "defaults", "operator_defaults", "geometry"):
                self.assertTrue(plan[key])
            self.assertAlmostEqual(plan["time_minutes"], plan["total_stations"] / plan["stations_per_minute"])

    def test_invalid_dimensions(self):
        for size, height in ((0, None), (-1, None), (math.nan, None), (math.inf, None), (2, 0), (2, math.nan)):
            with self.subTest(size=size, height=height), self.assertRaises(ValueError):
                planner.make_plan("object", size, height)

    def test_markdown(self):
        plan = planner.make_plan("object", 2)
        output = planner.render_markdown(plan)
        for term in ("## Field checklist", "HFOV =", "chord =", "EXIF", "zoom", "scale reference", "provenance"):
            self.assertIn(term, output)
        for key in plan["operator_defaults"]:
            self.assertIn(key, output)


if __name__ == "__main__":
    unittest.main()
