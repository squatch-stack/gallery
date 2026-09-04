#!/usr/bin/env python3
"""Render a planning estimate from JSON (stdlib) or YAML (optional PyYAML)."""

import argparse
import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RATES = Path(__file__).with_name("pricing.json")
OPTIONS = {"mesh", "accessibility_pass", "dataset_publication", "rush"}
TEXT_FIELDS = {"name", "applicant", "vendor", "program", "location", "purpose", "start_date", "repository"}
NUM_FIELDS = {"days_on_site", "travel_miles", "hosting_months", "donated_capture_days", "federal_request"}
RATE_FIELDS = {
    "capture_day",
    "baseline_production",
    "site_production_base",
    "site_additional_subject",
    "program_production_per_subject",
    "mesh_per_subject",
    "accessibility_pass",
    "dataset_publication",
    "travel_per_mile",
    "hosting_per_month",
    "rush_fraction",
    "day_hours",
    "estimate_valid_days",
    "draft_business_days",
    "rush_draft_business_days",
    "review_business_days",
    "revision_business_days",
    "archive_retention_months",
}
TOKEN = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise TypeError(f"{label} must be a number")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return result


def plain(value, label):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label} must be nonempty single-line text")
    if "{{" in value or "}}" in value:
        raise ValueError(f"{label} cannot contain template markers")
    return value.strip()


def md(value):
    return re.sub(r"([\\`*_{}\[\]<>|#])", r"\\\1", str(value))


def money(value):
    return f"${value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def rounded(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def load_job(path):
    source = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise ValueError("YAML requires PyYAML; install it or supply a JSON job") from exc
        try:
            data = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML: {exc}") from exc
    else:
        data = json.loads(source)
    return validate_job(data)


def validate_job(data):
    if not isinstance(data, dict):
        raise TypeError("job must be an object")
    unknown = set(data) - TEXT_FIELDS - NUM_FIELDS - {"subjects", "options"}
    if unknown:
        raise ValueError(f"unknown job fields: {', '.join(sorted(map(str, unknown)))}")
    job = {key: plain(value, key) for key, value in data.items() if key in TEXT_FIELDS}
    if "name" not in job:
        raise ValueError("name is required")
    subjects = data.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        raise ValueError("subjects must be a nonempty list")
    job["subjects"] = []
    for subject in subjects:
        if not isinstance(subject, dict) or set(subject) - {"name", "type", "scope"}:
            raise ValueError("each subject accepts name, type, and optional scope")
        name = plain(subject.get("name"), "subject name")
        kind = subject.get("type")
        if not isinstance(kind, str) or kind not in {"object", "building", "interior", "site"}:
            raise ValueError("subject type must be object, building, interior, or site")
        scope = plain(subject.get("scope", "ACCESSIBLE SURFACES; BOUNDARY TO BE AGREED"), "subject scope")
        job["subjects"].append({"name": name, "type": kind, "scope": scope})
    if len({s["name"].casefold() for s in job["subjects"]}) != len(subjects):
        raise ValueError("subject names must be unique")
    for key in NUM_FIELDS:
        if key in data or key != "federal_request":
            job[key] = number(data.get(key, 0), key)
    if job["days_on_site"] <= 0:
        raise ValueError("days_on_site must be positive")
    if job["hosting_months"] != job["hosting_months"].to_integral_value():
        raise ValueError("hosting_months must be a whole number")
    if job["donated_capture_days"] > job["days_on_site"]:
        raise ValueError("donated_capture_days cannot exceed days_on_site")
    options = data.get("options", {})
    if not isinstance(options, dict) or set(options) - OPTIONS or any(type(v) is not bool for v in options.values()):
        raise ValueError("options accepts only boolean mesh, accessibility_pass, dataset_publication, rush")
    job["options"] = {key: options.get(key, False) for key in OPTIONS}
    return job


