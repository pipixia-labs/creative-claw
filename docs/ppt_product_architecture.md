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
  │   ├─ PptRequirementAnalysisAgent
  │   ├─ PptSystemSelectionAgent
  │   └─ PptContentPlanningAgent
  │
  ├─ tools
  │   ├─ list_ppt_experts
  │   ├─ invoke_ppt_expert
  │   ├─ build_initial_deck_content_plan
  │   └─ dispatch_ppt_route
  │
  └─ routes
      └─ html route calls PptHtmlPageGenerationExpert
```

This structure is intentionally product-centered: routes and skills consume experts owned by `PptProductManager`; they do not create independent PPT execution agents for product orchestration.

## Experts

### PptHtmlPageGenerationExpert

Product-level expert that turns a `DeckContentPlan` into one editable, PPT-friendly HTML fragment per slide.

Current users:

- Built-in HTML route, for `free_design` page generation.
- Private or user-authored PPT skills, through `invoke_ppt_expert("PptHtmlPageGenerationExpert", ...)`.

This expert keeps `save_html_route_pages` as its internal save tool. That tool is not a general product tool because it depends on HTML route state keys.

### PptRequirementAnalysisAgent

Internal requirement expert that writes `ConfirmedRequirement` JSON.

It normalizes the user request, source inputs, output format, route hints, template hints, language, audience, scenario, slide-count policy, and editability requirement.

### PptSystemSelectionAgent

Internal selection expert that chooses the delivery system for one PPT request.

It decides between:

- A private product-ppt skill.
- A built-in PPT route, currently the HTML route.

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
- `save_ppt_private_skill_html`

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

## Skill Boundary

Private product-ppt skills are selected and run by `PptProductManager` itself.

A selected skill may:

- Read its own skill files and references.
- Use PPT product tools.
- Call product experts through `invoke_ppt_expert`.
- Save final HTML artifacts through `save_ppt_private_skill_html`.

A selected skill should not require a separate private skill execution agent. The execution flow is led by the skill content while `PptProductManager` provides product tools, experts, and state.

## Current Direction

The intended direction is:

- Product manager owns product experts.
- Routes consume product experts by injection.
- Skills consume product experts through `invoke_ppt_expert`.
- Route-internal tools stay internal unless they become stable product tools.
- Private/user-authored skills can orchestrate product experts, tools, and resources without needing a new hard-coded agent class.
