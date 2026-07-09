<div align="center">
  <img src="asset/logo-2.png" alt="CreativeClaw" width="420">
  <h1>CreativeClaw: your personal creative assistant</h1>
  <h3>One conversation. Endless creativity.</h3>
  <p><a href="README_zh.md">中文</a> · <strong>English</strong></p>
  <p>
    <img src="https://img.shields.io/badge/python-3.13%20recommended-blue" alt="Python">
    <img src="https://img.shields.io/badge/google--adk-2.1.0-green" alt="Google ADK">
    <img src="https://img.shields.io/badge/channels-CLI%20%7C%20Web%20%7C%20Telegram%20%7C%20Feishu-orange" alt="Channels">
  </p>
</div>

<p align="center">
  <a href="https://youtu.be/H8DIIPYhO7w"><strong>Watch the CreativeClaw demo on YouTube</strong></a>
</p>

CreativeClaw is a creative workflow system powered by multiple autonomous agents, turning the creative process from tool switching into continuous conversation.

It comes with multiple built-in agents that provide reliable creative capabilities and intelligently invoke different tools through the Skill mechanism. With conversation alone, you can complete an end-to-end creative workflow covering image and video generation, image understanding, content refinement, and information search.

No more jumping back and forth between different tools.
With CreativeClaw, you can keep iterating around a single idea and move from inspiration to final output in one flow.

## 📰 News
 - 2026-06-02: Released v0.5 with Google ADK 2.1 support, provider-aware structured final responses, ADK-native PPT HITL bridging, schema-hardened Product/Expert protocols, OpenAI Codex OAuth model support, and more reliable Web Design/Page/PPT delivery.
 - 2026-05-22: Released v0.4 with Web visual workspace upgrades, PPT/Page/Design product workflows, broader LLM compatibility, improved video previews, grouped Activity cards, and richer runtime trace debugging.
 - 2026-05-10: Added requirement confirmation and design system support for Web design tasks.
 - 2026-05-09: Upgraded the Design and Web Chat workflow with a tldraw-powered Visual Board, canvas-style Design previews, and path-based visual attachments.
 - 2026-05-05: Added interactive PPT generation and HTML-to-PPTX workflows, plus improved Web Chat previews for artifacts, media, PDF, and PPT outputs.
 - 2026-05-02: Released v0.3.0 with DeepSeek V4, Seedance 2.0, Seed TTS 2.0 multi-voice support, DashScope media models, and expanded 3D providers.
 - 2026-05-01: Added design product flow and CodeGenerationExpert routing.
 - 2026-04-24: Released v0.2.0 with expert runtime refactor, expert cards, and deduplicated basic media agents.
 - 2026-04-21: Added Kling video generation, including text-to-video, image-to-video, and multi-reference workflows.
 - 2026-04-14: Added HY 3D and expanded the built-in media expert set.
 - 2026-04-13: Expanded LLM provider coverage and added image segmentation.
 - 2026-04-12: Released v0.1.1 with core image/video workflows across Web, CLI, and Feishu.


## ✨ Key Features of CreativeClaw

- **Built for creative workflows**: image generation, image editing, image understanding, prompt extraction, grounding, search, and video generation are first-class capabilities.
- **Supports multiple models and providers**: image and video flows can use different providers so you can balance quality, speed, and cost.
- **Iterative through conversation**: send a reference image for analysis, then keep asking follow-up questions, editing, and refining prompts.
- **Web Chat visual workspace**: inspect generated outputs in the Design preview, annotate image artifacts on the Visual Board, and send selected canvas regions back into the conversation as workspace file attachments.
- **Design brief and design systems**: Web design tasks can start with a compact requirement form, recommend relevant design systems from the bundled catalog, preview them in light/dark modes, and carry the selected system into generation.
- **Extensible by design**: skills let you add specialized workflows such as MiniMax CLI.
- **Coding-based asset processing**: besides generating content directly, it can also help process assets in batches through OpenCV / Python scripts.
- **Deterministic media operations**: supports local image, video, and audio inspection and transformation through `ImageBasicOperations`, `VideoBasicOperations`, and `AudioBasicOperations`.
  See [docs/media_basic_operations.md](docs/media_basic_operations.md) for a quick reference.

## 🏗️ Architecture

The following diagram shows the high-level architecture of CreativeClaw, including the orchestrator, expert agents, skills, and channel integrations.

