---
name: guizang-ppt
description: Generate a single-file horizontal-swipe HTML deck with an editorial magazine and e-ink visual language. Includes WebGL fluid backgrounds, serif display titles, sans-serif body text, section-divider heroes, data-poster slides, image grids, and presentation-ready layouts. Use when the user asks for a magazine-style web PPT, horizontal swipe deck, editorial magazine deck, e-ink presentation, conference/share deck, product launch talk, or demo-day slides.
triggers:
  - "magazine"
  - "magazine style ppt"
  - "magazine web ppt"
  - "horizontal swipe deck"
  - "editorial magazine"
  - "e-ink presentation"
  - "web ppt"
  - "product launch deck"
  - "share deck"
  - "conference deck"
od:
  mode: deck
  scenario: presentation
  preview:
    type: html
    entry: index.html
  design_system:
    requires: true
    sections: [color, typography, layout, components]
  example_prompt: "Create a magazine-style PPT about 'One-person companies and AI-folded organizations' for a 25-minute talk to designers and founders. First recommend one direction from Monocle / WIRED / Kinfolk / Domus / Lab and let me choose."
---

# Guizang PPT Skill

Generate a single-file horizontal-swipe HTML deck. The visual language is a hybrid of editorial magazine, e-ink paper, restrained WebGL atmosphere, and presentation-grade information design. It is not a generic business deck and not a consumer app UI; it should feel like a *Monocle* feature translated into an interactive web presentation.

## When To Use

Use this skill for:

- Offline talks, industry briefings, salon-style sessions, or internal speeches.
- AI product launches, demo days, and opinionated product narratives.
- Highly personal talks where visual identity matters.
- Web-based slides that should work without a slide authoring tool.

Do not use this skill for:

- Dense tables or heavily layered analytical charts. Use a conventional PPT route instead.
- Training courseware where information density is the priority.
- Workflows that require multi-person editing in slide software.

## Workflow

### Step 0: Pick A Direction First

Before asking detailed clarification questions, ask the user to choose one of five magazine directions. Each direction packages theme colors, recommended layouts, chrome style, and slide-count guidance. Choosing a direction answers roughly half of the style questions up front.

Open `references/styles.md`, copy the one-line summaries for the five directions, and ask the user to choose:

1. Monocle Editorial - international magazine default.
2. WIRED Tech - data, engineering, benchmark, and technical launch topics.
3. Kinfolk Slow - reading, personal salons, cultural reflection, and human-centered topics.
4. Domus Architectural - design, architecture, portfolio, and spatial topics.
5. Lab / Reference - research, academic, process, and methodology topics.

If the user says they do not know, recommend Monocle Editorial because it has the lowest failure risk. If the user mentions AI, benchmarks, or technical launches, recommend WIRED. If the user mentions reading, private salons, or social sharing, recommend Kinfolk. If the user mentions design, architecture, or portfolio work, recommend Domus. If the user mentions research, academia, or methodology, recommend Lab.

After the direction is chosen, create or update a project notes file in the target project folder. The first line should record direction, theme colors, audience, and talk duration. Do not switch direction mid-build; switching invalidates earlier layout and color decisions.

### Step 1: Clarify Requirements Before Building

If the user already provided a complete outline and image assets, proceed to Step 2.

If the user only gave a topic or vague idea, ask these questions before writing slides:

| # | Question | Why It Matters |
|---|----------|----------------|
| 1 | Who is the audience, and what is the presentation setting? | Determines language, depth, and pacing. |
| 2 | How long is the talk? | 15 minutes is about 10 slides; 30 minutes is about 20 slides; 45 minutes is about 25-30 slides. |
| 3 | Are there source materials? | Source docs, data, old decks, or article links should drive the narrative. |
| 4 | Are there images, and where should they appear? | Image-heavy layouts need assets before visual validation. |
| 5 | Which theme direction should be used? | Usually answered in Step 0. |
| 6 | Are there hard constraints? | Required data, forbidden claims, or brand constraints prevent rework. |

If the user has no outline, use this narrative arc:

```text
Hook      -> 1 slide    : contrast, question, or hard data that stops the audience.
Context   -> 1-2 slides : background, speaker position, and why the topic matters.
Core      -> 3-5 slides : main argument, using Layout 4/5/6/9/10 as needed.
Shift     -> 1 slide    : break the expected frame or introduce the new viewpoint.
Takeaway  -> 1-2 slides : memorable line, open question, or action recommendation.
```

