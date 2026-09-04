# Lane A report

## Diff summary

- Forward and backward rasterization now dispatch the tile grid as `(tile_bounds.x, tile_bounds.y, 1)` instead of flattening all tiles onto X.
- Both raster WGSL entry points take `workgroup_id`; `workgroup_id.xy` is the tile coordinate and `local_invocation_index` is mapped within the 16x16 tile. `pix_id`, `tile_id`, bounds checks, and downstream raster/training math are unchanged.
- `create_dispatch_buffer` now flattens the requested workgroup total and emits `(min(n, 65535), ceil(n / 65535), 1)`.
- The WGSL consumers of that indirect dispatch (visible projection, intersection mapping, and radix-sort count/reduce/scan-add/scatter) flatten `(workgroup_id.x, workgroup_id.y)` back to the original linear workgroup ID. The CubeCL `get_tile_offsets` consumer already uses `ABSOLUTE_POS`, which CubeCL defines as the flattened invocation position across all dispatch axes.

No other reads of `global_id.x` remain in either raster shader. The only helper formerly used there, `map_1d_to_2d`, remains in use for local in-tile coordinates.

## One-axis launch audit

The specified image is 5712x4284 = 24,470,208 pixels. With 16x16 tiles it has `ceil(5712/16) * ceil(4284/16) = 357 * 268 = 95,676` tiles.

- Forward raster: previously 95,676x1x1, exceeding 65,535. Changed to 357x268x1.
- Backward raster: same 95,676-workgroup overflow. Changed to 357x268x1.
- `create_dispatch_buffer` visible-splat projection and intersection mapping: at 1,500,000 visible splats and workgroup size 256, `ceil(1,500,000/256) = 5,860`; this does not exceed 65,535.
- Depth radix sort for 1,500,000 splats: its block size is 256 threads times 4 elements = 1,024 elements, so it needs `ceil(1,500,000/1024) = 1,465` count/scatter workgroups. Its reduce stages are smaller. This does not exceed 65,535.
- Tile-intersection radix sort: intersections are capped by `INTERSECTS_UPPER_BOUND = 512 * 65,535 = 33,553,920`. At the 1,024-element sort block size the cap needs 32,768 count/scatter workgroups. The reduce/scan-add dispatch needs `16 * ceil(32,768/1024) = 512` workgroups. Neither exceeds 65,535.
- Tile-offset/visibility map at `render.rs` (indirect count made with workgroup size 256): the intersection cap needs `ceil(33,553,920/256) = 131,070` workgroups, so it can exceed the per-axis limit. The new indirect dispatch is 65,535x2x1. Its CubeCL launch uses a 512-thread cube and `ABSOLUTE_POS`; this preserves the existing 2x over-dispatch and bounds guard while flattening both dispatch axes.
- `calc_cube_count` for forward projection and backward projection: 1,500,000 splats at workgroup size 256 requires 5,860 workgroups, below the limit.
- `calc_cube_count` in prefix sum: the largest first-level input here is the per-splat intersection-count array of 1,500,001 elements. At 512 threads it requires 2,930 workgroups; recursive scan levels and add passes are smaller. It cannot exceed 65,535 for 1.5M splats.
- Fixed one-axis utility launches (`CreateDispatchBuffer` and radix `SortScan`) are 1x1x1 and cannot exceed the limit.

Therefore, for the requested 24.5 MP / 1.5M-splat case, only the raster tile dispatches and the 256-thread intersection tile-offset launch can cross 65,535. All three are now two-dimensional.

## Build and static verification

- Baseline `CARGO_HOME=/private/tmp/brush-lane-a-cargo /opt/homebrew/bin/cargo build --release -p brush-cli`: passed in 7m23s. (`brush-cli` is a library package at v0.3.0, so `brush-app` was also built to obtain `target/release/brush_app`.)
- Patched required build: passed in 1m46s.
- Patched `cargo build --release -p brush-app`: passed in 2m35s.
- Patched `cargo test --release -p brush-kernel -p brush-sort -p brush-render -p brush-render-bwd --no-run`: passed in 1m24s and produced all four unit-test executables.
- `git diff --check`: passed.
- No warnings were introduced. Cargo printed only its existing future-incompatibility notice for dependency `block v0.1.6`.

## Real-data test results

Dataset availability and the expected `images/` and `sparse/0/` directories were verified read-only.

All four requested before/after application runs were attempted on this Mac. This execution environment exposes no Metal adapter, so every run panicked during WGPU adapter selection, before dataset loading or any compute dispatch:

`No possible adapter available for backend. Falling back to first available.: NotFound { active_backends: Backends(METAL), requested_backends: Backends(METAL), supported_backends: Backends(VULKAN | METAL), no_fallback_backends: Backends(0x0), no_adapter_backends: Backends(METAL), incompatible_surface_backends: Backends(0x0) }`

- Before patch, max resolution 5712: exit 101; real 3.03s; no PLY.
- Before patch, max resolution 1920: exit 101; real 0.02s; no PLY.
- After patch, max resolution 5712 with export name `test5712.ply`: exit 101; real 2.71s; no PLY.
- After patch, max resolution 1920: exit 101; real 0.02s; no PLY.

Because failure occurred before training, I could not verify 200 completed steps, the expected pre-patch 95,676-dispatch validation panic, exported PLY vertex counts, or per-step timing. No GPU other than this Mac's was used, and no files were written outside `target/`, `out/`, this report/patch, and the permitted temporary Cargo cache.

LANE A DONE