![CreativeClaw architecture](asset/framework.png)

## 🤖 Supported Models

### 🧠 LLM

-  `openai`, `openai_codex` (`openai-codex` config alias), `anthropic`, `gemini`, `openrouter`, `deepseek`, `groq`, `zhipu`, `dashscope`, `vllm`, `ollama`, `moonshot`, `minimax`, `mistral`, `stepfun`, `siliconflow`, `volcengine`, `byteplus`, `qianfan`, `azure_openai`, `custom`
- `openai_codex` / `openai-codex` uses ChatGPT/Codex OAuth instead of `OPENAI_API_KEY`. Run `creative-claw provider login openai-codex` first, then set `llm.provider` to `openai-codex` or `openai_codex`, and set `llm.model` to `gpt-5.5`.
- DeepSeek V4 is available through the `deepseek` provider with `deepseek-v4-pro` or `deepseek-v4-flash`. The runtime keeps the ADK `LiteLlm` path and sends requests as `deepseek/<model>` with `api_base` set to `https://api.deepseek.com` by default.

### 🖼️ Image Generation

- Nano Banana Pro (`gemini-3.1-flash-image-preview`)
- Seedream 5.0 (`doubao-seedream-5-0-260128`)
- GPT Image 2 (`gpt-image-2`)
- DashScope image models (`wan2.7-image-pro`, `qwen-image-2.0-pro`, `z-image-turbo`)

### 🎬 Video Generation

- Seedance 2.0 (`doubao-seedance-2-0-260128`, default) and Seedance 2.0 fast (`doubao-seedance-2-0-fast-260128`)
- Seedance 1.0 Pro (`doubao-seedance-1-0-pro-250528`, legacy-compatible)
- Veo 3.1 (`veo-3.1-generate-preview`)
- Kling 3 (`kling-v3`; `multi_reference` currently uses `kling-v1-6`)
- DashScope Wan 2.7 (`wan2.7-t2v`, `wan2.7-t2v-2026-04-25`, `wan2.7-i2v`, `wan2.7-i2v-2026-04-25`)
- DashScope HappyHorse 1.0 (`happyhorse-1.0-t2v`, `happyhorse-1.0-i2v`)

### 📦 3D Generation

- Hunyuan 3D Pro (`hy3d`, default provider, model `3.0` / `3.1`)
- Seed3D (`seed3d`, model `doubao-seed3d-2-0-260328`)
- Hyper3D (`hyper3d`, model `hyper3d-gen2-260112`)
- Hitem3D (`hitem3d`, model `hitem3d-2-0-251223`)

### 🔊 Speech Synthesis

- ByteDance / Volcengine streaming TTS (`seed-tts-2.0` by default, with validated Seed TTS 2.0 voice selection)

### 🎵 Music Generation

- MiniMax Music Generation API (`music-2.5`)

### 🎤 Speech Recognition
 - Volcengine BigASR Flash (`volc.bigasr.auc_turbo`)
 - Volcengine subtitle generation and alignment (`vc.async.default`, `volc.ata.default`)


## 🚀 Quick Start

### 1. Set up the environment

