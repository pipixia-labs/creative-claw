# Design Knowledge Resource Index

This index summarizes the resources exposed by `design-knowledge-and-skills`. Use `resource-manifest.json` as the machine-readable source of truth.

## Counts

- `brief_element_schema`: 38
- `contract_schema`: 2
- `design_system`: 72
- `device_frame`: 5
- `index_skill`: 1
- `task_skill`: 35

## Selection Order

1. Identify the user task scenario and surface.
2. Read the matching `brief_element_schema` first and use it to detect missing design elements.
3. Ask only the highest-impact missing questions, or use schema defaults when the user asks to proceed directly.
4. Select one primary task skill.
5. Select at most one primary design system.
6. Exclude resources marked `runtimeEnabled: false` or `referenceOnly: true` from execution context.
7. Read only the selected resource files before building the design brief.

## Contract Schemas

- `schema.design_brief_v1`: Creative Claw Design Brief v1 -> `schemas/design-brief-v1.schema.json`
- `schema.design_product_result_v1`: Creative Claw Design Product Result v1 -> `schemas/design-product-result-v1.schema.json`

## Brief Elements

- `brief_elements.admin_console`: Admin Console -> `brief-elements/admin-console.json`
- `brief_elements.audio_jingle`: Audio Jingle -> `brief-elements/audio-jingle.json`
- `brief_elements.blog_post`: Blog Post -> `brief-elements/blog-post.json`
- `brief_elements.critique`: Design Critique -> `brief-elements/critique.json`
- `brief_elements.dashboard`: Dashboard / Analytics Console -> `brief-elements/dashboard.json`
- `brief_elements.dating_web`: Dating Web -> `brief-elements/dating-web.json`
- `brief_elements.deck`: Slide Deck -> `brief-elements/deck.json`
- `brief_elements.digital_eguide`: Digital E-guide -> `brief-elements/digital-eguide.json`
- `brief_elements.docs_page`: Documentation Page -> `brief-elements/docs-page.json`
- `brief_elements.email_marketing`: Email Marketing -> `brief-elements/email-marketing.json`
- `brief_elements.eng_runbook`: Engineering Runbook -> `brief-elements/eng-runbook.json`
- `brief_elements.finance_report`: Finance Report -> `brief-elements/finance-report.json`
- `brief_elements.gamified_app`: Gamified App -> `brief-elements/gamified-app.json`
- `brief_elements.guizang_ppt`: Magazine Web PPT -> `brief-elements/guizang-ppt.json`
- `brief_elements.hr_onboarding`: HR Onboarding -> `brief-elements/hr-onboarding.json`
- `brief_elements.html_deck`: HTML Deck -> `brief-elements/html-deck.json`
- `brief_elements.hyperframes`: Hyperframes -> `brief-elements/hyperframes.json`
- `brief_elements.image_poster`: Image Poster -> `brief-elements/image-poster.json`
- `brief_elements.invoice`: Invoice -> `brief-elements/invoice.json`
- `brief_elements.kanban_board`: Kanban Board -> `brief-elements/kanban-board.json`
- `brief_elements.landing_page`: SaaS / Marketing Landing Page -> `brief-elements/landing-page.json`
- `brief_elements.magazine_poster`: Magazine Poster -> `brief-elements/magazine-poster.json`
- `brief_elements.marketing_campaign_page`: Marketing Campaign Page -> `brief-elements/marketing-campaign-page.json`
- `brief_elements.meeting_notes`: Meeting Notes -> `brief-elements/meeting-notes.json`
- `brief_elements.mobile_app`: Mobile App Screen / Prototype -> `brief-elements/mobile-app.json`
- `brief_elements.mobile_onboarding`: Mobile Onboarding -> `brief-elements/mobile-onboarding.json`
- `brief_elements.motion_frames`: Motion Frames -> `brief-elements/motion-frames.json`
- `brief_elements.operation_data_ui`: Operation Data UI -> `brief-elements/operation-data-ui.json`
- `brief_elements.pm_spec`: PM Spec -> `brief-elements/pm-spec.json`
- `brief_elements.pricing_page`: Pricing Page -> `brief-elements/pricing-page.json`
- `brief_elements.replit_deck`: Replit Deck -> `brief-elements/replit-deck.json`
- `brief_elements.social_carousel`: Social Carousel -> `brief-elements/social-carousel.json`
- `brief_elements.sprite_animation`: Sprite Animation -> `brief-elements/sprite-animation.json`
- `brief_elements.team_okrs`: Team OKRs -> `brief-elements/team-okrs.json`
- `brief_elements.tweaks`: Tweaks -> `brief-elements/tweaks.json`
- `brief_elements.video_shortform`: Shortform Video -> `brief-elements/video-shortform.json`
- `brief_elements.weekly_update`: Weekly Update -> `brief-elements/weekly-update.json`
- `brief_elements.wireframe_sketch`: Wireframe Sketch -> `brief-elements/wireframe-sketch.json`

