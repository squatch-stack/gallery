# DJI Air 3S subject missions

Offline planning only; this tool never talks to the aircraft. Review the operator
card on the ground. A generated route is not permission or an obstacle survey.

## Unverified against a DJI Fly export

All aircraft settings and flight-policy numbers in drone_defaults.json are
provisional operator choices, not validated aircraft specifications or legal
limits. In particular the enterprise WPML namespace 1.0.6, aircraft/payload
enumerations, height reference, action support (including waypoint photographs),
and any requirement for wpmz/res/ are unverified for DJI Fly on RC 2 / Air 3S.
The synthetic test skeleton proves serialization only, never compatibility.

Export a small mission by hand from the actual controller, then run
`python3 tools/drone_plan.py --from-export export.kmz --write-skeleton`.
The command asks for the DJI Fly version and aircraft and saves a coordinate-free
`tools/dji_air3s_skeleton.json`. Keep that small file under version control after
review; do not commit the real KMZ. Namespace, element order, config, enums and
Placemark layout come from that file. Unknown geometry-bearing fields or extra
resource members are refused rather than silently invented or discarded.
Inspect heights, headings, gimbal sign and action behavior in DJI Fly and on the
ground before flight. Skeleton availability is structural evidence, not a flight
certification; source metadata and warnings remain in every plan.

Without a skeleton, JSON and the operator card are still written, but KMZ output
is refused (exit 2). `--unverified-schema` explicitly permits the synthetic
enterprise guess and stamps SCHEMA UNVERIFIED. Use `--skeleton PATH` to select a
reviewed skeleton. `--diff export.kmz --against ours.kmz` compares XML element
paths and archive members, exiting 1 when ours lacks any export path. A passing
structural diff does not validate values or semantics.

## Geometry and camera

Projection is a local ENU tangent plane on WGS-84, not UTM. At the supported
spans its tangent-plane error is far below the aircraft's own GNSS error.
There is NO terrain model and no terrain following. GNSS only, no RTK.
The centre is the subject centre; EXIF gives the camera position only, so
`--from-photo` is a convenience requiring an independently checked radius/bbox.
Latitude is limited to 85 degrees; antimeridian-spanning boxes are unsupported.

The pinhole assumption is 24 mm equivalent on a 36 mm width: tan(HFOV/2)=0.75.
VFOV uses the landscape pixel aspect ratio. At 8192 pixels and 120 m,
GSD=180/8192=0.02197265625 m. A requested 0.05 m GSD permits 273.066667 m,
so the 120 m policy ceiling binds for that explicit request.

Without `--gsd`, the target is `min(subject_height / subject_pixels_across, gsd_m)`:
the defaults are 2000 pixels across and a 0.05 m GSD cap. Subject height sets the
scale, independently of the coverage radius. The chosen altitude is the smaller
of the target's pinhole altitude and max_altitude_agl_m (120 m). A 20 m barn at
40 m radius therefore targets 0.01 m GSD and 54.613 m AGL, with
`binding_constraint=subject_pixels_across`; a 50 m subject reaches the 120 m
ceiling, reporting `max_altitude_agl_m`. An explicit `--gsd` replaces the
subject-derived target and reports `gsd_m` when it binds below the altitude cap.

The obstacle floor is subject height plus clearance (20 m). For a 6 m
springhouse: 6/2000 = 0.003 m GSD, giving a 16.384 m ceiling below the 26 m
obstacle floor. The planner refuses and points to `tools/capture_plan.py` for
capture on foot. For example, this command refuses before writing any files:

```sh
python3 tools/drone_plan.py --subject springhouse --place "Private field" --authorization "Owner approval recorded" --lat 37 --lon -93 --radius 30 --subject-height 6 --out out/springhouse --unverified-schema
```

An explicit altitude is never clamped and reports `operator_altitude` when
accepted. It must still satisfy the target GSD ceiling: in the current
implementation `--altitude` alone does not replace the subject-derived target,
so relaxing that target requires `--gsd` as well. Oblique GSD uses the slant
range altitude/sin(pitch), separately checked against max_gsd_m.

Line spacing uses horizontal footprint and side overlap; photo spacing uses
vertical footprint and forward overlap. At an explicit 120 m altitude these are 54 m and 27 m.
Timed candidates 2, 3, 5 seconds give 13.5, 9, 5.4 m/s and 1.536, 1.024,
0.6144 pixels of blur at 1/400 second. The shortest admissible interval is 5 s.
Forced timing recomputes actual overlap. Turn photographs are redundant and the
operator starts the camera timer by hand in interval mode.

Double-grid contains two orthogonal nadir grids and four oblique side passes.
Line ends extend by half a photo spacing plus the turn radius. Grid lines are
sampled so waypoint-mode photographs also meet the planned spacing. Orbit
station counts use capture_plan.ring_geometry with SLANT clearance
hypot(radius-size/2, altitude-subject_height/2), adapting its level-camera
footprint solver. Orbit radius encloses the subject with a clearance margin;
a separate nadir grid uses nadir_grid_altitude_m. Overlap is a footprint proxy,
not proof of visibility or successful registration.

Takeoff offset is takeoff elevation minus subject ground elevation; execute
height is AGL minus that offset. Zero is an explicit assumption when omitted.
The subject centre is the assumed home location; replace planning assumptions
with a ground survey before use. Duration includes geodesic legs, turns, climb,
transit and RTH allowances. Multi-battery output splits only at pass boundaries;
a pass longer than a usable battery is refused. Every leg includes return and
launch allowances. Frames are ceil(total_seconds/interval), including turns.
Frame count cannot predict compressed payload size: use runbook stage 6 to
measure against make_scene_payload.py's 256 MiB limit.

## Permission screening

The prohibited-place terms are sourced to docs/capture.md > Before you leave,
which records drones as prohibited in NPS units. A case-insensitive substring
match on operator text is a screening aid and not a legal determination. The
absence of a match is not permission. There is no override for this refusal.
A nonempty authorization description and place are required. Obtain actual
site and airspace authorization independently; this offline tool does not check
current restrictions.

## Operation

`python3 tools/drone_plan.py --subject shed --place "Private field" --authorization
"Owner approval recorded" --lat 37 --lon -93 --radius 30 --subject-height 10
--gsd 0.05 --altitude 120 --created 2026-09-04T12:00:00Z --out out/mission --unverified-schema`

Use `--mode orbit`, `--resolution 12mp`, `--altitude`, `--gsd`, `--heading`,
`--interval`, `--speed`, `--photo-mode waypoint`, `--takeoff-offset`, and
`--rth-altitude` only after reviewing the card. `--allow-multi-battery` produces
numbered leg KMZs if needed. `--force` permits replacing existing outputs.
`--json` keeps stdout identical to the JSON file; warnings and the schema stamp
then go to stderr. All other refusals happen before any output is written.
