# Insta360 intake for photogrammetry

This workflow turns an Insta360 camera original into the stitched, full-sphere,
2:1 equirectangular JPG or MP4 that `tools/reframe360.py` expects. Keep the
original `.insv`/`.insp`/DNG files and an export-settings screenshot beside the
derivative.

## Heritage-site capture

1. Clean both lenses, remove lens guards when it is safe, and select the correct
   lens-guard mode when they are fitted. Keep people and important fabric at
   least 0.8 m from X-series lenses; parallax is worst at the seam
   ([Insta360 stitching guidance](https://onlinemanual.insta360.com/x4/en-us/troubleshooting/image/stitching)).
2. Shoot ordinary **360 Video**, not Active HDR, PureVideo, TimeShift, or an
   auto-edited mode. Choose manual exposure and white balance, lock them for the
   entire pass, use the lowest practical ISO, and avoid filters. X5 exposes
   Active HDR as a Video option and PureVideo as a separate low-light mode
   ([X5 screen/settings guide](https://onlinemanual.insta360.com/x5/en-us/camera/basicuse/usingthescreen),
   [PureVideo guide](https://onlinemanual.insta360.com/x5/en-us/operating_tutorials/capture-preview/shooting-mode/purevideo)).
   On X4/X3 select Video rather than Active HDR or HDR Photo. This avoids
   temporal tone/exposure changes and multi-exposure motion ghosts.
3. Record at maximum normal 360 resolution: 8K30 on X4/X5, 5.7K30 on X3 and
   ONE RS 1-Inch 360. For still capture use ordinary Photo/Interval rather than
   HDR/PureShot+RAW: 72 MP is 11904×5952 on X4/X5 and 11968×5984 on X3;
   ONE RS 1-Inch 360 is 6080×3040. The vendor's consolidated specification lists
   the X-series sizes ([X-series specifications](https://onlinemanual.insta360.com/onex2/en-us/specs/shooting-specs));
   the ONE RS table lists its 360 resolutions
   ([ONE RS resolutions](https://onlinemanual.insta360.com/oners/ja-jp/faq/firmware/resolution)).
4. Mount vertically at roughly 1.6–1.8 m, lenses facing sideways across the
   walking direction. Walk smoothly at about 0.5 m/s. Do not spin the pole.
   Make two overlapping passes: an outbound centerline/grid pass, then a return
   pass offset roughly 1–2 m, with 30–50% path overlap and slower turns. Pause
   briefly at entrances and complex detail. These speeds/heights/overlaps are a
   field recipe, not camera-vendor specifications; validate them with a short
   reconstruction before the full survey.
5. Prefer remote start and leave the scene. Otherwise keep the pole close to the
   body and accept that the operator and nadir must be masked. Capture a color
   target and measured control/check distances. Respect access restrictions and
   do not touch historic surfaces.

Hold the camera physically level and export with **FlowState off, automatic
horizon correction off, and Direction/Horizon Lock off** for the preservation
master. That retains the camera-coordinate sphere and lets COLMAP estimate the
real rotation. A second, clearly named stabilized derivative can be tested if
walking shake defeats matching. FlowState and lock rotate (and, depending on
mode/version, may process) frames using gyro data; a locked horizon looks tidy
but is not evidence the camera was level. Insta360 documents these controls but
does not document their geometric error bounds
([Studio basic settings](https://onlinemanual.insta360.com/onex2/en-us/studio/import/basicsetting)).

## Desktop export recipes

Use current Insta360 Studio (the vendor's VR guide requires 5.6.1 or later).
Import every high-resolution file belonging to a take—especially both X3
`_00_`/`_10_` masters—not the LRV proxy. On the Project page choose **2:1
(360)**, do no reframing/keyframing, and export the original/full resolution
([Studio VR export guide](https://onlinemanual.insta360.com/studio/en-us/operation-guide/file-management/instructions-for-vr-panoramic-video-editing-and-export)).
Choose MP4 H.265 at the highest available bitrate (or ProRes 422 if storage and
the local Studio version permit it); export photos as full-resolution JPG.
Studio's documented formats are H.264, H.265, and ProRes 422
([Studio export guide](https://onlinemanual.insta360.com/onex2/en-us/studio/export)).

| Camera | Camera master | Full stitched export target |
|---|---|---|
| X5 | one high-resolution INSV containing both lenses, plus LRV proxy | 7680×3840 MP4; 11904×5952 JPG (72 MP) |
| X4 | one high-resolution INSV at 5.7K+/8K, plus LRV | 7680×3840 MP4; 11904×5952 JPG (72 MP) |
| X3 | at 5.7K, two matching VID INSV masters, plus LRV | 5760×2880 MP4; 11968×5984 JPG (72 MP) |
| ONE RS 1-Inch 360 | matching 360 originals (retain all same-take files) | 5760×2880 MP4; 6080×3040 JPG |

The X4/X5-versus-X3 file grouping is documented in
[X-series file formats](https://onlinemanual.insta360.com/x3/en-us/operating-tutorials/storage/fileformat).
The X5 72 MP target corrects the brief's shorthand: the current official table
gives 11904×5952, not the X3's 11968×5984.

In **Stitching > Stitching Optimization**, begin with **Optical Flow
Stitching**; Insta360 recommends it for export and close/seam problems. Dynamic
Stitching adapts over time and can make seam geometry fluctuate, so use it only
after a comparison clip shows fewer artifacts. “Scene-optimized” is not a
choice in the current official Studio documentation found for these models;
current documentation lists AI, Optical Flow, Dynamic, or Off, so treat that
label as version-dependent/unverified. Select the physically correct lens guard
or dive-case profile. Sources:
[stitching troubleshooting](https://onlinemanual.insta360.com/x4/en-us/troubleshooting/image/stitching) and
[current stitching choices](https://onlinemanual.insta360.com/x6-user-manual/en-us/operating-tutorials/stitching/in-camera-stitching).

### Metadata caveat

Studio's public export pages do **not** promise which GPano XMP fields survive
JPG/MP4 export. In particular, embedding `ProjectionType=equirectangular`,
`FullPanoWidthPixels`, `FullPanoHeightPixels`, and pose heading/pitch/roll is
unverified and must be checked per export. ExifTool documents these as members
of the XMP-GPano family ([ExifTool tag table](https://www.exiftool.org/TagNames.pdf)).
If Studio produces a valid 2:1 panorama but omits projection metadata, preserve
the original and add a metadata-tagged derivative with ExifTool or a spherical
metadata injector before intake; do not invent heading/pitch/roll values.

## What INSV is—and is not

INSV is Insta360's proprietary camera-original extension around an MP4-family
container holding fisheye H.264/H.265 media and proprietary calibration/gyro
metadata. Layout varies: X3 5.7K uses two master files, while X4/X5 use one
master containing both lenses. FFmpeg/ffprobe can often demux/decode the media
streams and its `v360` filter can reproject known fisheye layouts, but it does
not reproduce Insta360's calibrated seam, gyro processing, or current optical
flow merely by renaming `.insv` to `.mp4`. Therefore INSV is deliberately not
accepted by the intake tool.

Community projects include
[`insv-to-yt`](https://github.com/peterbraden/insv-to-yt) (older paired-file
FFmpeg pipeline) and [`insv-stitch`](https://github.com/BenjaminHenriksson/insv-stitch)
(X5-oriented experimental stitching). Their correctness for every listed
camera, firmware, lens guard, calibration record, and moving seam is
**unverified**; use Studio as the reference export and compare seams before any
heritage deliverable. FFmpeg's MOV demuxer recognizes `st3d`/`sv3d` spherical
boxes ([FFmpeg patch documentation](https://ffmpeg.org/pipermail/ffmpeg-devel/2016-November/202922.html)),
which is distinct from decoding Insta360's private gyro/calibration trailer.

## Intake check

Run:

```sh
python tools/insta360_intake.py exported.jpg
python tools/insta360_intake.py exported.mp4 --json
```

The checker prefers ExifTool (`PATH`, then `/opt/homebrew/bin/exiftool`), falls
back to reading a simple embedded XMP packet, and uses ffprobe (`PATH`, then
`/opt/homebrew/bin/ffprobe`) for video dimensions, tags, and spherical/stereo
side data. “Ready” requires decodable, exact 2:1 pixels plus an equirectangular
GPano tag or MP4 spherical side data. Missing pose and stitching-software tags
are warnings: `reframe360.py` can run without them, but orientation provenance
is weaker. A nonzero exit status means not ready.