```bash
git clone https://github.com/GML-FMGroup/creative_claw.git
cd creative_claw
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Python 3.13 is the recommended runtime. Python 3.12 is also supported, but Python 3.14 is not recommended yet because the current Volcengine Ark SDK path used by Seedance / Seedream depends on Pydantic v1 compatibility code that is not stable on Python 3.14.

If you want deterministic local video or audio operations, also make sure `ffmpeg` and `ffprobe` are installed and available on `PATH`.
For operation parameters and example payloads, see [docs/media_basic_operations.md](docs/media_basic_operations.md).

### 2. Initialize the runtime directory

```bash
creative-claw init
```

This creates:

- `~/.creative-claw/conf.json`
- `~/.creative-claw/workspace/`

### 3. Add the minimum required API key

The minimum working config looks like this:

```json
{
  "workspace": "~/.creative-claw/workspace",
  "llm": {
    "provider": "openai",
    "model": "gpt-5.4"
  },
  "providers": {
    "openai": {
      "api_key": "your_api_key_here"
    }
  }
}
```

Notes:

- This is enough to try the default CLI chat flow.
- To use a Codex OAuth account instead of an OpenAI API key, run `creative-claw provider login openai-codex` first, then set `llm.provider` to `openai-codex` or `openai_codex`, and set `llm.model` to `gpt-5.5`; this route does not need `providers.openai.api_key`.
- Image, video, search, and some provider-specific capabilities only need extra credentials when you actually use them.
- `VideoGenerationAgent` provider `seedance` now defaults to `doubao-seedance-2-0-260128`. For faster generation use `model_name="doubao-seedance-2-0-fast-260128"` and keep `resolution` at `720p`; legacy `model_name="doubao-seedance-1-0-pro-250528"` remains accepted.
- For exact dialogue or native generated audio with Seedance 2.0, use `provider="seedance"`, `generate_audio=true`, and `prompt_rewrite="off"` so quoted dialogue is preserved.
- Seedance / Volcengine Ark calls should run on Python 3.13 for now. Python 3.14 can trigger SDK compatibility warnings or failures from the Ark SDK's Pydantic v1 compatibility layer.
- For `VideoGenerationAgent` with `provider="kling"`, prompt and image-guided routes now default to `kling-v3`, while `mode="multi_reference"` follows the official `kling-v1-6` schema.
- If `services.kling_api_base` or `KLING_API_BASE` is not set explicitly, the built-in Kling provider probes the official Beijing and Singapore gateways and caches the first working base.
- Kling image-guided routes validate the documented input constraints but do not auto-resize or auto-crop input images. If preprocessing is needed, do it first with local image tools before calling `VideoGenerationAgent`.
- DashScope media providers use `providers.dashscope.api_key` or `DASHSCOPE_API_KEY`. `VideoGenerationAgent` supports DashScope text-to-video, first-frame image-to-video, and first/last-frame image-to-video only; video editing and reference-video routes are intentionally not exposed yet.
- `ImageGenerationAgent` provider `dashscope` supports `model_name="wan2.7-image-pro"`, `model_name="qwen-image-2.0-pro"`, and `model_name="z-image-turbo"`.
- `3DGeneration` defaults to Tencent Cloud `hy3d` and also supports Volcengine Ark providers `seed3d`, `hyper3d`, and `hitem3d`. `hy3d` uses `services.tencentcloud_*`, while the Volcengine 3D providers use `services.ark_api_key` or `ARK_API_KEY`.
- `seed3d` is image-to-3D only with exactly one image source, `hyper3d` supports prompt-only text-to-3D or 1-5 reference images, and `hitem3d` requires 1-4 externally accessible image URLs.
- `SpeechSynthesisExpert` uses Volcengine streaming TTS with `seed-tts-2.0` by default. Users or the orchestrator may select Seed TTS 2.0 voices with `speaker`, `voice_type`, or `voice_name`; the default voice is Vivi 2.0 (`zh_female_vv_uranus_bigtts`).
- `SpeechRecognitionExpert` uses Volcengine speech services. Besides `VOLCENGINE_APPID` and `VOLCENGINE_ACCESS_TOKEN`, the current backend also needs these resource grants: `volc.bigasr.auc_turbo` for `task=asr`, `vc.async.default` for subtitle generation, and `volc.ata.default` for subtitle timing when `subtitle_text` / `audio_text` is provided. The activation entry is the [Volcengine speech console](https://console.volcengine.com/speech/app). Missing grants usually surface as `requested resource not granted` or `requested grant not found`.
- Resolution order is: repository-local `.env` is loaded first without overriding shell variables; `conf.json` remains primary; if an API key is empty in `conf.json`, runtime falls back to the matching environment variable.
- The first-round text LLM providers include `openai`, `openai_codex`, `anthropic`, `gemini`, `openrouter`, `deepseek`, `groq`, `zhipu`, `dashscope`, `vllm`, `ollama`, `moonshot`, `minimax`, `mistral`, `stepfun`, `siliconflow`, `volcengine`, `byteplus`, `qianfan`, `azure_openai`, and `custom`.
- To use DeepSeek V4 Pro or Flash, set `llm.provider` to `deepseek`, set `llm.model` to `deepseek-v4-pro` or `deepseek-v4-flash`, and provide `providers.deepseek.api_key` or `DEEPSEEK_API_KEY`.
- For the full environment and credential matrix, the reference full template, and common field descriptions, see [docs/development.md](docs/development.md).

### 3. Start chatting

Web Chat is recommended for most creative workflows because it has optimized progress and artifact previews:

```bash
creative-claw chat web
```

If you have not installed the console script yet, use the module entrypoint:

```bash
python -m src.creative_claw_cli chat web
```

If you already ran `pip install -e .`, you can also use CLI Chat directly:

```bash
creative-claw chat cli
```

You can also send a single request directly:

```bash
creative-claw chat cli --message "Generate a poster-style cat image"
```

Design tasks also use the normal chat entrypoint:

```bash
creative-claw chat cli --message "Design an operations dashboard for DAU, conversion, retention, and channel ROI"
```

Ask with an image attachment:

```bash
creative-claw chat cli \
  --message "Describe this image and write a better prompt for recreating it" \
  --attachment ./example.png