def load_rates(path=RATES):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != RATE_FIELDS | {"currency", "status"}:
        raise ValueError("pricing must contain exactly the documented rate fields, currency, and status")
    if data["currency"] != "USD":
        raise ValueError("only USD pricing is supported")
    rates = {key: number(data[key], key) for key in RATE_FIELDS}
    rates["status"] = plain(data["status"], "pricing status")
    if rates["capture_day"] <= 0 or rates["day_hours"] <= 0 or rates["rush_fraction"] > 1:
        raise ValueError("capture_day/day_hours must be positive; rush_fraction must be at most 1")
    return rates


def estimate(job, rates):
    count, days = len(job["subjects"]), job["days_on_site"]
    tier = "Baseline" if count == 1 and days == 1 else "Site" if 3 <= count <= 5 and days == 2 else "Program"
    lines = []

    def add(label, quantity, rate):
        lines.append((label, Decimal(quantity), rate, rounded(Decimal(quantity) * rate)))

    add("Capture (includes proposed donated time)", days, rates["capture_day"])
    if tier == "Baseline":
        add("Production, web export, archive and provenance", 1, rates["baseline_production"])
    elif tier == "Site":
        add("Production and metadata for first three subjects", 1, rates["site_production_base"])
        add("Production and metadata for additional subjects", count - 3, rates["site_additional_subject"])
    else:
        add(
            "Provisional production, archive and provenance per subject", count, rates["program_production_per_subject"]
        )
    access = tier == "Site" or job["options"]["accessibility_pass"]
    if access:
        add(
            "Accessibility pass" + (" (included in Site tier)" if tier == "Site" else ""),
            1,
            rates["accessibility_pass"],
        )
    if job["options"]["mesh"]:
        add("Mesh derivative per subject", count, rates["mesh_per_subject"])
    if job["options"]["dataset_publication"]:
        add("Dataset publication per job", 1, rates["dataset_publication"])
    donation = rounded(job["donated_capture_days"] * rates["capture_day"])
    services = sum((row[3] for row in lines), Decimal(0))
    if job["options"]["rush"]:
        add("Rush surcharge on cash services after donation", services - donation, rates["rush_fraction"])
    add("Travel, total round-trip vehicle miles", job["travel_miles"], rates["travel_per_mile"])
    add("Hosting per job-month", job["hosting_months"], rates["hosting_per_month"])
    gross = sum((row[3] for row in lines), Decimal(0))
    cash = gross - donation
    request = rounded(job.get("federal_request", cash))
    return {
        "tier": tier,
        "lines": lines,
        "gross": gross,
        "donation": donation,
        "cash": cash,
        "request": request,
        "gap": max(Decimal(0), request - donation),
        "access": access,
    }