Align the narrative arc, slide count, and theme rhythm before building.

### Step 1.5: Image Asset Rules

Before implementation, tell the user these asset rules:

- Put images under `project/<name>/ppt/images/`, next to `index.html`.
- Name assets as `{slide-number}-{semantic-name}.{ext}`, for example `01-cover.jpg`, `03-figma.jpg`, or `05-dashboard.png`.
- Use zero-padded slide numbers for sorting.
- Keep semantic names short, English, and tied to slide meaning.
- Prefer images at least 1600px wide for large-screen clarity.
- Use JPG for photos/screenshots and PNG for transparent UI or charts.
- Keep total image weight under 10MB when possible.
- Replace assets by keeping the same filename. If names change, search and update `images/<old-name>` references.
- If there are no images yet, create structure with neutral placeholders and tell the user that image-heavy layouts cannot be visually verified until real assets exist.

### Step 2: Copy The Template

Copy `assets/template.html` to the target location, usually `project/<name>/ppt/index.html`, and create a sibling `images/` folder.

```bash
mkdir -p "project/<name>/ppt/images"
cp "<SKILL_ROOT>/assets/template.html" "project/<name>/ppt/index.html"
```

`template.html` is already runnable. CSS, WebGL shader code, page navigation JavaScript, fonts, and icon CDN links are preset. Only the `<main id="deck">` content should be replaced.

Immediately replace required placeholders. At minimum, update `<title>` from the template placeholder to the actual deck title. After copying, run a search for placeholder markers and remove them before delivery.

### Step 2.5: Select One Theme

This skill only allows the five curated theme presets. Do not accept arbitrary user-provided hex values. Bad color pairings degrade the whole deck; protecting the visual system is more important than offering unrestricted color freedom.

The five presets are:

| # | Theme | Best For |
|---|-------|----------|
| 1 | Ink Classic | General talks, business launches, and default use. |
| 2 | Indigo Porcelain | Technology, research, data, and technical launches. |
| 3 | Forest Ink | Nature, sustainability, culture, and nonfiction. |
| 4 | Kraft Paper | Nostalgia, humanities, literature, and indie magazine tone. |
| 5 | Dune | Art, design, creative work, and gallery-like decks. |

Implementation:

1. Recommend a preset based on topic, or ask the user to choose.
2. Open `references/themes.md` and find the matching `:root` block.
3. Replace the theme-variable lines in the copied template's initial `:root` block: `--ink`, `--ink-rgb`, `--paper`, `--paper-rgb`, `--paper-tint`, and `--ink-tint`.
4. Do not change the rest of the CSS unless a referenced class is missing.

Hard rules:

- Use exactly one theme per deck.
- Do not mix variables across themes.
- If the user provides arbitrary hex values, politely redirect them to the five presets.

### Step 3: Fill Content

#### Step 3.0: Preflight Template Classes

Before writing any slide code, read `assets/template.html` through the end of its `<style>` block. Compare the classes used in `references/layouts.md` against the classes available in the template.

If a required class is missing, add it to the template stylesheet once. Do not duplicate fixes inline on every slide. `template.html` is the source of truth for class names; avoid inventing new classes unless absolutely necessary.

Common class names that must exist include:

```text
h-hero / h-xl / h-sub / h-md / lead / kicker / meta-row / stat-card
stat-label / stat-nb / stat-unit / stat-note / pipeline-section
pipeline-label / pipeline / step / step-nb / step-title / step-desc
grid-2-7-5 / grid-2-6-6 / grid-2-8-4 / grid-3-3 / grid-6
grid-3 / grid-4 / frame / frame-img / img-cap / callout / callout-src
chrome / foot
```

#### Step 3.0.5: Plan Theme Rhythm

Before choosing layouts, list every slide's theme class in a note or draft. Use one of: `hero dark`, `hero light`, `dark`, or `light`.

Rules:

- Every slide `<section>` must include one of those theme classes.
- Never write only `hero`; specify `hero dark` or `hero light`.
- Three or more consecutive slides with the same theme is not allowed.
- Decks with 8 or more slides must include at least one `hero dark` and one `hero light`.
- The whole deck cannot be only light body slides; include dark body slides for rhythm.
- Insert a hero slide every 3-4 slides for covers, section dividers, questions, or major quotes.

After generation, run `grep 'class="slide' index.html` and manually check the rhythm.

#### Step 3.1: Choose Layouts

