"""Keep SOW tool references and acceptance check names tied to this branch."""

import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "docs/sow-template.md"
SPEC = importlib.util.spec_from_file_location("sow_checker", ROOT / "tools/check_deliverable.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class SowTemplateTests(unittest.TestCase):
    def test_named_python_tools_exist(self):
        references = set(re.findall(r"\btools/[\w/-]+\.py\b", TEMPLATE.read_text()))
        self.assertTrue(references, "No tool references found in the SOW")
        for reference in sorted(references):
            with self.subTest(tool=reference):
                self.assertTrue((ROOT / reference).is_file(), f"Missing SOW tool: {reference}")

    def test_acceptance_check_names_appear_in_checker_output(self):
        acceptance = TEMPLATE.read_text().split("## 6. Acceptance criteria\n", 1)[1].split("\n## 7.", 1)[0]
        # Bare identifiers in this section name checks, profiles or output statuses.
        identifiers = set(re.findall(r"`([a-z][a-z_]*)`", acceptance))
        cited = identifiers - {"object", "place", "info", "not_applicable"}
        self.assertTrue(cited, "No acceptance check names found")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scenes").mkdir()
            (root / "scenes.json").write_text(json.dumps([{"stem": "sample", "splats": 2}]))
            names = sorted(checker.REQUIRED)
            values = {name: 0 for name in names}
            values.update(opacity=4, scale_0=-6, scale_1=-6, scale_2=-6, rot_0=1)
            rows = []
            for position in (-1, 1):
                values.update(x=position, y=position, z=position)
                rows.append(" ".join(str(values[name]) for name in names))
            (root / "scenes/sample.ply").write_text(
                "ply\nformat ascii 1.0\nelement vertex 2\n"
                + "".join(f"property float {name}\n" for name in names)
                + "end_header\n"
                + "\n".join(rows)
                + "\n"
            )
            # Read real geometry for fraction names; unavailable geometry emits cleanliness.
            results = [checker.check_scene("sample", root=root, subject=profile) for profile in ("object", "place")]
            results.append(checker.check_scene("missing.ply", root=root))
            output_names = {check["name"] for result in results for check in result["checks"]}
        self.assertFalse(cited - output_names, f"Unknown acceptance check names: {sorted(cited - output_names)}")


if __name__ == "__main__":
    unittest.main()
