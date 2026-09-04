"""Quote contract tests: tier boundaries, money, validation and CLI/SOW behavior."""

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("quote", ROOT / "tools/quote.py")
quote = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quote)


def job(subjects=1, days=1, **updates):
    data = {
        "name": "the pilot",
        "subjects": [{"name": f"Subject {i}", "type": "building"} for i in range(subjects)],
        "days_on_site": days,
    }
    data.update(updates)
    return quote.validate_job(data)


class QuoteTests(unittest.TestCase):
    def setUp(self):
        self.rates = quote.load_rates()

    def calc(self, **kwargs):
        return quote.estimate(job(**kwargs), self.rates)

    def test_baseline(self):
        result = self.calc()
        self.assertEqual((result["tier"], result["cash"]), ("Baseline", Decimal("2500.00")))

    def test_site_boundaries(self):
        for count, expected in [(3, "5000"), (4, "6500"), (5, "8000")]:
            with self.subTest(count=count):
                result = self.calc(subjects=count, days=2)
                self.assertEqual(result["tier"], "Site")
                self.assertEqual(result["cash"], Decimal(expected))
                self.assertTrue(result["access"])
                selected = self.calc(subjects=count, days=2, options={"accessibility_pass": True})
                self.assertEqual(selected["cash"], result["cash"])

    def test_program_boundaries(self):
        for count, days in [(2, 1), (1, 2), (3, 1), (6, 2), (5, 3), (1, 0.5)]:
            with self.subTest(count=count, days=days):
                result = self.calc(subjects=count, days=days)
                self.assertEqual(result["tier"], "Program")
                self.assertEqual(result["cash"], Decimal(str(days)) * 1500 + count * 1000)

    def test_types_do_not_silently_multiply_price(self):
        for kind in ["object", "building", "interior", "site"]:
            data = job()
            data["subjects"][0]["type"] = kind
            self.assertEqual(quote.estimate(data, self.rates)["cash"], Decimal(2500))

    def test_pilot_donation_and_gap(self):
        data = quote.load_job(ROOT / "docs/examples/pilot.json")
        result = quote.estimate(data, self.rates)
        self.assertEqual(
            (result["gross"], result["donation"], result["cash"], result["gap"]),
            tuple(map(Decimal, ["2500", "750", "1750", "1000"])),
        )
        self.assertEqual(self.calc(donated_capture_days=1, federal_request=1000)["gap"], Decimal(0))
        self.assertEqual(self.calc(federal_request=0)["request"], Decimal(0))

    def test_all_options_and_rush_basis(self):
        result = self.calc(
            donated_capture_days=0.5, travel_miles=100, hosting_months=12, options=dict.fromkeys(quote.OPTIONS, True)
        )
        # Services 2500 + 600 + 500 + 350; cash service basis 3200; rush 800; travel 75; hosting 300.
        self.assertEqual(result["gross"], Decimal(5125))
        self.assertEqual(result["cash"], Decimal(4375))
        self.assertEqual(sum(row[3] for row in result["lines"]), result["cash"] + result["donation"])

    def test_rounding(self):
        result = self.calc(travel_miles=0.02)
        self.assertEqual(result["cash"], Decimal("2500.02"))
        self.assertEqual(quote.money(Decimal("1.005")), "$1.01")

    def test_rates_loaded_at_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pricing.json"
            rates = json.loads(quote.RATES.read_text())
            rates["capture_day"] = 2000
            path.write_text(json.dumps(rates))
            result = quote.estimate(job(donated_capture_days=0.5), quote.load_rates(path))
            self.assertEqual((result["gross"], result["donation"]), (Decimal(3000), Decimal(1000)))

    def test_bad_jobs(self):
        base = {"name": "the pilot", "subjects": [{"name": "building", "type": "building"}], "days_on_site": 1}
        invalid = [None, [], {}, {**base, "subjects": []}, {**base, "days_on_site": 0}]
        for key, values in {
            "days_on_site": [-1, True, "1", float("nan"), float("inf")],
            "travel_miles": [-1, "10"],
            "hosting_months": [-1, 1.5, True],
            "donated_capture_days": [2, -1],
            "federal_request": [-1, True],
            "options": [[], {"mesh": "false"}, {"unknown": True}],
            "name": ["", "bad\nheading", "{{CASH_TOTAL}}"],
            "typo": [1],
            "subjects": [
                [{"name": "x", "type": "boat"}],
                [{"name": "x", "type": []}],
                [{"type": "site"}],
                ["building"],
                base["subjects"] * 2,
            ],
        }.items():
            invalid.extend({**base, key: value} for value in values)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises((TypeError, ValueError)):
                quote.validate_job(data)

    def test_bad_rates(self):
        original = json.loads(quote.RATES.read_text())
        for key, value in [
            ("capture_day", -1),
            ("day_hours", 0),
            ("rush_fraction", 2),
            ("currency", "EUR"),
            ("hosting_per_month", "25"),
        ]:
            with tempfile.TemporaryDirectory() as tmp, self.subTest(key=key):
                path = Path(tmp) / "rates.json"
                data = {**original, key: value}
                path.write_text(json.dumps(data))
                with self.assertRaises((TypeError, ValueError)):
                    quote.load_rates(path)

    def test_sow_populates_and_preserves_sources(self):
        data = job(name="A | B <C>", vendor="STUDIO", start_date="2026-10-01")
        result = quote.estimate(data, self.rates)
        rendered = quote.render_sow(data, self.rates, result)
        self.assertNotIn("{{", rendered)
        for text in [
            "STUDIO",
            "2026-10-01",
            "APPLICANT TO BE AGREED",
            "$2,500.00",
            "`floater`",
            "`fog`",
            "`translucent`",
            "`nonfinite`",
            "[^wcag]:",
            "200.315",
        ]:
            self.assertIn(text, rendered)
        self.assertIn(r"A \| B \<C\>", rendered)
        self.assertIn("NOT INCLUDED; NO CONFORMANCE CLAIM", rendered)
        data["options"]["accessibility_pass"] = True
        self.assertIn(
            "Accessibility pass: **INCLUDED**", quote.render_sow(data, self.rates, quote.estimate(data, self.rates))
        )

    def test_unknown_template_token_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "template.md"
            template.write_text("{{UNMAPPED}}")
            with self.assertRaises((TypeError, ValueError)):
                quote.render_sow(job(), self.rates, self.calc(), template)

    def test_cli_from_other_directory_and_failure_no_partial_quote(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = [sys.executable, str(ROOT / "tools/quote.py"), str(ROOT / "docs/examples/pilot.json"), "--sow"]
            result = subprocess.run(command, cwd=tmp, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("# Statement of work: the pilot", result.stdout)
            self.assertIn("$1,750.00", result.stdout)
            bad = Path(tmp) / "bad.json"
            bad.write_text('{"subjects":')
            result = subprocess.run([*command[:2], str(bad)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("error:", result.stderr)

    def test_yaml_equivalence_and_unsafe_input(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("optional PyYAML not installed")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "job.yaml"
            data = json.loads((ROOT / "docs/examples/pilot.json").read_text())
            path.write_text(yaml.safe_dump(copy.deepcopy(data)))
            self.assertEqual(quote.load_job(path), quote.validate_job(data))
            path.write_text("!!python/object/apply:os.system ['echo unsafe']")
            with self.assertRaises((TypeError, ValueError)):
                quote.load_job(path)


if __name__ == "__main__":
    unittest.main()