## Task Skills

- `task_skill.audio-jingle`: audio-jingle -> `skills/audio-jingle/SKILL.md`
- `task_skill.blog-post`: blog-post -> `skills/blog-post/SKILL.md`
- `task_skill.critique`: critique -> `skills/critique/SKILL.md`
- `task_skill.dashboard`: dashboard -> `skills/dashboard/SKILL.md`
- `task_skill.dating-web`: dating-web -> `skills/dating-web/SKILL.md`
- `task_skill.digital-eguide`: digital-eguide -> `skills/digital-eguide/SKILL.md`
- `task_skill.docs-page`: docs-page -> `skills/docs-page/SKILL.md`
- `task_skill.email-marketing`: email-marketing -> `skills/email-marketing/SKILL.md`
- `task_skill.eng-runbook`: eng-runbook -> `skills/eng-runbook/SKILL.md`
- `task_skill.finance-report`: finance-report -> `skills/finance-report/SKILL.md`
- `task_skill.gamified-app`: gamified-app -> `skills/gamified-app/SKILL.md`
- `task_skill.guizang-ppt`: magazine-web-ppt -> `skills/guizang-ppt/SKILL.md`
- `task_skill.hr-onboarding`: hr-onboarding -> `skills/hr-onboarding/SKILL.md`
- `task_skill.hyperframes`: hyperframes -> `skills/hyperframes/SKILL.md`
- `task_skill.image-poster`: image-poster -> `skills/image-poster/SKILL.md`
- `task_skill.invoice`: invoice -> `skills/invoice/SKILL.md`
- `task_skill.kanban-board`: kanban-board -> `skills/kanban-board/SKILL.md`
- `task_skill.magazine-poster`: magazine-poster -> `skills/magazine-poster/SKILL.md`
- `task_skill.meeting-notes`: meeting-notes -> `skills/meeting-notes/SKILL.md`
- `task_skill.mobile-app`: mobile-app -> `skills/mobile-app/SKILL.md`
- `task_skill.mobile-onboarding`: mobile-onboarding -> `skills/mobile-onboarding/SKILL.md`
- `task_skill.motion-frames`: motion-frames -> `skills/motion-frames/SKILL.md`
- `task_skill.pm-spec`: pm-spec -> `skills/pm-spec/SKILL.md`
- `task_skill.pricing-page`: pricing-page -> `skills/pricing-page/SKILL.md`
- `task_skill.replit-deck`: replit-deck -> `skills/replit-deck/SKILL.md`
- `task_skill.saas-landing`: saas-landing -> `skills/saas-landing/SKILL.md`
- `task_skill.simple-deck`: simple-deck -> `skills/simple-deck/SKILL.md`
- `task_skill.social-carousel`: social-carousel -> `skills/social-carousel/SKILL.md`
- `task_skill.sprite-animation`: sprite-animation -> `skills/sprite-animation/SKILL.md`
- `task_skill.team-okrs`: team-okrs -> `skills/team-okrs/SKILL.md`
- `task_skill.tweaks`: tweaks -> `skills/tweaks/SKILL.md`
- `task_skill.video-shortform`: video-shortform -> `skills/video-shortform/SKILL.md`
- `task_skill.web-prototype`: web-prototype -> `skills/web-prototype/SKILL.md`
- `task_skill.weekly-update`: weekly-update -> `skills/weekly-update/SKILL.md`
- `task_skill.wireframe-sketch`: wireframe-sketch -> `skills/wireframe-sketch/SKILL.md`

## Design Systems

### AI & LLM