Do not write slides from scratch. Open `references/layouts.md`; it contains 10 paste-ready `<section>` skeletons:

| Layout | Purpose |
|--------|---------|
| 1. Opening cover | First slide. |
| 2. Section divider | Start of each chapter. |
| 3. Data poster | Hard data or a memorable metric. |
| 4. Quote + image | Identity contrast or story beat. |
| 5. Image grid | Multiple-image comparison or screenshot evidence. |
| 6. Two-column pipeline | Workflow or process. |
| 7. Suspense / question close | Chapter ending or final close. |
| 8. Big quote | Serif takeaway line. |
| 9. Before / after | Old mode versus new mode. |
| 10. Lead image + side text | Dense image and text slide. |

Pick the matching skeleton, paste it into the deck, and replace copy and image paths. Always complete the class preflight first.

#### Step 3.2: Image Ratio Rules

Always use standard ratios; do not copy unusual source-image ratios such as `2592/1798`.

| Use Case | Recommended Ratio |
|----------|-------------------|
| Quote + image main visual | 16:10 or 4:3 plus `max-height:56vh`. |
| Image grid | Fixed `height:26vh`; do not use `aspect-ratio`. |
| Small left image + right text | 1:1 or 3:2. |
| Full-screen hero visual | 16:9 plus `max-height:64vh`. |
| Lead-image side illustration | 3:2 or 3:4. |

Never use `align-self:end` for images. It can push images into the bottom of a cell and make them collide with browser chrome. Use a grid container with `align-items:start`. If the left column needs bottom alignment, use flex column and `justify-content:space-between`.

Component details for typography, color, grid, icons, callouts, stat cards, and pipelines live in `references/components.md`.

### Step 4: Run The Checklist

After generating the deck, open `references/checklist.md` and validate every item. The P0 items are mandatory.

Pay special attention to:

1. Hero titles must use serif type. If they render as sans-serif, the class preflight was probably skipped and `h-hero` is missing.
2. Image grids should use fixed `height:Nvh`, not `aspect-ratio`.
3. Images must not sink to the bottom of the page. Use grid plus `align-items:start`.
4. Images must use standard ratios: 16:10, 4:3, 3:2, 1:1, or 16:9.
5. Use Lucide icons instead of emoji.
6. Titles use serif, body uses sans-serif, metadata uses monospace.

### Step 5: Preview Locally

Open `index.html` directly in a browser. A local dev server is not required. Image paths should be relative, such as `images/cover.jpg`.

On macOS:

```bash
open "project/<name>/ppt/index.html"
```

### Step 6: Iterate

Apply user feedback in the copied template. The CSS is already parameterized; most adjustments should be inline values such as `font-size:Xvw`, `height:Yvh`, or `gap:Zvh`.

## Resource Guide

```text
guizang-ppt/
├── SKILL.md
├── assets/
│   ├── template.html
│   └── example-slides.html
└── references/
    ├── styles.md
    ├── components.md
    ├── layouts.md
    ├── themes.md
    └── checklist.md
```

Recommended load order:

1. Read this `SKILL.md`.
2. For Step 0, read `references/styles.md`.
3. After clarification, read `references/themes.md` if theme details need confirmation.
4. Before writing slides, read the `<style>` block in `assets/template.html`.
5. Read `references/layouts.md` to choose skeletons.
6. Read `references/components.md` for detailed component tuning.
7. After generation, read `references/checklist.md` for QA.

## Design Principles

1. Restraint beats spectacle. WebGL should show mainly on hero slides; normal slides should stay quiet.
2. Structure beats decoration. Avoid shadows, floating cards, and padding-box aesthetics. Use large type, type contrast, grids, and whitespace.
3. Hierarchy comes from both size and typeface. Largest serif is the main title; medium serif is the subtitle; large sans is the lead; small sans is body; monospace is metadata.
4. Images are first-class content. Crop only the bottom when needed; preserve top and side context. Use `height:Nvh` for grids and avoid fragile aspect-ratio hacks.
5. Rhythm comes from hero slides. Alternate hero and non-hero slides so the deck has breathing room.
6. Terminology stays consistent. Use "Skills" as-is; do not mix translated labels into the skill docs.

## Style References

Use these as style anchors:

- Guizang's "One-person company: AI-folded organizations" talk from 2026-04-22.
- *Monocle* magazine layout language.
- Garry Tan's "Thin Harness, Fat Skills" demo/blog framing.
