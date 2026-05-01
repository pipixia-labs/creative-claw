---
name: design-knowledge-and-skills
description: Index and retrieval guide for CreativeClaw design resources, including task skills, design systems, device frames, and scenario-specific brief elements.
---

# Design Knowledge And Skills

Use this skill when a user asks for UI design, product design, visual prototype, landing page, dashboard, mobile app, deck, poster, social creative, or other design-related output.

This skill is an index skill. It does not contain all design knowledge inline. It tells the agent how to find the smallest useful set of design resources before invoking bottom capabilities such as `CodeGenerationExpert`, image experts, video experts, or built-in tools.

## Resource Files

- `resource-manifest.json`: machine-readable registry of design resources.
- `resource-index.md`: human-readable overview and selection rules.
- `schemas/*.schema.json`: stable DesignProductManager handoff and result contracts.
- `brief-elements/*.json`: scenario-specific key design elements and clarification questions.
- `skills/*/SKILL.md`: task-specific design workflows and output contracts.
- `design-systems/*/DESIGN.md`: product or style design systems.
- `assets/frames/*.html`: reusable device/browser frames.

## Required Workflow

1. Read `resource-manifest.json` first.
2. Identify the user's likely `scenario` and `surface`.
3. Find a matching `brief_element_schema` from `brief-elements/`.
4. Extract fields the user already provided.
5. Compare them with `required_fields`.
6. Ask only the most important missing questions, normally 3 to 7.
7. If the user says to skip questions or asks to proceed directly, use schema defaults and record assumptions in the design brief.
8. Choose exactly one primary task skill.
9. Choose at most one primary design system unless the user asks for comparison or multiple directions.
10. Read only the selected task skill, selected design system, and any required frame/reference files.
11. Build a structured design brief that conforms to `schemas/design-brief-v1.schema.json`.
12. Invoke the right bottom capability:
    - Use `CodeGenerationExpert` for HTML, CSS, JavaScript, prototype, app screen, dashboard, and deck code generation.
    - Use image generation/editing experts for bitmap image work.
    - Use video/audio experts for media generation.
    - Use built-in tools for deterministic file operations, search, validation, and future export helpers.

## Resource Selection Rules

- Treat `brief-elements` as the source of truth for clarification strategy.
- Do not hard-code scenario questions in the manager prompt when a matching brief element schema exists.
- Prefer task skills whose `scenario`, `surface`, and triggers match the user request.
- Prefer design systems that match the product category and tone.
- If the user names a brand or design system, use that as the primary design system when present.
- Never inject resources marked `runtimeEnabled: false` or `referenceOnly: true` into execution context.
- If no brand is given, choose a conservative system for the surface:
  - dashboards and operational tools: `linear-app`, `vercel`, `stripe`, `figma`, or `default`;
  - SaaS landing pages: `stripe`, `linear-app`, `vercel`, `supabase`, or `default`;
  - mobile consumer apps: `airbnb`, `notion`, `xiaohongshu`, `apple`, or `default`;
  - decks: `warm-editorial`, `stripe`, `vercel`, or `default`.
- Use `assets/frames` only when the user asks for device/browser framing or when the selected task skill requires it.

## Design Brief Contract

Before execution, produce a compact design brief with:

- `schema_version`
- `surface`
- `scenario`
- `primary_user`
- `business_domain`
- `goal`
- `content_requirements`
- `visual_direction`
- `design_system`
- `device_frame`
- `interactions`
- `output_format`
- `constraints`
- `assumptions`

The design brief is the handoff to `CodeGenerationExpert` or another bottom capability. The result returned through the Orchestrator should conform to `schemas/design-product-result-v1.schema.json`.

## Current Scope

This version supports resource lookup and clarification for all current built-in task skill families:

- audio jingle
- blog post
- critique / design review
- dashboard / operation data UI / admin console
- dating web
- digital e-guide
- documentation page
- email marketing
- engineering runbook
- finance report
- gamified app
- magazine web PPT / HTML deck / Replit deck / weekly update deck
- HR onboarding
- hyperframes
- image poster / magazine poster
- invoice
- kanban board
- meeting notes
- mobile app / mobile onboarding
- motion frames
- PM spec
- pricing page / SaaS landing page / marketing campaign page
- social carousel
- sprite animation
- team OKRs
- tweaks
- video shortform
- wireframe sketch

Media-oriented scenarios can produce structured briefs and storyboards in the current Design path. Full image, audio, or video execution should be delegated to the corresponding bottom capability when the Orchestrator supports that route.