def render_quote(job, rates, result):
    lines = [
        f"# Estimate: {md(job['name'])}",
        "",
        f"**{md(rates['status'])}. USD; planning estimate, not an offer.**",
        "",
        f"Tier: **{result['tier']}**. {len(job['subjects'])} subject(s); {job['days_on_site']} on-site day(s).",
        f"One day = {rates['day_hours']} hours. Validity after approval: {rates['estimate_valid_days']} days.",
        "",
        "| Item | Quantity x rate | Amount |",
        "|---|---:|---:|",
    ]
    for label, quantity, rate, amount in result["lines"]:
        arithmetic = f"{money(quantity)} x {rate * 100}%" if label.startswith("Rush") else f"{quantity} x {money(rate)}"
        lines.append(f"| {label} | {arithmetic} | {money(amount)} |")
    lines += [
        f"| Gross scope value | | {money(result['gross'])} |",
        (
            f"| Proposed in-kind match: donated capture, deducted from invoice | "
            f"{job['donated_capture_days']} days x {money(rates['capture_day'])} | -{money(result['donation'])} |"
        ),
        f"| **Total cash payable** | | **{money(result['cash'])}** |",
        "",
        "## Match planning (1:1 scenario)",
        "",
        f"Federal request used for this scenario: {money(result['request'])}. "
        + ("Provided by applicant." if "federal_request" in job else "Assumed equal to cash payable for illustration."),
        (
            f"Proposed donated capture: {money(result['donation'])}; remaining non-Federal match to identify: "
            f"**{money(result['gap'])}**. This is a job estimate, not the whole grant budget."
        ),
        "",
        (
            "Donated capture is part of the stated days, never an extra billed day. The same capture day rate applies. "
            "No donation is committed until the studio approves it. Applicant must substantiate allowable valuation "
            "and eligibility with service/time records; exclude profit or other ineligible components as required. "
            "Do not claim these hours on another award or as both cash expense and in-kind match. "
            "See [2 CFR 200.306](https://www.ecfr.gov/current/title-2/section-200.306)."
        ),
        "",
        "## Scope and conditions",
        "",
    ]
    lines += [f"- {md(s['name'])} ({s['type']}): {md(s['scope'])}" for s in job["subjects"]]
    lines += [
        "",
        (
            "Includes SOG/SPZ web delivery, available PLY source export, generated provenance and handoff. "
            "Mesh adds OBJ with materials/textures and glTF/GLB, subject to source feasibility. "
            "E57 requires separate scoping."
        ),
        "Accessibility pass: "
        + (
            "included; audit and remediation subject to the SOW."
            if result["access"]
            else "not selected; WCAG conformance is not asserted."
        ),
        "Dataset publication: "
        + (
            "selected; repository and release authorization to be agreed."
            if job["options"]["dataset_publication"]
            else "not selected."
        ),
        (
            "Travel is vehicle mileage only. Taxes, permits, lodging, meals, flights, repository fees and "
            "specialist work are excluded pending an approved revision; they are not assumed free."
        ),
        (
            "Hosting is per job for the stated months, then export/handoff or a separately approved renewal. "
            "No perpetual hosting or survey accuracy is included."
        ),
    ]
    if result["tier"] == "Program":
        lines += [
            "Program is grant-scoped: the arithmetic is a provisional allowance requiring a subject-by-subject review."
        ]
    lines += [
        "",
        (
            "For ABPP applications, use Category F. Contractual for contractor costs; applicant must confirm "
            "the applicable notice, procurement method and match rules. "
            "See docs/pricing.md for sources and rate assumptions."
        ),
    ]
    return "\n".join(lines) + "\n"


def render_sow(job, rates, result, template=None):
    text = (template or ROOT / "docs/sow-template.md").read_text(encoding="utf-8")
    values = {key.upper(): md(job.get(key, f"{key.upper()} TO BE AGREED")) for key in TEXT_FIELDS}
    values.update({key.upper(): str(value) for key, value in rates.items()})
    values.update(
        TIER=result["tier"],
        DAYS_ON_SITE=str(job["days_on_site"]),
        HOSTING_MONTHS=str(job["hosting_months"]),
        CASH_TOTAL=money(result["cash"]),
        GROSS_TOTAL=money(result["gross"]),
        IN_KIND=money(result["donation"]),
        DRAFT_DAYS=str(rates["rush_draft_business_days"] if job["options"]["rush"] else rates["draft_business_days"]),
        SUBJECTS="\n".join(
            f"| {md(s['name'])} | {s['type']} | {md(s['scope'])} | Core package |" for s in job["subjects"]
        ),
        MESH_SCOPE="INCLUDED FOR EACH SUBJECT" if job["options"]["mesh"] else "NOT INCLUDED",
        ACCESS_SCOPE="INCLUDED" if result["access"] else "NOT INCLUDED; NO CONFORMANCE CLAIM",
        DATASET_SCOPE="INCLUDED" if job["options"]["dataset_publication"] else "NOT INCLUDED",
    )
    missing = set(TOKEN.findall(text)) - values.keys()
    if missing:
        raise ValueError(f"unmapped SOW placeholders: {', '.join(sorted(missing))}")
    return TOKEN.sub(lambda match: values[match[1]], text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--sow", action="store_true", help="append the populated statement of work")
    parser.add_argument("--rates", type=Path, default=RATES, help="override the adjacent pricing.json")
    args = parser.parse_args(argv)
    try:
        job, rates = load_job(args.job), load_rates(args.rates)
        result = estimate(job, rates)
        output = render_quote(job, rates, result)
        if args.sow:
            output += "\n---\n\n" + render_sow(job, rates, result)
    except (OSError, TypeError, ValueError, InvalidOperation) as exc:
        parser.error(str(exc))
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
