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
11. Build a structured design brief.
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
- If no brand is given, choose a conservative system for the surface:
  - dashboards and operational tools: `linear-app`, `vercel`, `stripe`, `figma`, or `default`;
  - SaaS landing pages: `stripe`, `linear-app`, `vercel`, `supabase`, or `default`;
  - mobile consumer apps: `airbnb`, `notion`, `xiaohongshu`, `apple`, or `default`;
  - decks: `warm-editorial`, `stripe`, `vercel`, or `default`.
- Use `assets/frames` only when the user asks for device/browser framing or when the selected task skill requires it.

## Design Brief Contract

Before execution, produce a compact design brief with:

- `surface`
- `scenario`
- `primary_user`
- `audience`
- `goal`
- `platform`
- `content_requirements`
- `interaction_requirements`
- `selected_skill`
- `selected_design_system`
- `selected_frame`
- `constraints`
- `assumptions`
- `output_contract`

The design brief is the handoff to `CodeGenerationExpert` or another bottom capability.

## Current Scope

This first version supports resource lookup and clarification for:

- dashboard / operation data UI
- SaaS landing page
- mobile app screen or prototype
- slide deck

Other scenarios can still use general task skills, but their clarification strategy should be added to `brief-elements` before relying on them heavily.
