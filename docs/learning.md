# Learning this craft

Five stages. Each one names what to install, what to read, and an exercise on a
scene that already exists in this repository, so you are never learning against
a blank page. Work through them in order; each depends on the one before.

The order is not arbitrary. Looking comes first because every later judgement
is a judgement about a picture, and the tools cannot make it for you: the
deliverable checks pass a scan with its wheel rim deleted.

---

## 1. Look properly

**Install** nothing. **Read** [runbook.md](runbook.md) stage 11.

Any scene can be opened at a camera position you choose, by putting the
position in the address:

```sh
python3 -m http.server 8000
```

then `viewer.html?scene=cannon&az=30&el=15&d=1.4`, where `az` turns around the
subject, `el` raises the camera, and `d` is distance as a multiple of the
framing span. The same three numbers always give the same view, which is what
makes a defect reportable and a before-and-after honest.

`tools/inspect_page.py` writes a contact sheet of every scene at three angles.

**Exercise.** Generate the sheet, look at all ten scenes, and write one sentence
per scene naming its worst defect. Then open the two or three worst at close
range and find a second defect in each. Compare your list against the
[results page](results.md).

You are looking for: a hard straight edge (a crop cutting through material), a
halo of streaks (vegetation that moved between photographs), a floating
fragment, a surface that dissolves when you orbit past it (too few views saw
it), and a part that is simply missing.

---

## 2. Clean by hand

**Install** nothing; [SuperSplat](https://superspl.at/editor) runs in the
browser and is free and open source. **Read** its selection tools: rectangle,
lasso, polygon, brush and sphere, then Delete.

It opens the `.sog` files in `scenes/` directly. It removes splats; it does not
recolour them. Export as PLY.

**Exercise.** Open `scenes/oak.sog`, delete the floaters the automated pass left
behind, export a PLY, and bring it back:

```sh
python3 tools/clean_export.py ~/Downloads/oak.ply --stem oak-hand --out out --archive out
python3 tools/candidate_sheet.py out/oak-hand.sog --stem oak --serve
```

Judge your edit against the current scene at the same three angles. Ask whether
you removed anything the subject needed. That question is the whole skill.

---

## 3. Let the machine do the bulk

**Read** [runbook.md](runbook.md) stages 4, 7a and 7.

Three tools do most of what a person would do by hand, and each has a boundary
worth knowing:

- `tools/make_subject_masks.py` finds the subject in every photograph from a
  text prompt. Used at training time as an alpha channel, it stops the trainer
  fitting the background at all. This is the strongest lever in the pipeline
  and it must be pulled before training.
- `tools/prune_by_views.py` projects every splat into views that have masks and
  keeps what they agree on. It rescues a scene trained without masks. It cannot
  undo a decision made at training time: on a scene already trained with masks
  it removes almost nothing.
- `tools/clean_export.py` applies an opacity floor, a crop, an optional splat
  budget, and packs the delivery formats.

**Exercise.** Run the pruner on a scene trained without masks and on one trained
with them, and compare what it removed. Read the held-out precision and recall
it prints. Then look at both results. The lesson is in the gap between the
numbers and the pictures.

---

## 4. Frame and present

**Install** Blender and the 3DGS Render add-on, both free:

```sh
brew install --cask blender
```

then the add-on from the KIRI Engine GitHub release, recorded with
`tools/vet_download.py` before it is enabled. **Read**
[third-party.md](third-party.md) first; installing anything here has a
procedure, and the procedure is the point.

Blender is where a scan becomes a picture a client can read: an orthographic
elevation, a turntable, a scale bar, a figure in a report.

**Exercise.** Import the cannon, set an orthographic camera square to it, and
render an elevation and a twelve-second turntable. Compare your elevation
against a photograph of the same subject. This is the deliverable the statement
of work promises.

---

## 5. Plan a capture, then make one

**Read** [capture.md](capture.md) and [drone.md](drone.md).

`tools/capture_plan.py` plans a walk-around: rings, heights, station counts and
the overlap each achieves, from the guide's own numbers.
`tools/drone_plan.py` plans a flight, and refuses more than it accepts.

**Exercise, on foot.** Plan the springhouse and a subject you can reach, walk
one of them, and run it through `tools/subject_run.py` end to end. Compare the
frames you actually took against the plan.

**Exercise, in the air.** Plan the same subject as a flight and read the
refusals. A small subject will be refused, and the refusal is correct: a three
metre cannon is walked around, not flown over. Before any real flight, do the
ground checks in [drone.md](drone.md), in particular exporting one mission from
DJI Fly by hand so the planner can be shown what the aircraft actually expects.

Drones are prohibited on National Park Service land, which includes Wilson's
Creek. The planner refuses those places and will not be talked out of it.

---

## What to learn next, once these are habit

- **Solving.** `tools/solve_subject.py` and what a fragmented solve looks like.
  Most bad scans are bad captures, and the solve is where that first shows.
- **Provenance.** `tools/provenance.py` and why a scan without a record of how
  it was made is worth less to an institution than one with it.
- **The gates.** `tools/gate.sh` and what each check is protecting against.

## The one habit that matters most

Look at the thing. Every serious defect found in this gallery was found by
opening a scene and orbiting it, not by reading a number. The numbers are there
to tell you where to look and to stop a regression you would not have thought
to check. They have never once told anyone that a scan was good.
