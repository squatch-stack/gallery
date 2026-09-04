# Estimate: the pilot

**PLACEHOLDER — OWNER APPROVAL REQUIRED. USD; planning estimate, not an offer.**

Tier: **Baseline**. 1 subject(s); 1 on-site day(s).
One day = 8 hours. Validity after approval: 30 days.

| Item | Quantity x rate | Amount |
|---|---:|---:|
| Capture (includes proposed donated time) | 1 x $1,500.00 | $1,500.00 |
| Production, web export, archive and provenance | 1 x $1,000.00 | $1,000.00 |
| Travel, total round-trip vehicle miles | 0 x $0.75 | $0.00 |
| Hosting per job-month | 0 x $25.00 | $0.00 |
| Gross scope value | | $2,500.00 |
| Proposed in-kind match: donated capture, deducted from invoice | 0.5 days x $1,500.00 | -$750.00 |
| **Total cash payable** | | **$1,750.00** |

## Match planning (1:1 scenario)

Federal request used for this scenario: $1,750.00. Assumed equal to cash payable for illustration.
Proposed donated capture: $750.00; remaining non-Federal match to identify: **$1,000.00**. This is a job estimate, not the whole grant budget.

Donated capture is part of the stated days, never an extra billed day. The same capture day rate applies. No donation is committed until the studio approves it. Applicant must substantiate allowable valuation and eligibility with service/time records; exclude profit or other ineligible components as required. Do not claim these hours on another award or as both cash expense and in-kind match. See [2 CFR 200.306](https://www.ecfr.gov/current/title-2/section-200.306).

## Scope and conditions

- the pilot (building): Accessible exterior only; one rigid building, ground-level orbit.

Includes SOG/SPZ web delivery, available PLY source export, generated provenance and handoff. Mesh adds OBJ with materials/textures and glTF/GLB, subject to source feasibility. E57 requires separate scoping.
Accessibility pass: not selected; WCAG conformance is not asserted.
Dataset publication: not selected.
Travel is vehicle mileage only. Taxes, permits, lodging, meals, flights, repository fees and specialist work are excluded pending an approved revision; they are not assumed free.
Hosting is per job for the stated months, then export/handoff or a separately approved renewal. No perpetual hosting or survey accuracy is included.

For ABPP applications, use Category F. Contractual for contractor costs; applicant must confirm the applicable notice, procurement method and match rules. See docs/pricing.md for sources and rate assumptions.

---

# Statement of work: the pilot

**DRAFT FOR GRANT ATTACHMENT — OWNER APPROVAL REQUIRED.** All commercial numbers
and schedule allowances are placeholders until approved. Braced ALL CAPS fields
are populated by `tools/quote.py --sow`; missing administrative values remain
explicitly marked TO BE AGREED. Organizations and organizational roles only.

## 1. Parties, purpose and scope

Applicant / recipient: PILOT NONPROFIT. Vendor: Squatch Stack.
Program: National Park Service ABPP Battlefield Interpretation Grants. Location: PILOT HISTORIC SITE.
Purpose and intended public benefit: Document one historic building for public interpretation and grant planning.

The vendor will document the subjects below for public interpretation, using
1 on-site day(s), up to 8 hours per day, under the
Baseline tier. Boundaries and accessible faces must be confirmed at kickoff.
This is visual documentation; measured survey, engineering certification and
reconstruction of unseen surfaces are excluded.

## 2. Subjects and deliverables per subject

| Subject | Type | Capture boundary | Deliverables |
|---|---|---|---|
| the pilot | building | Accessible exterior only; one rigid building, ground-level orbit. | Core package |

Each **core package** contains:

- A cleaned Gaussian-splat web asset in SOG and SPZ, a viewer entry and a static
  preview with a plain-language description of the subject and coverage gaps.
- The available source PLY export with its property/schema description, original
  photographs and available camera/solve outputs in a private handoff archive.
  PLY splat properties are not a conventional surface mesh. Raw inputs unavailable
  from an app export must be declared before contracting, with a revised inventory.
- A generated HTML provenance page for every delivered scene, its generator-input
  JSON sidecar from `tools/provenance.py`, file inventory, SHA-256 hashes and saved
  checker JSON. Regenerate the page and sidecar on each replacement with
  `tools/promote_scene.py --replace`; `tools/promote_scene.py --revert` restores
  archived assets, catalog entry and available provenance page/sidecar and reruns
  the deliverable checker. Verify restored evidence against the delivered version;
  regenerate missing or stale provenance before handoff. Record capture date,
  device, software/version, processing settings, registered/input views and solve metrics where available. Mark unavailable
  information as unknown; do not substitute defaults or historical catalog counts
  for measurements of the delivered files. Use organization attribution only.
- A machine-readable JSON metadata sidecar: identifier, title, description,
  creator organization, publisher organization, date, type, format, spatial
  coverage, source, relation, rights and license mapped to Dublin Core terms.[^dc]
  Technical capture and processing history follows the documentation aims of
  Smithsonian 3D practice; this is an explicit local mapping, not Smithsonian
  certification or a claim to implement an unidentified schema.[^si]

Mesh option: **NOT INCLUDED**. If selected, add a textured OBJ with materials
and texture files and glTF 2.0/GLB derivative for each subject, after confirming
source feasibility. Verify dependencies and open both exports. glTF is a delivery
format; its presence alone does not establish archival suitability.[^gltf]

Archive format choice follows the receiving repository's written requirements.
Library of Congress describes PLY as acceptable for scanned 3D objects; its format
catalog also documents OBJ and E57. Documentation is not certification of every
variant or of this pipeline.[^ply][^formats] E57 applies to suitable point-cloud
source data and requires separately scoped capture/conversion and validation;
it is not promised as an automatic splat export. SOG/SPZ are web derivatives,
not asserted to be Library of Congress preferred archival formats.[^formats]

Dataset publication: **NOT INCLUDED**. If included, prepare the approved
archive and metadata, deposit with REPOSITORY TO BE AGREED, and deliver the repository
record/identifier and license notice. Applicant must authorize public release
and approve location redactions before deposit. Repository fees are excluded.
Hosting: 0 month(s) from acceptance, then handoff or approved renewal.

## 3. Methods in plain language

Confirm access and the subject list before fieldwork. Generate a field plan with
`tools/capture_plan.py --type TYPE --size METRES`, using the agreed subject type
(object, building, interior or site), horizontal size and optional `--height`.
Save the Markdown plan with `--out` or JSON with `--json`; review its labelled
operator defaults, rings, overlap arithmetic and checklist against access and
coverage requirements in `docs/capture.md`. Photograph each rigid
subject from overlapping positions at two distances and heights where safe;
keep the whole subject in view and do not change lens mid-orbit. Check image
registration before leaving when practical. Record omissions caused by access,
weather, moving vegetation, reflective surfaces or insufficient coverage.

Align the photographs to estimate camera positions, isolate the named subject,
create the 3D representation, remove stray and oversized elements, and export
web files. For subject isolation, `tools/clean_export.py --crop-shape` supports
box, ellipsoid and cylinder boundaries. Reduce a scene to the web splat budget
with `--target-count`, removing the least-contributing splats by opacity-weighted
footprint (opacity times the product of the two largest scale axes), never by
cropping an interior to make it fit. Confirm retained coverage and exported byte
size separately; a splat count alone does not guarantee either. Review the actual
exported files and regenerate provenance after changes. Repository methods are documented in `docs/capture.md`,
`tools/clean_export.py` and `tools/provenance.py`; provenance pages demonstrate
both raw-input and app-export evidence paths. The resolution sweep and masking
A/B in `docs/results.md` provide measured evidence of method, with their stated
comparison limits; they do not establish visual quality or future performance.
Existing gallery scenes do not establish that a future capture will meet
acceptance without review.

## 4. Standards commitments and evidence

Deliver an updated copy of the draft accessibility statement in
`docs/accessibility.md`, generated with `tools/a11y_check.py --all --statement`
and an output path. It is a measured self-assessment of static HTML/CSS in
`viewer.html` and `index.html`, not an independent audit or complete WCAG 2.1 AA
conformance assessment. It reports automated checks such as contrast, document
language/title, accessible names, visible focus, reduced motion and viewport
zoom, with known limitations: no screen-reader behavior, browser rendering, interactive focus
order, pointer gestures, JavaScript failures, captions, cognitive accessibility
or complete success-criterion testing; contrast covers only resolvable CSS pairs.
The canvas description and provenance link do not provide a complete nonvisual
equivalent of the scene. Supply this statement even when the accessibility pass
is not selected; it does not replace the audit and remediation below.

Accessibility pass: **NOT INCLUDED; NO CONFORMANCE CLAIM**. When included, the delivered viewer and
related interpretive pages must meet WCAG 2.1 Level AA: all applicable Level A
and AA success criteria over the complete agreed pages and processes.[^wcag]
The pass includes audit, scoped remediation and retest, with keyboard operation,
visible focus, labels, contrast, screen-reader checks and equivalent descriptive
content for the 3D presentation. Supply an applicability/test matrix listing page
versions, browsers, assistive technology, results and unresolved issues. These
examples are not a substitute for testing all applicable criteria.[^wcag]

The current gallery is not represented as already conformant. If the pass is not
included, this SOW makes no WCAG conformance claim; a grant requiring AA delivery
must add the pass or identify a separately funded implementation and audit before
approval. Unexpected remediation beyond the agreed viewer requires change control;
it cannot silently lower an agreed conformance requirement.[^wcag]

Archive and metadata commitments are those in section 2, with validation evidence
in the handoff. The file checker below does not test accessibility, physical
accuracy, archival validity or metadata semantics.

## 5. Schedule and responsibilities

| Milestone | Proposed timing / responsibility |
|---|---|
| Kickoff and permission review | START\_DATE TO BE AGREED; applicant and vendor agree boundaries, rights and repository |
| Field capture | 1 day(s), scheduled only after written access confirmation |
| Draft delivery and evidence | Within 15 business days after capture and receipt of applicant content |
| Consolidated review | Applicant supplies acceptance or a defect list within 10 business days |
| Correction and retest | Vendor allows 5 business days after consolidated review |
| Final handoff / publication | After written acceptance and public-release authorization |

One consolidated revision round is included for content preferences. Correction
of defects against the agreed acceptance criteria remains the vendor's obligation.
Weather, permit or source limitations require an agreed revised schedule. Rush
changes draft timing only, never acceptance thresholds. Vendor retains the
handoff archive for 12 months after acceptance; applicant
assumes long-term preservation after verified receipt.

## 6. Acceptance criteria

For each delivered splat, run `python3 tools/check_deliverable.py SCENE --platform
web-mobile --json` with the scene catalog and evidence installed. Save the command,
tool revision, date and JSON alongside hashes. The following are **local project
policy**, implemented in `tools/check_deliverable.py`, not an external standard.
Record the agreed subject profile in the scene catalog as `object` or `place`;
`--subject object` or `--subject place` overrides it and must be saved with the
evidence. The checker defaults to object if no profile is recorded. These are
checker profiles, distinct from the quote's descriptive subject types: a room or
landscape keeps its surroundings as a place, while an isolated subject is an
object. All applicable cleanliness checks must pass as follows:

| Check name | Required threshold | Definition used by the checker |
|---|---|---|
| `floater` | Objects: fraction **< 0.02 (2%)**; places: reported as `info`, not enforced | Centers farther than 3 times the opacity-weighted median distance from the opacity-weighted center; distance uses maximum absolute coordinate deviation |
| `fog` | fraction **< 0.01 (1%)** | Any scale component exceeds 0.05 times the largest center-position extent |
| `translucent` | fraction **< 0.10 (10%)** | Opacity below 0.05 |
| `nonfinite` | count **= 0** | Non-finite position, scale, opacity or decoded raw attributes |

The remaining checker results must also pass: `format` allows nonempty readable
`.sog`, `.spz`, `.ply` or `.glb` for web-mobile; `count` is at most **500,000 splats**;
`size` at most **20,000,000 bytes**; `catalog` agrees within **1% of file count**;
`licence` requires a LICENSE file and a stated scan license in README;
`provenance` requires a catalog-linked existing sheet. Equality passes the count
and size budgets but fails the strict fraction thresholds where applicable;
the floater threshold binds objects only. Fog, translucency and non-finite
checks bind both profiles. Unavailable or unreadable geometry fails closed. Test both SOG and SPZ; provide the SPZ decoder
needed by the checker rather than assuming a parsed header verifies cleanliness.

Mesh `cleanliness` is reported `not_applicable`; the checker uses zero as its mesh
count and only rudimentary file/extension checks. Thus a mesh must additionally
open in the agreed viewer and an independent importer, resolve all textures,
and match the subject inventory. Archive OBJ, PLY variants and E57 need their own
format-aware validation; do not misapply the splat checker to them. A desktop
exception must be agreed in writing: its existing limits are **1,500,000 splats**
and **60,000,000 bytes**, with other checks unchanged. No automatic exception.

Quality assurance runs automatically on pull requests and pushes to main through
`.github/workflows/gates.yml`: Ruff, tests, deliverable checks, static
accessibility, results freshness and path hygiene. Deliverable checks are
currently advisory in CI, so a green CI run does not establish acceptance.
`tools/gate.sh` runs the complete local suite with deliverable failures blocking;
its `--quick` mode runs only Ruff, path hygiene and tests.

Applicant and vendor review coverage, recognizable rigid surfaces and intended
interpretive views together; gaps must match the signed scope. Verify all file
hashes, archive readability, provenance contents, metadata fields, rights notices
and the selected accessibility evidence. A file-check pass alone is insufficient.
Acceptance requires a written organization-level approval. Failures require
correction and retest or a signed scope change; silence is not acceptance.

## 7. License and data ownership

Proposed contract allocation: upon payment, vendor assigns to PILOT NONPROFIT its
transferable rights in commissioned photographs, scans, metadata and project
outputs, and supplies copies of raw and processed project data. This allocation
must be adopted in the executed agreement; 2 CFR 200.315 does not itself assign
vendor rights to the applicant. Pre-existing tools and third-party components
retain their licenses; supply a rights inventory and sufficient usage rights.
Where applicable, preserve the Federal agency's royalty-free, nonexclusive,
irrevocable rights to reproduce, publish or otherwise use the work for Federal
purposes and authorize others to do so, and its rights in award-produced data.[^rights]

Public scan and metadata releases use CC BY 4.0 with title, organization credit,
source/license links and an indication of modifications. This permits sharing
and adaptation, including commercial reuse, subject to attribution; privacy,
access restrictions and third-party rights still require clearance.[^cc]
Applicant approves releases and any exceptions before publication. Source code
remains under its existing license (this repository uses Apache-2.0); asset
licensing must be stated separately.[^apache]

## 8. Assumptions, permits and exclusions

Applicant secures site-owner/land-manager access and confirms permit requirements,
lead times, fees and restrictions with the responsible authority; vendor supplies
its activity description and follows approved conditions. Do not treat the field
checklist as blanket permit authorization. No capture begins without documented
permission or confirmation that no permit is required. This allocation is a
contract responsibility, not a statement of jurisdiction-specific permit law.

Applicant provides approved interpretive content and resolves collection rights,
sensitive locations and publication restrictions. No drones, excavation, moving
collection objects, lifts, restricted-area access, destructive testing, CAD/BIM,
boundary surveying, guaranteed physical accuracy, conservation treatment or
historical interpretation research are included. Source feasibility and size of
buildings/interiors/sites must be reviewed; a subject count alone does not measure
complexity. Extra visits, travel beyond stated mileage, taxes, permits, lodging,
meals, flights and third-party repository fees require separately approved costs.

## 9. Price, grant budgeting and change control

Attached estimate: gross scope value **$2,500.00**, proposed donated capture
**$750.00**, total cash payable **$1,750.00**. No donation commitment exists
until documented by the vendor organization. Rate-based match estimates require
allowability, reasonable valuation, verifiable time/service records, no duplicate
claims, and approval under the applicable award. Donated employee services may
require regular-pay valuation instead of commercial rates; revise the match
budget if the proposed rate is not allowable.[^match]

For the cited FY2025 ABPP notice, contractor costs belong in Category F.
Contractual, supported by market research and procurement documentation for
contracts over $10,000. This is not a universal sole-source threshold. The
applicant selects an allowable procurement method and documents any qualifying
noncompetitive justification under 2 CFR 200.320; it must check the applicable
funding-year notice before submission.[^abpp][^procurement]

Use this vendor scope as an application cost attachment. Before using it in a
competitive solicitation, the applicant must assess the restriction on vendors
that draft procurement specifications/statements of work under 2 CFR 200.319(b).[^competition]

Before changed work begins, both organizations approve a written amendment stating
changed subjects, dates, costs, match, outputs and acceptance impacts. No oral
request changes the scope. The applicant remains responsible for grant approvals.
Proposed payment is on written acceptance and invoice; payment timing and any
milestone billing must be settled in the executed agreement.

## Sources

[^dc]: DCMI Metadata Terms: https://www.dublincore.org/specifications/dublin-core/dcmi-terms/
[^si]: Smithsonian 3D Digitization, metadata and workflow aims: https://3d.si.edu/about
[^gltf]: Library of Congress, glTF family format description: https://www.loc.gov/preservation/digital/formats/fdd/fdd000498.shtml
[^ply]: Library of Congress, PLY family, local preference and limitations: https://www.loc.gov/preservation/digital/formats/fdd/fdd000501.shtml
[^formats]: Library of Congress, Design and 3D format descriptions (distinct from recommendations): https://www.loc.gov/preservation/digital/formats/fdd/design3D_fdd.shtml ; Recommended Formats Statement: https://www.loc.gov/preservation/resources/rfs/index.html
[^wcag]: W3C, WCAG 2.1 including conformance requirements: https://www.w3.org/TR/WCAG21/
[^rights]: 2 CFR 200.315, intangible property: https://www.ecfr.gov/current/title-2/section-200.315 ; accessible text: https://www.law.cornell.edu/cfr/text/2/200.315
[^cc]: Creative Commons, Attribution 4.0 International: https://creativecommons.org/licenses/by/4.0/
[^apache]: Apache Software Foundation, Apache License 2.0: https://www.apache.org/licenses/LICENSE-2.0
[^match]: 2 CFR 200.306, cost sharing: https://www.ecfr.gov/current/title-2/section-200.306
[^abpp]: NPS, FY2025 Battlefield Interpretation Grant notice, Category F and match documentation (historical notice, not a current solicitation): https://files.simpler.grants.gov/opportunities/d7877c5b-64b6-49ce-baa4-a8ccf904cc90/attachments/293214f4-c8c5-41d9-ad4a-8cdbff9e83c4/P25AS00477_ABPP_BIG_NOFO_25-0826.pdf
[^procurement]: 2 CFR 200.320, procurement methods: https://www.ecfr.gov/current/title-2/section-200.320 ; accessible text: https://www.law.cornell.edu/cfr/text/2/200.320
[^competition]: 2 CFR 200.319(b), competition: https://www.ecfr.gov/current/title-2/section-200.319
