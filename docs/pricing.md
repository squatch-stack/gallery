# Planning prices and quote generation

All studio prices, quantities and schedule allowances are **placeholders pending
owner approval**. USD throughout. Edit [tools/pricing.json](../tools/pricing.json)
to change commercial rates and schedule defaults in one place; the quote tool
reads it at runtime. The figures below describe the initial configuration, not a
second price source. Regenerate estimates after edits; an issued estimate is a
snapshot and must not be silently repriced. Technical checker thresholds are
existing project policy, not pricing assumptions.

## Tiers and arithmetic

| Tier | Selection | Initial base arithmetic | Included |
|---|---|---|---|
| Baseline | Exactly one subject and one on-site day | 1 × $1,500 capture + $1,000 production = **$2,500** | Web exports, available source archive, basic metadata, provenance, handoff |
| Site | Three to five subjects and exactly two on-site days | 2 × $1,500 capture + $1,500 initial production + $1,500 per subject beyond three + $500 accessibility = **$5,000 / $6,500 / $8,000** | Core package per subject, structured metadata and accessibility pass |
| Program | Every other combination | Days × $1,500 + subjects × $1,000 provisional production, plus options | Grant-scoped; subject-by-subject review before an offer |

The tier range excludes travel, hosting, mesh, dataset publication and rush, and
is before donations. The Site accessibility pass is charged once within the
advertised base, even if the input also selects it. A two-subject job, half-day
pilot, extra capture day or six-subject job is Program; no hidden rounding to a
package or assertion that a whole site fits a pilot. Subject type is descriptive,
not a price multiplier: actual size, access and complexity determine feasibility.

Production covers planning, processing, cleanup, export, documentation and the
review allowance in the SOW. Each core package includes the generated provenance
page and generator-input sidecar, refreshed on replacement, plus the measured
accessibility self-assessment statement described in the SOW. The statement is
included even without the paid accessibility pass; it is not an independent
audit or a complete WCAG conformance assessment. The paid pass retains its
audit, remediation and retest scope. These are selling-price allowances, not
measured internal labor costs. One capture day is initially eight hours; fractional days
are supported without minimum rounding. There is no automatic overtime charge.

## Editable rate keys

| Key in pricing.json | Unit and initial value |
|---|---|
| `capture_day` | $1,500 per on-site day; same rate for proposed donated capture |
| `baseline_production` | $1,000 per Baseline job |
| `site_production_base` | $1,500 for first three Site subjects |
| `site_additional_subject` | $1,500 per fourth/fifth Site subject |
| `program_production_per_subject` | $1,000 provisional Program production per subject |
| `mesh_per_subject` | $600 per subject, if selected, subject to source feasibility |
| `accessibility_pass` | $500 per job, included in Site, optional elsewhere |
| `dataset_publication` | $350 per job, if selected; repository fees excluded |
| `travel_per_mile` | $0.75 per vehicle mile, all round trips combined; studio assumption, not a Federal mileage claim |
| `hosting_per_month` | $25 per job per whole month, no per-subject multiplier |
| `rush_fraction` | 0.25 (25%) on service subtotal after donated capture, excluding travel and hosting |
| `day_hours` | 8 hours |
| `estimate_valid_days` | 30 calendar days after owner approval |
| `draft_business_days` / `rush_draft_business_days` | 15 / 7 business days after capture and receipt of content |
| `review_business_days` / `revision_business_days` | 10 / 5 business days |
| `archive_retention_months` | 12 months after acceptance |

`currency` must be `USD`. `status` appears on every estimate; leave the placeholder
status until the owner approves the rate card. One content revision round is the
proposed SOW term; acceptance defects still require correction. Costs use decimal
arithmetic, round each line half up to cents, then sum the rounded lines. No tax,
contingency or indirect-cost percentage is silently added. Reconcile to the
applicable grant's rounding rules when transferring the estimate to its forms.

## Research anchors and their limits

Sources checked 2026-09-04. These support market context, not a finding that the
studio's prices are allowable or that laser-survey and splat outputs are equivalent.

- **$1,000–$3,000 per on-site day:** Cad Crowd's pricing discussion lists this
  range for large projects requiring multiple scanning days. It is a marketplace
  guide, not a binding vendor quote or statistical US market survey.[^daily]
- **$1,500–$5,000 per building scan:** Reality IMT's Houston building-scanning
  guide gives this range per scan and notes variation by size and complexity.
  This is the closest cited US building anchor to the brief's “small building”
  range; the source does not limit the range specifically to small buildings.[^building]
- An additional US provider illustrates the scope difference: Global Design
  Solutions lists a small building with point cloud and 2D drawings at
  $5,000–$15,000. That is a broader output than this studio's visual pilot.[^comparison]

Keep these researched anchors separate from the owner's chosen numbers. Obtain
current, comparable written estimates for the actual site and retain scope,
date, unit rate and inclusions in the applicant's market-research file.

## Run the tool

JSON needs only Python's standard library. YAML additionally needs PyYAML
(`python3 -m pip install PyYAML` in an appropriate virtual environment).

```sh
python3 tools/quote.py docs/examples/pilot.json
python3 tools/quote.py docs/examples/pilot.json --sow > docs/examples/pilot-quote-and-sow.md
python3 tools/quote.py docs/examples/pilot.json --rates tools/pricing.json
python3 -m unittest discover -s tests -p 'test_quote.py'
ruff check --line-length 120 tools/quote.py tests/test_quote.py
```

