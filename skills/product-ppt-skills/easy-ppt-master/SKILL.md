---
name: easy-ppt-master
description: Use Creative Claw's native SVG route to create high-control, editable PPTX decks in the spirit of ppt-master: design strategy, sequential SVG pages, quality gate, and SVG-to-DrawingML PPTX export.
---

# Easy PPT Master

Use this private PPT product skill when the user wants a ppt-master-like result inside Creative Claw: high-control visual pages generated as SVG, validated against the native DrawingML converter, then exported to an editable `.pptx`.

This skill borrows ppt-master's core idea:

```text
ConfirmedRequirement
-> DeckContentPlan
-> design strategy and SVG execution plan
-> sequential SVG page generation
-> SVG quality gate
-> native DrawingML PPTX export
```

It does not use ppt-master's project directory protocol, `project_manager.py`, `finalize_svg.py`, `svg_to_pptx.py`, animation export, narration, or template-copy workflow. Creative Claw's `PptProductManager` owns workspace paths, session state, output recording, and delivery.

Creative Claw now bundles ppt-master layout templates under the PPT product SVG template registry. The SVG route may auto-select one for strong task matches, or honor an explicit system `template_id`. These templates guide design strategy and page structure; the executor must still generate converter-safe SVG pages.

## When To Use

Use this skill when:

- The user asks for `ppt-master`, "easy ppt master", SVG route, editable PPTX, high-quality PPTX, or stronger visual control than the default HTML route.
- The deck benefits from native editable shapes, grouped vector layouts, charts, diagrams, gradients, markers, and controlled image clipping.
- The requested output is `.pptx` or an editable PowerPoint-style deck.

Do not use this skill when:

- The user explicitly asks for a single-file HTML deck or magazine web PPT.
- The user needs a specific uploaded PPTX template edited in-place; that belongs to a future XML/OOXML route.
- The request is only to draft content or review an existing deck without producing a file.

## Available Product Capabilities

Use only product tools and experts exposed by `PptProductManager`.

Experts:

- `PptDesignStrategyExpert`: creates a generic design strategy and `PptSvgExecutionPlan`.
- `PptSvgDeckExecutorExpert`: generates one converter-safe SVG page per planned slide.
- `PptContentPlanningAgent`: already produced the `DeckContentPlan` before this skill runs.

Tools:

- `list_ppt_experts`
- `invoke_ppt_expert`
- `save_ppt_design_strategy`
- `save_ppt_svg_execution_plan`
- `read_ppt_svg_execution_plan`
- `save_ppt_svg_page`
- `check_ppt_svg_quality`
- `export_ppt_svg_to_pptx`
- `dispatch_ppt_route`

## Execution Discipline

Run the workflow serially. Do not generate pages before the design strategy and SVG execution plan are saved.

Use one confirmation point in the strategy phase when the surrounding product workflow requires user confirmation. The relevant decision set is:

1. Canvas format
2. Page count
3. Audience and scenario
4. Style objective
5. Color scheme
6. Icon approach
7. Typography plan
8. Image approach

After the user has confirmed the requirement and content plan, do not stop for extra approvals unless a hard blocker appears.

## Workflow

### Step 1: Inspect Current State

Use the provided `ConfirmedRequirement` and `DeckContentPlan` as content truth. Do not invent source files, citations, generated paths, or missing facts.

Call `list_ppt_experts` and verify `PptDesignStrategyExpert` and `PptSvgDeckExecutorExpert` are available.

### Step 2: Build Design Strategy

Before invoking the design expert, let the SVG route resolve any explicit or automatic system SVG layout template selection. If a template is selected, include its design specification and page-type structure as guidance.

Call:

```text
invoke_ppt_expert("PptDesignStrategyExpert", ...)
```

The expert must save:

- `PptDesignStrategy`
- `PptSvgExecutionPlan`

The execution plan is the contract for every SVG page. It should use:

```text
converter_profile = native_drawingml_ppt_master_baseline_v1
```

The plan should include canvas, palette, font stack, page rhythm, icon/image policy, supported tags, forbidden tags, forbidden attributes, and editability level.

### Step 3: Generate SVG Pages Sequentially

Call:

```text
invoke_ppt_expert("PptSvgDeckExecutorExpert", ...)
```

Generation rules:

- Generate pages in slide order.
- Before each page, call `read_ppt_svg_execution_plan`.
- Save each page exactly once with `save_ppt_svg_page`.
- Keep visible text as SVG `text` / `tspan` where possible.
- Use top-level `<g id="...">` groups for meaningful editable objects.
- Use only local workspace images or data image URIs.

Allowed baseline SVG features:

- Visual tags: `rect`, `circle`, `ellipse`, `line`, `path`, `polygon`, `polyline`, `text`, `tspan`, `image`, `g`.
- Defs features: `linearGradient`, `radialGradient`, `marker`, `clipPath`, and basic shadow/glow filters.
- Path commands: `M/L/H/V/C/S/Q/T/A/Z`.
- Image handling: `preserveAspectRatio` meet/slice and image-only `clipPath`.

Forbidden SVG features:

- `style`, `class`, external CSS, `foreignObject`, `mask`, `script`, events, `symbol/use`, `textPath`, animation tags, remote images, and `rgba()`.

### Step 4: Quality Gate

Call:

```text
check_ppt_svg_quality(svg_page_paths=[])
```

Passing states:

- `pass`
- `warning` only when warnings are acceptable and no converter error exists

If status is `error`, fix the offending page and run the quality gate again. Do not export a PPTX from invalid SVG.

### Step 5: Export Editable PPTX

Call:

```text
export_ppt_svg_to_pptx(pptx_file_name="deck.pptx", svg_page_paths=[])
```

The exported PPTX is the final artifact. Do not also save an HTML fallback unless SVG export fails and the product manager explicitly chooses fallback delivery.

### Shortcut: Built-In SVG Route

If the task does not require custom per-step intervention, call:

```text
dispatch_ppt_route(route="svg")
```

Use the full expert/tool sequence above when the user explicitly asks for ppt-master-like control, or when you need to inspect/fix SVG pages before export.

## Quality Bar

- The deck must have a clear narrative, not just a list of topics.
- Slide titles should be presentation-facing claims or clear section labels.
- Visual rhythm should vary: cover/chapter/anchor pages, dense information pages, and breathing pages.
- The final `.pptx` should contain editable DrawingML text/shapes/images whenever the converter supports them.
- Do not claim final success unless `export_ppt_svg_to_pptx` or `dispatch_ppt_route("svg")` produced a PPTX path.
