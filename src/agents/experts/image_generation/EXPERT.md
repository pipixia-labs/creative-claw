+++
name = "ImageGenerationAgent"
enabled = true
default_provider = "nano_banana"
input_types = ["prompt"]
output_types = ["image"]
routing_keywords = ["image generation", "text to image", "poster", "illustration", "visual", "render"]
parameter_examples = [
  "{'prompt': ['prompt1', 'prompt2', ...], 'provider': 'nano_banana|seedream|gpt_image|dashscope'(optional), 'model_name': 'wan2.7-image-pro|qwen-image-2.0-pro|z-image-turbo'(optional, dashscope only), 'aspect_ratio': '16:9'(optional), 'resolution': '1K|2K|4K'(optional), 'size': '1024x1024|1024x1536|1536x1024|2K|2048*2048|1024*1536'(optional, provider-specific), 'quality': 'low|medium|high'(optional, gpt_image only), 'negative_prompt': 'things to avoid'(optional, dashscope qwen only), 'prompt_extend': true(optional, dashscope qwen/z-image), 'watermark': false(optional, dashscope wan/qwen), 'thinking_mode': true(optional, dashscope wan)}",
]
+++

# ImageGenerationAgent

## When to Use

Use this expert for generating one or more new images from text prompts only. It is the correct route when the user asks for a new visual, poster, illustration, product concept image, or design render and does not need to preserve or modify an existing image.

## Routing Notes

- Use `nano_banana` by default for ordinary text-to-image generation.
- Use `seedream` only when the user requests Seedream or the task clearly needs that provider.
- Use `gpt_image` only for text-to-image generation with OpenAI GPT Image controls such as `size` and `quality`.
- Use `dashscope` when the user asks for Aliyun Model Studio, Wan image, Qwen Image 2.0, or Z-Image generation.
- If the user provides reference images or asks to modify an existing image, use `ImageEditingAgent` instead.
- If the user asks to describe, OCR, analyze style, or reverse engineer a prompt from an image, use `ImageUnderstandingAgent` instead.

## Provider Boundaries

- `nano_banana` uses Gemini image generation and supports optional `aspect_ratio` and `resolution`.
- `seedream` uses Seedream image generation and does not use `aspect_ratio`, `resolution`, `size`, or `quality` parameters in the current integration.
- `gpt_image` is available only on `ImageGenerationAgent`; it supports `size` and `quality`.
- `dashscope` supports `wan2.7-image-pro`, `qwen-image-2.0-pro`, and `z-image-turbo`. Use `model_name` to choose the model; `size` may use DashScope values such as `2K`, `2048*2048`, or `1024*1536`.

## When Not to Use

Do not use this expert for image editing, reference-image workflows, OCR, image analysis, segmentation masks, object grounding, or deterministic local image operations.