- `design_system.claude`: Design System Inspired by Claude (Anthropic) -> `design-systems/claude/DESIGN.md`
- `design_system.cohere`: Design System Inspired by Cohere -> `design-systems/cohere/DESIGN.md`
- `design_system.elevenlabs`: Design System Inspired by ElevenLabs -> `design-systems/elevenlabs/DESIGN.md`
- `design_system.minimax`: Design System Inspired by MiniMax -> `design-systems/minimax/DESIGN.md`
- `design_system.mistral-ai`: Design System Inspired by Mistral AI -> `design-systems/mistral-ai/DESIGN.md`
- `design_system.ollama`: Design System Inspired by Ollama -> `design-systems/ollama/DESIGN.md`
- `design_system.opencode-ai`: Design System Inspired by OpenCode -> `design-systems/opencode-ai/DESIGN.md`
- `design_system.replicate`: Design System Inspired by Replicate -> `design-systems/replicate/DESIGN.md`
- `design_system.runwayml`: Design System Inspired by Runway -> `design-systems/runwayml/DESIGN.md`
- `design_system.together-ai`: Design System Inspired by Together AI -> `design-systems/together-ai/DESIGN.md`
- `design_system.voltagent`: Design System Inspired by VoltAgent -> `design-systems/voltagent/DESIGN.md`
- `design_system.x-ai`: Design System Inspired by xAI -> `design-systems/x-ai/DESIGN.md`

### Automotive

- `design_system.bmw`: Design System Inspired by BMW -> `design-systems/bmw/DESIGN.md`
- `design_system.bugatti`: Design System Inspired by Bugatti -> `design-systems/bugatti/DESIGN.md`
- `design_system.ferrari`: Design System Inspired by Ferrari -> `design-systems/ferrari/DESIGN.md`
- `design_system.lamborghini`: Design System Inspired by Lamborghini -> `design-systems/lamborghini/DESIGN.md`
- `design_system.renault`: Design System Inspired by Renault -> `design-systems/renault/DESIGN.md`
- `design_system.tesla`: Design System Inspired by Tesla -> `design-systems/tesla/DESIGN.md`

### Backend & Data

- `design_system.clickhouse`: Design System Inspired by ClickHouse -> `design-systems/clickhouse/DESIGN.md`
- `design_system.composio`: Design System Inspired by Composio -> `design-systems/composio/DESIGN.md`
- `design_system.hashicorp`: Design System Inspired by HashiCorp -> `design-systems/hashicorp/DESIGN.md`
- `design_system.mongodb`: Design System Inspired by MongoDB -> `design-systems/mongodb/DESIGN.md`
- `design_system.posthog`: Design System Inspired by PostHog -> `design-systems/posthog/DESIGN.md`
- `design_system.sanity`: Design System Inspired by Sanity -> `design-systems/sanity/DESIGN.md`
- `design_system.sentry`: Design System Inspired by Sentry -> `design-systems/sentry/DESIGN.md`
- `design_system.supabase`: Design System Inspired by Supabase -> `design-systems/supabase/DESIGN.md`

### Design & Creative

- `design_system.airtable`: Design System Inspired by Airtable -> `design-systems/airtable/DESIGN.md`
- `design_system.clay`: Design System Inspired by Clay -> `design-systems/clay/DESIGN.md`
- `design_system.figma`: Design System Inspired by Figma -> `design-systems/figma/DESIGN.md`
- `design_system.framer`: Design System Inspired by Framer -> `design-systems/framer/DESIGN.md`
- `design_system.miro`: Design System Inspired by Miro -> `design-systems/miro/DESIGN.md`
- `design_system.webflow`: Design System Inspired by Webflow -> `design-systems/webflow/DESIGN.md`

### Developer Tools

- `design_system.cursor`: Design System Inspired by Cursor -> `design-systems/cursor/DESIGN.md`
- `design_system.expo`: Design System Inspired by Expo -> `design-systems/expo/DESIGN.md`
- `design_system.lovable`: Design System Inspired by Lovable -> `design-systems/lovable/DESIGN.md`
- `design_system.raycast`: Design System Inspired by Raycast -> `design-systems/raycast/DESIGN.md`
- `design_system.superhuman`: Design System Inspired by Superhuman -> `design-systems/superhuman/DESIGN.md`
- `design_system.vercel`: Design System Inspired by Vercel -> `design-systems/vercel/DESIGN.md`
- `design_system.warp`: Design System Inspired by Warp -> `design-systems/warp/DESIGN.md`

### E-Commerce & Retail