The `--sow` flag appends the populated SOW to the estimate on stdout. It writes no
files unless the caller redirects output. Paths resolve relative to the tool for
rates/template, so invocation works from other directories. Missing organizational
and scheduling details remain ALL CAPS “TO BE AGREED”; no unresolved `{{TOKEN}}`
markers remain. Review these details before attaching the draft to an application.

Required job fields:

- `name`: nonempty single-line project title.
- `subjects`: nonempty list of `{ "name": "SUBJECT", "type": "building", "scope": "ACCESSIBLE EXTERIOR" }`.
  Type must be `object`, `building`, `interior` or `site`; scope is optional but must
  be settled before contracting. Names must be unique without regard to case.
- `days_on_site`: positive number. Fractional days are allowed.

Optional fields:

- `travel_miles`: nonnegative total vehicle miles; default 0.
- `hosting_months`: nonnegative whole months; default 0.
- `options`: booleans `mesh`, `accessibility_pass`, `dataset_publication`, `rush`;
  absent options default false, except Site always includes the accessibility pass.
- `donated_capture_days`: nonnegative part of `days_on_site`; default 0; cannot
  exceed the total capture days. It is not added to those days.
- `federal_request`: nonnegative USD amount used only in the match scenario. If
  omitted, the scenario assumes the total cash payable is requested federally.
- `applicant`, `vendor`, `program`, `location`, `purpose`, `start_date`, `repository`:
  single-line text for the SOW. Use organizations, programs and organizational
  roles only; do not enter personal names. Quote YAML dates as strings.

Unknown fields, invalid types, negative/non-finite values and string booleans are
rejected. YAML is loaded safely; arbitrary object constructors are not allowed.
The tool cannot infer personal names or decide whether a site is feasible; those
remain document-review responsibilities.

## Pilot and match accounting

[The example](examples/pilot.json) is a single building called **the pilot**,
one day, accessible exterior only, no travel or hosting and no paid options.
Its base value is $2,500. The proposed half-day donation is $750, reducing cash
payable to **$1,750**. Assuming that cash amount is federally requested, the
remaining 1:1 match to identify is **$1,000**. If no capture is donated, set
`donated_capture_days` to 0 and the cash total is $2,500.

This example is a cost attachment within a larger grant project; it is not a claim
that the pilot alone meets a program's minimum award. A donation does not by
itself establish full match: required match equals the Federal request in the
1:1 scenario, and the tool reports the remaining gap. It does not count future
hosting, applicant labor or any other contribution without a separate budget.

The tool values donated capture at the same commercial rate as paid capture,
as requested, and subtracts it once from cash payable. This is a **proposed value**,
not proof of allowable match. Section 200.306 requires verifiable, necessary,
allowable contributions without double counting; valuation may instead follow
regular pay for donated employee services. Document hours, work, donor
organization, valuation basis and award approval; adjust the grant budget if
commercial rates include ineligible profit or overhead. A separate approved
commitment letter is needed; this estimate does not commit the studio.[^match]

## Grant use and owner review

The FY2025 ABPP notice is a historical application reference: it lists awards of
$20,000–$200,000 and requires at least half the total project cost from non-Federal
sources. Category F. Contractual asks for market research and unit-cost arithmetic;
contracts over $10,000 require evidence of competition or another allowable
procurement method. It does **not** require every such contract to be sole-source.
Use the applicable year's notice, not this historical range, to establish current
eligibility, award limits, match and submission rules.[^abpp]

Section 200.320 allows noncompetitive procurement only under specified conditions;
price alone above $10,000 is not a sole-source justification. The recipient owns
its procurement decision. Also assess 200.319(b) before using a vendor-drafted SOW
as competitive solicitation specifications.[^procurement][^competition]

IMLS Museums for America and Missouri Humanities require their own current
program-specific review. No ABPP match ratio, award range or budget category is
automatically applied to them. This tool produces the requested 1:1 planning
scenario, not an eligibility determination for every program.

Before an offer, the owner must approve every rate and allowance, capacity and
margins, site boundaries, included archival outputs, accessibility remediation
scope, permissible match valuation, donated hours, travel and taxes, repository
fees, retention/hosting terms, schedule and payment terms. Baseline without an
accessibility pass does not promise a WCAG-conformant viewer. Add the option or
budget a separate approved implementation if the award requires it. E57 and
survey-grade deliverables require a revised scope and cost, not a checkbox.

[^daily]: Cad Crowd, scanning-service pricing guide: https://www.cadcrowd.com/blog/explore-costs-of-3d-laser-scanning-3d-modeling-services-pricing-and-rates-with-freelance-design-firms/amp/
[^building]: Reality IMT, Houston building-scanning guide, cost FAQ: https://realityimt.com/industry-insights/what-to-expect-from-3d-laser-scanning-for-building-companies-in-houston/
[^comparison]: Global Design Solutions, indicative project ranges: https://www.globaldesignsolutions.com/resources/3d-laser-scanning-project-cost/
[^match]: 2 CFR 200.306: https://www.ecfr.gov/current/title-2/section-200.306
[^abpp]: NPS, FY2025 Battlefield Interpretation Grant notice, award information, match and Category F: https://files.simpler.grants.gov/opportunities/d7877c5b-64b6-49ce-baa4-a8ccf904cc90/attachments/293214f4-c8c5-41d9-ad4a-8cdbff9e83c4/P25AS00477_ABPP_BIG_NOFO_25-0826.pdf
[^procurement]: 2 CFR 200.320: https://www.ecfr.gov/current/title-2/section-200.320 ; accessible text: https://www.law.cornell.edu/cfr/text/2/200.320
[^competition]: 2 CFR 200.319(b): https://www.ecfr.gov/current/title-2/section-200.319