```

## 💡 Common Usage

### Generate an image

```bash
creative-claw chat cli --message "Create a cinematic travel poster for Hangzhou in spring"
```

### Improve a prompt from a reference image

```bash
creative-claw chat cli \
  --message "Look at this reference image and write a cleaner generation prompt" \
  --attachment ./reference.png
```

### Understand an image before deciding how to edit it

```bash
creative-claw chat cli \
  --message "Describe this image, identify the subject, and suggest three editing directions" \
  --attachment ./input.png
```

### Start a new session

Inside the chat, use:

- `/help`
- `/new`

## 🧰 Built-in Tools and Expert Tools

The main LLM orchestrator can call these tool groups directly:

- **Workspace file tools**: `list_dir`, `glob`, `grep`, `read_file`, `write_file`, `edit_file`.
- **Deterministic media tools**: `image_crop`, `image_rotate`, `image_flip`, `image_info`, `image_resize`, `image_convert`, `video_info`, `video_extract_frame`, `video_trim`, `video_concat`, `video_convert`, `audio_info`, `audio_trim`, `audio_concat`, `audio_convert`.
- **Runtime and web tools**: `exec_command`, `process_session`, `web_search`, `web_fetch`, `list_session_files`.
- **Product-line tools**: `run_ppt_product` handles PPTX / PowerPoint delivery, while `run_design_product` handles HTML, UI, web, and design prototype outputs when the final deliverable is not PPTX.
- **Expert dispatch**: `invoke_agent` routes structured requests to expert agents such as `ImageGenerationAgent`, `ImageEditingAgent`, `ImageUnderstandingAgent`, `VideoGenerationAgent`, `SpeechRecognitionExpert`, `SpeechSynthesisExpert`, `MusicGenerationExpert`, and `3DGeneration`.

`VideoGenerationAgent` currently exposes these provider-aware tool parameters:

- Common controls: `provider`, `mode`, `prompt_rewrite`, `aspect_ratio`, `resolution`, `duration_seconds`, `negative_prompt`, `seed`, and optional `input_path` / `input_paths`.
- `seedance`: modes `prompt`, `first_frame`, `first_frame_and_last_frame`, `reference_asset`, `reference_style`; model ids `doubao-seedance-2-0-260128`, `doubao-seedance-2-0-fast-260128`, `doubao-seedance-1-0-pro-250528`; extra controls `generate_audio` and `watermark`.
- Seedance uses Volcengine Ark and is currently validated with Python 3.13. Avoid Python 3.14 for this path until the Ark SDK fully supports it.
- `veo`: modes `prompt`, `first_frame`, `first_frame_and_last_frame`, `reference_asset`, `reference_style`, `video_extension`; model id `veo-3.1-generate-preview`; extra control `person_generation`.
- `kling`: modes `prompt`, `first_frame`, `first_frame_and_last_frame`, `multi_reference`; model ids `kling-v3` and `kling-v1-6` for `multi_reference`; extra control `kling_mode` (`std` or `pro`).
- `dashscope`: modes `prompt`, `first_frame`, `first_frame_and_last_frame`; text-to-video model ids `wan2.7-t2v`, `wan2.7-t2v-2026-04-25`, `happyhorse-1.0-t2v`; image-to-video model ids `wan2.7-i2v`, `wan2.7-i2v-2026-04-25`, `happyhorse-1.0-i2v`. HappyHorse image-to-video requires `image_url` / `image_urls`; Wan 2.7 image-guided routes can use local `input_path` / `input_paths`.

`ImageGenerationAgent` currently exposes these provider-aware tool parameters:

- Common controls: `provider`, `prompt`, `aspect_ratio`, `resolution`, `size`, `model_name`, `negative_prompt`, `prompt_extend`, `watermark`, and `thinking_mode`.
- `nano_banana`: default Gemini-backed text-to-image provider; supports `aspect_ratio` and `resolution`.
- `seedream`: Volcengine Seedream provider for text-to-image.
- `gpt_image`: OpenAI GPT Image provider; supports `size` and `quality`.
- `dashscope`: Aliyun Model Studio provider; supported model ids are `wan2.7-image-pro`, `qwen-image-2.0-pro`, and `z-image-turbo`.

`3DGeneration` currently exposes these provider-aware tool parameters:

- `hy3d`: default provider; supports prompt-only, image-only, and `generate_type=sketch` prompt-plus-image input.
- `seed3d`: Volcengine Ark image-to-3D provider; requires one `input_path` / `input_paths` or `image_url`; optional controls include `file_format` (`glb|obj|usd|usdz`) and `subdivision_level` (`low|medium|high`).
- `hyper3d`: Volcengine Ark text/image-to-3D provider; supports English prompt-only or 1-5 reference images; optional controls include `file_format`, `mesh_mode`, `material`, `quality_override`, and `hd_texture`.
- `hitem3d`: Volcengine Ark image-to-3D provider; requires 1-4 externally accessible `image_url` / `image_urls`; optional controls include `file_format`, `resolution`, `face_count`, `request_type`, and `multi_images_bit`.

## 🌐 Supported Channels

CreativeClaw currently supports:

- **Local Web Chat (recommended)**: browser-based chat with optimized realtime progress, Markdown rendering, compact design brief forms, design-system previews, Design preview, Visual Board annotation, and media / PDF / PPT previews
- **CLI Chat**: lightweight command-line chat for quick calls and automation
- **Telegram**: chat in Telegram
- **Feishu**: chat in Feishu

### Local Web Chat

```bash
creative-claw chat web
```

The default address is `http://127.0.0.1:18900`.

