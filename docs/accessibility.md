# Accessibility Conformance Statement (DRAFT)

This is a self-assessment, not an independent accessibility audit. It covers the static HTML and CSS in `viewer.html` and `index.html` and was generated on 2026-09-04 by accessibility checker version 1.0.0.

## Measured support

All measured checks pass. The checker measured: contrast, document-lang, document-title, focus-visible, image-alt, interactive-canvas, link-name, outline-replacement, reduced-motion, single-h1, viewport-zoom. These results support only the specific automated checks listed here; they do not establish complete WCAG 2.1 AA conformance.

## Partially supported content

The interactive 3D canvas does not provide a text alternative for its full visual content. Its accessible name and screen-reader description identify the scan and summarize its catalog blurb and controls. The linked provenance page provides capture, processing, and delivery context, but neither substitute conveys every spatial or visual detail in the scan.

## Keyboard operations

- drag to orbit
- right-drag to pan
- scroll to zoom
- arrow keys to pan

## Known limitations

- This static checker does not test screen-reader behavior, browser rendering, focus order during interaction, pointer gestures, JavaScript failures, captions, cognitive accessibility, or the complete WCAG success-criterion set.
- Contrast is calculated only for CSS foreground/background pairs the checker can resolve from hex or rgba values over the known body background.
- The 3D scene itself has no complete nonvisual equivalent.

Feedback contact: ACCESSIBILITY CONTACT TO BE PROVIDED
