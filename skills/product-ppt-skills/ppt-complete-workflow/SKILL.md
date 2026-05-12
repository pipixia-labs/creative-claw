---
name: ppt-complete-workflow
description: Complete PPT production workflow for PptProductManager, covering requirement normalization, story planning, visual direction, route selection, asset planning, PPTX delivery, and quality checks.
---

# PPT Complete Workflow

Use this private skill when PptProductManager needs a complete PPT-making workflow rather than only the built-in HTML route defaults.

## When To Use

- The user asks for a complete PPT, slide deck, keynote-style talk, teaching deck, report deck, or presentation package.
- The user cares about narrative, content structure, visual direction, charts, images, speaker context, or delivery quality.
- The user does not explicitly require the built-in HTML route.
- The task has ambiguous production path and needs a product-manager decision.

## System Choice

Creative Claw currently has multiple PPT systems:

- Built-in HTML route: creates an HTML deck, preview images, quality report, and editable PPTX. Prefer it when the user needs fast MVP output, reviewable HTML, editable text/shapes, or no user-supplied PPTX template.
- Private complete PPT skill workflow: use this skill as the planning and execution guide when the task needs richer deck thinking, route comparison, narrative shaping, visual-system decisions, or future non-HTML pipelines.
- SVG route: future option for high-control visual pages and SVG-to-PPTX workflows.
- XML route: future option for user-uploaded PPTX templates and native OOXML editing.

If the user explicitly specifies a system, route, template workflow, or skill, follow the user's choice when available. If it is not implemented, report that clearly and do not pretend the output was generated.

If the user does not specify a system, choose freely based on task fit. Record the assumption in the requirement or result summary.

## Workflow

1. Normalize the requirement:
   - topic
   - audience
   - scenario
   - slide count
   - language
   - aspect ratio
   - source materials
   - editability requirement
   - requested route or system, if any

2. Confirm the requirement with the user before reading or generating large supporting assets.

3. Read source materials and extract:
   - core claims
   - evidence
   - key visuals or tables
   - terms that must remain accurate
   - missing information or assumptions

4. Build a template-independent content plan:
   - deck title
   - narrative spine
   - slide-by-slide titles
   - purpose and takeaway for every slide
   - concise content blocks
   - visual intent per slide

5. Confirm the content plan with the user before image search or image generation.

6. Resolve assets only after content confirmation:
   - use user material first when suitable
   - use generated images for illustration-heavy slides
   - use search only for reference material or factual visual grounding
   - keep readable text editable in PPTX, not baked into images

7. Produce the deck through the selected system.

8. Run delivery checks:
   - final PPTX exists
   - slide count matches the confirmed plan
   - core text is present
   - obvious placeholders are removed
   - generated assets are referenced correctly
   - known editability caveats are reported

## Quality Bar

- The deck should have a clear story, not a list of unrelated slides.
- Slide titles should be audience-facing claims or topics, not raw instructions.
- Content should be concise enough for presentation use.
- Visuals should support the takeaway, not decorate the page.
- Do not invent facts, numbers, citations, or file paths.
- Do not use local absolute paths in user-facing content or generated documents.
