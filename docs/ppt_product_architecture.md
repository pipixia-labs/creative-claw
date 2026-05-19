# PPT Product Architecture

This document records the current PPT product boundary around `PptProductManager`.

## Product Boundary

`PptProductManager` owns PPT and PowerPoint production end to end. It is the product-level ADK `LlmAgent` for `.pptx`, PowerPoint, PPT, and editable slide-deck requests.

The product manager is responsible for:

- Normalizing and confirming PPT requirements.
- Selecting between private product skills and built-in routes.
- Managing PPT product-level experts.
- Exposing product tools to selected skills.
- Dispatching built-in route workflows.
- Returning delivery status, output files, warnings, and manifests.

## Structure

```text
PptProductManager
  ├─ experts
  │   ├─ PptHtmlPageGenerationExpert
  │   ├─ PptDesignStrategyExpert
  │   ├─ PptSvgDeckExecutorExpert
  │   ├─ PptRequirementAnalysisAgent
  │   ├─ PptSystemSelectionAgent
  │   └─ PptContentPlanningAgent
  │
  ├─ tools
  │   ├─ list_ppt_experts
  │   ├─ invoke_ppt_expert
  │   ├─ save_ppt_design_strategy
  │   ├─ save_ppt_svg_execution_plan
  │   ├─ read_ppt_svg_execution_plan
  │   ├─ save_ppt_svg_page
  │   ├─ check_ppt_svg_quality
  │   ├─ export_ppt_svg_to_pptx
  │   ├─ build_initial_deck_content_plan
  │   └─ dispatch_ppt_route
  │
  └─ routes
      ├─ html route calls PptHtmlPageGenerationExpert
      └─ svg route calls PptDesignStrategyExpert and PptSvgDeckExecutorExpert
```

This structure is intentionally product-centered: routes and skills consume experts owned by `PptProductManager`; they do not create independent PPT execution agents for product orchestration.

## Experts

### PptHtmlPageGenerationExpert

Product-level expert that turns a `DeckContentPlan` into one editable, PPT-friendly HTML fragment per slide.

Current users:

- Built-in HTML route, for `free_design` page generation.
- Private or user-authored PPT skills, through `invoke_ppt_expert("PptHtmlPageGenerationExpert", ...)`.

This expert keeps `save_html_route_pages` as its internal save tool. That tool is not a general product tool because it depends on HTML route state keys.

### PptDesignStrategyExpert

Product-level expert that turns `ConfirmedRequirement`, source understanding, and `DeckContentPlan` into a generic PPT design strategy plus a strict SVG authoring contract for the native DrawingML converter.

It saves:

- `PptDesignConfirmation`
- `PptDesignStrategy`
- `PptSvgExecutionPlan`

It does not generate SVG pages or export PPTX.

### PptSvgDeckExecutorExpert

Product-level expert that generates one converter-safe SVG page per planned slide from the saved design strategy and SVG execution plan.

It uses:

- `read_ppt_svg_execution_plan`
- `save_ppt_svg_page`

It reads the current SVG execution plan before page generation, and saved SVG pages are validated against the native converter subset. It does not perform requirement analysis, design strategy generation, quality checks, or PPTX export.

### PptRequirementAnalysisAgent

Internal requirement expert that writes `ConfirmedRequirement` JSON.

It normalizes the user request, source inputs, output format, route hints, template hints, language, audience, scenario, slide-count policy, and editability requirement.

### PptSystemSelectionAgent

Internal selection expert that chooses the delivery system for one PPT request.

It decides between:

- A private product-ppt skill.
- A built-in PPT route, currently HTML or SVG.

The decision should come from the user task, available skill metadata/content, route implementation status, and explicit user choices. It should not rely on hard-coded keyword routing.

### PptContentPlanningAgent

Internal content-planning expert owned by `PptContentPlanner`.

It builds a template-independent `DeckContentPlan` from the confirmed requirement and prepared source material. The plan is the content truth used by routes, skills, and page-generation experts.

## Tools And Helpers

The public product tools most relevant to skill-driven execution are:

- `list_ppt_experts`: lists experts available in the current PPT run.
- `invoke_ppt_expert`: invokes a registered PPT expert from a skill workflow.
- `dispatch_ppt_route`: dispatches the confirmed PPT request to the selected route.