- `design_system.airbnb`: Design System Inspired by Airbnb -> `design-systems/airbnb/DESIGN.md`
- `design_system.meta`: Design System Inspired by Meta (Store) -> `design-systems/meta/DESIGN.md`
- `design_system.nike`: Design System Inspired by Nike -> `design-systems/nike/DESIGN.md`
- `design_system.shopify`: Design System Inspired by Shopify -> `design-systems/shopify/DESIGN.md`
- `design_system.starbucks`: Design System Inspired by Starbucks -> `design-systems/starbucks/DESIGN.md`

### Fintech & Crypto

- `design_system.binance`: Design System Inspired by Binance.US -> `design-systems/binance/DESIGN.md`
- `design_system.coinbase`: Design System Inspired by Coinbase -> `design-systems/coinbase/DESIGN.md`
- `design_system.kraken`: Design System Inspired by Kraken -> `design-systems/kraken/DESIGN.md`
- `design_system.mastercard`: Design System Inspired by Mastercard -> `design-systems/mastercard/DESIGN.md`
- `design_system.revolut`: Design System Inspired by Revolut -> `design-systems/revolut/DESIGN.md`
- `design_system.stripe`: Design System Inspired by Stripe -> `design-systems/stripe/DESIGN.md`
- `design_system.wise`: Design System Inspired by Wise -> `design-systems/wise/DESIGN.md`

### Media & Consumer

- `design_system.apple`: Design System Inspired by Apple -> `design-systems/apple/DESIGN.md`
- `design_system.ibm`: Design System Inspired by IBM -> `design-systems/ibm/DESIGN.md`
- `design_system.nvidia`: Design System Inspired by NVIDIA -> `design-systems/nvidia/DESIGN.md`
- `design_system.pinterest`: Design System Inspired by Pinterest -> `design-systems/pinterest/DESIGN.md`
- `design_system.playstation`: Design System Inspired by PlayStation -> `design-systems/playstation/DESIGN.md`
- `design_system.spacex`: Design System Inspired by SpaceX -> `design-systems/spacex/DESIGN.md`
- `design_system.spotify`: Design System Inspired by Spotify -> `design-systems/spotify/DESIGN.md`
- `design_system.theverge`: Design System Inspired by The Verge -> `design-systems/theverge/DESIGN.md`
- `design_system.uber`: Design System Inspired by Uber -> `design-systems/uber/DESIGN.md`
- `design_system.vodafone`: Design System Inspired by Vodafone -> `design-systems/vodafone/DESIGN.md`
- `design_system.wired`: Design System Inspired by WIRED -> `design-systems/wired/DESIGN.md`
- `design_system.xiaohongshu`: Design System Inspired by Xiaohongshu -> `design-systems/xiaohongshu/DESIGN.md`

### Productivity & SaaS

- `design_system.cal`: Design System Inspired by Cal.com -> `design-systems/cal/DESIGN.md`
- `design_system.intercom`: Design System Inspired by Intercom -> `design-systems/intercom/DESIGN.md`
- `design_system.linear-app`: Design System Inspired by Linear -> `design-systems/linear-app/DESIGN.md`
- `design_system.mintlify`: Design System Inspired by Mintlify -> `design-systems/mintlify/DESIGN.md`
- `design_system.notion`: Design System Inspired by Notion -> `design-systems/notion/DESIGN.md`
- `design_system.resend`: Design System Inspired by Resend -> `design-systems/resend/DESIGN.md`
- `design_system.zapier`: Design System Inspired by Zapier -> `design-systems/zapier/DESIGN.md`

### Starter

- `design_system.default`: Neutral Modern -> `design-systems/default/DESIGN.md`
- `design_system.warm-editorial`: Warm Editorial -> `design-systems/warm-editorial/DESIGN.md`

## Device Frames

- `device_frame.android-pixel`: Android Pixel -> `assets/frames/android-pixel.html`
- `device_frame.browser-chrome`: Browser Chrome -> `assets/frames/browser-chrome.html`
- `device_frame.ipad-pro`: Ipad Pro -> `assets/frames/ipad-pro.html`
- `device_frame.iphone-15-pro`: Iphone 15 Pro -> `assets/frames/iphone-15-pro.html`
- `device_frame.macbook`: Macbook -> `assets/frames/macbook.html`

## Notes

- Prompt templates are documented in design-v3 reference docs but are not runtime-enabled in this slice.
- Visual directions should be added as first-class resources in a later slice if they are needed at runtime.
- Source and license fields are intentionally explicit so future ingestion can filter runtime-enabled resources safely.
