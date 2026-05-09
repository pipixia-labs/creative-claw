---
name: design-canvas-artifact
description: Generate Claude Design-style HTML design artifacts with visible sections, artboards, screen states, and visual variants for design review and AI iteration.
---

# Design Canvas Artifact

Use this private product-design skill when the user asks for UI design, product design, mobile app screens, web design mockups, dashboard design, visual variants, HTML design prototypes, or iterative design edits from screenshots/sketch annotations.

## Intent

The deliverable is a reviewable design artifact, not a production application. The output should make design decisions visible: screens, states, variants, assumptions, visual systems, and tradeoffs should be placed on a canvas where the user and AI can compare them.

## Output Contract

Generate one standalone HTML file that:

- Embeds its own design canvas structure with sections and fixed-size artboards.
- Defines clear local primitives such as `DesignCanvas`, `DCViewport`, `DCSection`, and `DCArtboard`, or equivalent names with the same meaning.
- Uses transform-based canvas navigation for the main board: `translate3d(x, y, 0) scale(scale)`.
- Supports trackpad pinch zoom, two-finger pan, and blank-space drag pan without browser-window scrollbars.
- Posts host synchronization messages when JavaScript is enabled: `__dc_present` on mount and `__dc_zoom` when scale changes; listens for `__dc_set_zoom`.
- Shows important screens and states side by side.
- Uses stable section ids, artboard ids, labels, and component names.
- Keeps visual variants aligned by information architecture while changing visual language.
- Includes a compact brief or design-system artboard when useful.
- Runs locally in a browser without backend services.
- Avoids local absolute file paths.

## Design Rules

- For mobile app work, prefer phone artboards around `393x852` unless the brief names another device.
- For web or dashboard work, use explicit desktop/tablet/mobile artboards when responsive decisions matter.
- Do not hide key states behind routing or tabs. The artifact should support design review at a glance.
- Use lightweight demo interactions only when they clarify a state. Do not build complete business logic.
- Hide scrollbars on the main canvas and artboards. Only keep internal scrolling when the designed screen itself needs to demonstrate scroll behavior.
- Prefer structured reusable components for repeated screens so later AI edits can target the design cleanly.
- If sketch annotations, screenshots, or exported tldraw images are present, interpret them as design feedback and modify the corresponding artboards.

## Code Generation Handoff

When invoking the private design code generation agent, include:

- The user request and selected assumptions.
- The target surface and artboard sizes.
- Required screens, states, and variants.
- Selected visual direction or design system.
- Relevant uploaded files, screenshots, sketch exports, or annotation notes.
- Validation expectations such as "standalone HTML", "visible text", and "no local absolute paths".
