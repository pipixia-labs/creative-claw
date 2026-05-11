---
name: poster-page-designer
description: Use for poster, HTML poster/card, campaign page, editorial single-page, landing page, and one-page visual design tasks that need copy/layout planning, visual assets from search or image generation, and final standalone HTML output.
---

# Poster Page Designer

Use this private product-design skill when the user wants a poster-like visual page, campaign page, HTML poster/card, editorial single page, or simple landing page.

Internally, write the content and visual plan into a Markdown draft first, resolve the needed assets, then hand a complete brief plus asset paths to `invoke_design_code_generation`.

## Best For

- HTML posters, HTML cards, editorial single pages, visual announcement pages, and simple landing pages.
- Tasks where copy, hierarchy, and illustration intent need to be explicit before generation.
- Workflows that need searched references, generated illustrations, uploaded assets, or a mix of these.
- Requests where final HTML quality depends on knowing every image path before code generation.

## When Not To Use

- The user only wants one standalone generated image.
- The user already supplied final copy, final assets, and a complete layout brief, and no draft step is useful.
- The task is a multi-screen product prototype or dashboard; use `design-canvas-artifact` unless the user specifically asks for a draft-first asset workflow.

## Workflow

1. Start with `emit_design_progress`.
2. Call `list_product_design_skills` and read this skill with `read_product_design_skill`.
3. Call `list_design_experts` before invoking private experts.
4. Normalize the user request into a concise production scope:
   - deliverable type: poster, HTML card, editorial page, or landing page
   - target size or aspect ratio
   - audience and message
   - required copy, CTA, brand notes, and constraints
   - assumptions that must remain visible
5. Create a Markdown draft before generating assets. Save it with `save_design_artifact` as an auxiliary `.md` artifact. Do not use `save_design_artifact` for the final HTML.
6. Resolve the asset manifest:
   - Use `SearchAgent` for reference images, factual/text context, or visual research.
   - Use `ImageGenerationAgent` for original final bitmap assets such as hero visuals, poster illustrations, product renders, or backgrounds.
   - Use `ImageUnderstandingAgent` when uploaded or searched reference images need style analysis, OCR, or reverse-prompt extraction.
   - Use `AnythingToMD` when user-provided documents or web pages need conversion into Markdown source material.
7. Update the code-generation handoff with every resolved asset path and intended usage.
8. Generate the final standalone HTML with `invoke_design_code_generation`.
9. Validate the HTML with `validate_design_artifact`.
10. Finish with `register_design_delivery`, normally returning the final HTML path. Include the Markdown draft path as a supporting file when useful.

## Markdown Draft Contract

The draft should be concrete enough that a code generation expert can build from it without inventing missing structure.

Use this structure:

```markdown
# Production Draft: <working title>

## Task Brief
- Deliverable:
- Target format:
- Audience:
- Primary message:
- Desired action:
- Confirmed constraints:
- Assumptions:

## Copy Draft
### Primary Headline

### Secondary Copy

### Supporting Details

### CTA / Footer

## Layout And Hierarchy
- Canvas:
- Reading path:
- Main focal point:
- Section/block order:
- Typography intent:
- Responsive behavior:

## Visual Direction
- Mood:
- Palette:
- Image style:
- Texture/material:
- Motion/interactivity if any:
- Things to avoid:

## Asset Manifest
| id | role | source strategy | query or prompt | target format | final usage | status | resolved path | notes |
|----|------|-----------------|-----------------|---------------|-------------|--------|---------------|-------|
| hero-visual | main illustration | generated_final | ... | 16:9 PNG | hero background | planned | | |
| reference-1 | visual reference | search_reference | ... | image result | style reference only | planned | | |

## Resolved Assets
- `asset-id`: `workspace/relative/path` - usage notes

## Code Generation Handoff
- Final file type:
- Required artboards/sections:
- Asset paths and alt text:
- Layout constraints:
- CSS/JS interaction constraints:
- Accessibility notes:
- Validation expectations:

## QA Checklist
- [ ] All final copy is present.
- [ ] Every final image asset has a workspace-relative path.
- [ ] Search assets are used only as references unless user explicitly allowed direct use.
- [ ] Final HTML can run locally without backend services.
- [ ] No local absolute paths.
```

## Asset Strategy Rules

- Default to `generated_final` for public-facing illustrations and poster visuals.
- For image generation, prefer Google's Nano Banana route by passing `provider="nano_banana"` to `ImageGenerationAgent` unless the user explicitly asks for another provider or a task-specific constraint requires it.
- Use `search_reference` to inform style, composition, examples, and factual context. Do not embed searched images directly in the final HTML unless the user explicitly permits direct use or provides license-safe sources.
- If search results are downloaded, record their workspace-relative paths in the draft and label them as references unless they are approved final assets.
- Keep image-generation prompts specific: subject, composition, style, lighting, color, aspect ratio, and what text should not be baked into the image.
- Prefer placing final text in HTML/CSS, not inside generated images, unless the user explicitly wants text rendered into the image.
- Generate only the assets needed for the first usable version. Add more variants later if the user asks.

## Expert Handoff Patterns

For visual reference search:

```json
{"query":"editorial conference poster bold typography abstract light installation reference","mode":"image","count":3}
```

For original image generation:

```json
{"prompt":["Abstract luminous paper sculpture forming a central poster hero visual, high contrast, clean negative space, no text"],"provider":"nano_banana","aspect_ratio":"16:9","resolution":"2K"}
```

For final HTML generation, the prompt to `invoke_design_code_generation` should include:

- The saved Markdown draft path.
- The final copy and layout hierarchy.
- The resolved asset table with workspace-relative paths.
- Whether searched assets are references or approved final assets.
- A clear requirement to create exactly one standalone HTML file.
- A clear requirement to use HTML/CSS text for copy and avoid local absolute paths.

## Quality Bar

- The Markdown draft is not optional; it is the alignment artifact for the workflow.
- The final HTML should feel designed for the requested medium, not like a generic SaaS landing page.
- Poster and editorial work should prioritize composition, typography, contrast, and message hierarchy.
- Landing pages should prioritize narrative, proof, conversion path, and responsive behavior.
- Keep implementation reviewable: named sections, readable CSS, stable ids, and clear asset references.