`build_initial_deck_content_plan` is a deterministic helper on `PptProductManager`, not part of the default LLM tool list today. The full product workflow normally uses `build_deck_content_plan`, which can run `PptContentPlanningAgent` when ADK context is available.

Other PM tools support private skill execution:

- `list_product_ppt_skills`
- `read_product_ppt_skill`
- `read_product_ppt_skill_file`
- `save_ppt_system_selection`
- `save_ppt_private_skill_html` for HTML private-skill artifacts.
- `export_ppt_svg_to_pptx` or `dispatch_ppt_route` for PPTX private-skill artifacts that use the SVG route.

SVG route tools are product-level because they are intended for skill-driven orchestration:

- `save_ppt_design_strategy`: validates and stores `PptDesignStrategy`.
- `save_ppt_svg_execution_plan`: validates and stores the `PptSvgExecutionPlan` authoring contract.
- `read_ppt_svg_execution_plan`: lets the SVG executor read current route constraints.
- `save_ppt_svg_page`: validates one generated SVG page against the native converter subset, then saves it into the current generated session route directory.
- `check_ppt_svg_quality`: validates SVG files against the same native converter subset.
- `export_ppt_svg_to_pptx`: converts saved SVG pages into native DrawingML slide XML and writes an editable PPTX.

For `check_ppt_svg_quality` and `export_ppt_svg_to_pptx`, a skill can pass an empty `svg_page_paths` list to use SVG pages already saved in session state.

## Route Boundary

Routes are deterministic or semi-deterministic production workflows. They should not own top-level PPT product orchestration.

The current HTML route flow is:

```text
PptProductManager._dispatch_ppt_route
  -> build_html_route_with_agent
  -> generate_html_pages_with_agent
  -> PptHtmlPageGenerationExpert when free_design and ADK context are available
  -> export_html_pptx
  -> deliver_html_route_quality
```

If no page-generation expert or ADK invocation context is available, the HTML route falls back to deterministic HTML page rendering.

The current SVG route flow is:

```text
PptProductManager._dispatch_ppt_route
  -> build_svg_route_with_agent
  -> optional system SVG layout template auto-selection
  -> PptDesignStrategyExpert when ADK context is available
  -> PptSvgDeckExecutorExpert when ADK context is available
  -> check_svg_pages_quality
  -> export_svg_pages_to_pptx via native DrawingML converter
  -> deliver_svg_route_quality
```

If no SVG experts or ADK invocation context are available, the SVG route falls back to deterministic design strategy and SVG page rendering. The route can now auto-select a bundled ppt-master layout template from `src/productions/ppt/templates/svg/layouts` for strong task matches, or use an explicit system `template_id`. The selected template is design guidance and page-type structure; generated pages must still satisfy the native converter subset. The current exporter parses converter-safe SVG into native DrawingML slide XML, writes media relationships and content types into the PPTX package, and only publishes the requested PPTX after conversion succeeds. The active converter profile is `native_drawingml_ppt_master_baseline_v1`: it supports editable basic shapes, groups, rich `text/tspan`, image resources, `M/L/H/V/C/S/Q/T/A/Z` paths, defs-based linear/radial gradients, simple line/path markers, image-only clipPath, and basic shadow/glow filters.

## Skill Boundary

Private product-ppt skills are selected and run by `PptProductManager` itself.

A selected skill may:

- Read its own skill files and references.
- Use PPT product tools.
- Call product experts through `invoke_ppt_expert`.
- Save final HTML artifacts through `save_ppt_private_skill_html`.
- Export final editable PPTX artifacts through SVG route tools, typically `export_ppt_svg_to_pptx` or `dispatch_ppt_route(route="svg")`.

A selected skill should not require a separate private skill execution agent. The execution flow is led by the skill content while `PptProductManager` provides product tools, experts, and state.

`easy-ppt-master` is the private skill for the ppt-master-style path. It keeps Creative Claw's product workflow and workspace model while using the SVG route's `native_drawingml_ppt_master_baseline_v1` converter profile for editable PPTX delivery.

## Current Direction

The intended direction is:

- Product manager owns product experts.
- Routes consume product experts by injection.
- Skills consume product experts through `invoke_ppt_expert`.
- Route-internal tools stay internal unless they become stable product tools.
- Private/user-authored skills can orchestrate product experts, tools, and resources without needing a new hard-coded agent class.