You can also set it explicitly:

```bash
creative-claw chat web --host 127.0.0.1 --port 18900 --title "CreativeClaw Web Chat"
```

### Telegram

After filling the Telegram fields in `~/.creative-claw/conf.json`:

```bash
creative-claw chat telegram
```

### Feishu

After filling the Feishu fields in `~/.creative-claw/conf.json`:

```bash
creative-claw chat feishu
```

Additional notes:

- `FEISHU_APP_ID` and `FEISHU_APP_SECRET` are the main required values for Feishu.
- `FEISHU_ENCRYPT_KEY` and `FEISHU_VERIFICATION_TOKEN` are only needed when the matching security settings are enabled in the Feishu platform.
- Web chat defaults also live in `~/.creative-claw/conf.json`, and CLI flags can still override them for one run.

## 🧰 Built-in Skill

### 🎵 MiniMax CLI Skill

CreativeClaw includes a project-level MiniMax skill at `skills/minimax-cli-skill/SKILL.md`.

Use it when:

- you explicitly want MiniMax or `mmx`
- you want MiniMax music generation
- you want MiniMax speech synthesis
- you need MiniMax file upload or `file_id`-based follow-up workflows

For agent-style usage, API key login is the recommended setup:

```bash
# install CLI globally
npm install -g mmx-cli
# Authenticate
mmx auth login --api-key sk-xxxxx
mmx auth status
```

> Requires [Node.js](https://nodejs.org) 18+

> **Requires a MiniMax Token Plan** — [Global](https://platform.minimax.io/subscribe/token-plan) · [CN](https://platform.minimaxi.com/subscribe/token-plan)

In practice, you only need this skill when you explicitly want MiniMax-specific capabilities.

## 📚 More Docs

- [docs/development.md](docs/development.md): architecture, environment, credentials, tests, and development notes
- [docs/model_and_token_map.md](docs/model_and_token_map.md): model names, mapped experts, and token application links
- [docs/expert_model_capability_map_zh.md](docs/expert_model_capability_map_zh.md): current expert capability boundaries, including Kling route coverage and constraints

## 🛠️ TODO

- [ ] Support more image-generation and video-generation models
- [ ] Add more creativity-related skills
- [x] Support more LLM providers
- [ ] Support more channels

## License

CreativeClaw project code is licensed under the MIT License. See [LICENSE](LICENSE).

Bundled skills, assets, fonts, and third-party materials under `skills/` may include their own license files and remain governed by those terms.
