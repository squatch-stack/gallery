# Field Scans

Gaussian-splat and mesh scans of real places, rendered in the browser with
[Spark](https://sparkjs.dev) on three.js.

**Live:** https://squatch-stack.github.io/gallery/

- `index.html`: the catalog. `viewer.html?scene=<stem>` opens one scene from `scenes.json`.
- `scenes/`: web-delivery assets, `.sog` splats and `.glb` meshes.
- `provenance/`: one page per scene saying how it was captured and cleaned, with file hashes.
- `checks.json`: the saved web-mobile budget checks shown on each card.

Scans were cleaned with Squatch Stack tooling and the
[holo](https://github.com/squatch-stack/hdc-holo) exporters. The capture and training pipeline is
maintained separately.

## License

Site code: Apache-2.0. Scans: CC BY 4.0 unless a scene says otherwise.
