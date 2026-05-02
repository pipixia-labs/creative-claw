import os
import asyncio
import base64
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, AsyncGenerator

import requests
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest
from google.genai import types
from google.genai.types import Content, Part
from openai import OpenAI

from conf.api import API_CONFIG
from conf.llm import build_llm
from conf.system import SYS_CONFIG
from src.logger import logger

_OPENAI_GPT_IMAGE_MODEL = "gpt-image-2"
_DASHSCOPE_API_BASE = "https://dashscope.aliyuncs.com/api/v1"
_DASHSCOPE_IMAGE_GENERATION_ENDPOINT = "/services/aigc/image-generation/generation"
_DASHSCOPE_MULTIMODAL_GENERATION_ENDPOINT = "/services/aigc/multimodal-generation/generation"
_DASHSCOPE_TASK_ENDPOINT_TEMPLATE = "/tasks/{task_id}"
_DASHSCOPE_HTTP_RETRY_ATTEMPTS = 3
_DASHSCOPE_HTTP_RETRY_DELAY_SECONDS = 1.0
_DASHSCOPE_WAN_IMAGE_MODEL = "wan2.7-image-pro"
_DASHSCOPE_QWEN_IMAGE_MODEL = "qwen-image-2.0-pro"
_DASHSCOPE_Z_IMAGE_MODEL = "z-image-turbo"
DASHSCOPE_IMAGE_MODEL_NAMES = (
    _DASHSCOPE_WAN_IMAGE_MODEL,
    _DASHSCOPE_QWEN_IMAGE_MODEL,
    _DASHSCOPE_Z_IMAGE_MODEL,
)


@dataclass
class ImageGenerationResult:
    """Normalized image generation result across different providers."""

    status: str
    message: Any
    provider: str
    model_name: str
    usage: dict | None = None


def normalize_dashscope_image_model_name(raw_value: Any) -> str:
    """Return one supported DashScope image generation model name."""
    value = str(raw_value or "").strip()
    return value if value in DASHSCOPE_IMAGE_MODEL_NAMES else _DASHSCOPE_WAN_IMAGE_MODEL


def _get_dashscope_api_key() -> str:
    """Return the configured DashScope API key."""
    return os.environ.get("DASHSCOPE_API_KEY", "").strip() or str(API_CONFIG.DASHSCOPE_API_KEY).strip()


def _build_dashscope_http_session() -> requests.Session:
    """Build one DashScope HTTP session with deterministic environment behavior."""
    session = requests.Session()
    session.trust_env = False
    return session


def _build_dashscope_headers(api_key: str, *, async_task: bool = False) -> dict[str, str]:
    """Build headers for DashScope image API requests."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if async_task:
        headers["X-DashScope-Async"] = "enable"
    return headers


def _run_dashscope_retryable_http_call(
    request_call: Callable[[], requests.Response],
    *,
    action: str,
) -> requests.Response:
    """Run one retryable DashScope HTTP call."""
    for attempt in range(1, _DASHSCOPE_HTTP_RETRY_ATTEMPTS + 1):
        try:
            return request_call()
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.SSLError,
            requests.exceptions.Timeout,
        ) as exc:
            if attempt >= _DASHSCOPE_HTTP_RETRY_ATTEMPTS:
                raise
            logger.warning(
                "dashscope image {} failed on attempt {}/{} with {}, retrying ...",
                action,
                attempt,
                _DASHSCOPE_HTTP_RETRY_ATTEMPTS,
                type(exc).__name__,
            )
            time.sleep(_DASHSCOPE_HTTP_RETRY_DELAY_SECONDS * attempt)
    raise RuntimeError(f"unreachable retry path for dashscope image {action}")


def _decode_dashscope_json_response(response: requests.Response) -> dict[str, Any]:
    """Decode one DashScope JSON response and surface API errors clearly."""
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        response_text = response.text.strip()
        if response_text:
            raise ValueError(f"DashScope API HTTP {response.status_code}: {response_text}") from exc
        raise
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("DashScope response is not a JSON object.")
    code = str(payload.get("code", "") or "").strip()
    if code and code.lower() not in {"success", "ok", "0", "200"}:
        message = payload.get("message") or payload.get("msg") or "unknown error"
        raise ValueError(f"DashScope API error: {message}")
    return payload


def _submit_dashscope_generation_sync(
    *,
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Submit one DashScope image generation request."""
    with _build_dashscope_http_session() as session:
        response = session.post(
            f"{_DASHSCOPE_API_BASE}{endpoint}",
            headers=headers,
            json=payload,
            timeout=60,
        )
    return _decode_dashscope_json_response(response)


def _get_dashscope_task_sync(*, task_id: str, headers: dict[str, str]) -> dict[str, Any]:
    """Fetch one DashScope asynchronous image task status."""
    endpoint = _DASHSCOPE_TASK_ENDPOINT_TEMPLATE.format(task_id=task_id)
    with _build_dashscope_http_session() as session:
        response = _run_dashscope_retryable_http_call(
            lambda: session.get(
                f"{_DASHSCOPE_API_BASE}{endpoint}",
                headers=headers,
                timeout=60,
            ),
            action="task polling",
        )
    return _decode_dashscope_json_response(response)


def _download_dashscope_binary_sync(url: str) -> bytes:
    """Download one DashScope image result."""
    with _build_dashscope_http_session() as session:
        response = _run_dashscope_retryable_http_call(
            lambda: session.get(url, timeout=120),
            action="binary download",
        )
        response.raise_for_status()
        return response.content


def _extract_dashscope_task_id(payload: dict[str, Any]) -> str:
    """Extract one DashScope task id from a create-task response."""
    output = payload.get("output", {})
    task_id = str(output.get("task_id", "") if isinstance(output, dict) else "").strip()
    if not task_id:
        raise ValueError("DashScope create-task response did not include output.task_id.")
    return task_id


def _extract_dashscope_task_status(payload: dict[str, Any]) -> str:
    """Extract one normalized DashScope task status."""
    output = payload.get("output", {})
    return str(output.get("task_status", "") if isinstance(output, dict) else "").strip().upper()


def _extract_dashscope_image_url(payload: dict[str, Any]) -> str:
    """Extract the first generated image URL from a DashScope response."""
    output = payload.get("output", {})
    if not isinstance(output, dict):
        raise ValueError("DashScope image output is not a JSON object.")

    results = output.get("results", [])
    if isinstance(results, list) and results:
        first_result = results[0] if isinstance(results[0], dict) else {}
        url = str(first_result.get("url", "") or first_result.get("image_url", "") or "").strip()
        if url:
            return url

    choices = output.get("choices", [])
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content", []) if isinstance(message, dict) else []
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("image", "") or item.get("image_url", "") or item.get("url", "") or "").strip()
                if url:
                    return url

    url = str(output.get("url", "") or output.get("image_url", "") or "").strip()
    if url:
        return url
    raise ValueError("DashScope image response did not include a downloadable image URL.")


def _normalize_dashscope_image_size(model_name: str, raw_size: Any, raw_resolution: Any) -> str:
    """Return one DashScope-compatible image size or resolution value."""
    size = str(raw_size or "").strip()
    resolution = str(raw_resolution or "").strip()
    value = size or resolution
    if value:
        return value.replace("x", "*")
    if model_name == _DASHSCOPE_WAN_IMAGE_MODEL:
        return "2K"
    if model_name == _DASHSCOPE_QWEN_IMAGE_MODEL:
        return "2048*2048"
    return "1024*1536"


def _build_dashscope_image_payload(
    *,
    prompt: str,
    model_name: str,
    size: str,
    negative_prompt: str,
    prompt_extend: bool | None,
    watermark: bool,
    thinking_mode: bool | None,
) -> dict[str, Any]:
    """Build one DashScope text-to-image request payload."""
    parameters: dict[str, Any] = {
        "size": size,
        "n": 1,
    }
    if model_name == _DASHSCOPE_WAN_IMAGE_MODEL:
        parameters["watermark"] = bool(watermark)
        if thinking_mode is not None:
            parameters["thinking_mode"] = bool(thinking_mode)
    elif model_name == _DASHSCOPE_QWEN_IMAGE_MODEL:
        parameters["watermark"] = bool(watermark)
        parameters["prompt_extend"] = True if prompt_extend is None else bool(prompt_extend)
        if negative_prompt:
            parameters["negative_prompt"] = negative_prompt
    else:
        parameters["prompt_extend"] = False if prompt_extend is None else bool(prompt_extend)

    return {
        "model": model_name,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ]
        },
        "parameters": parameters,
    }


async def prompt_enhancement_tool(ctx: InvocationContext, prompt: str) -> dict[str, str]:
    system_prompt = """
    You are a professional prompt optimization expert, proficient in the concretization and optimization of prompt words in the field of text, biology, and graphics.
    The user will input the initial prompt, and you need to polish or expand it.
    Your task has two situations:
    1. The user entered a vague and brief instruction (usually a short sentence without any details)
    You must generate a more detailed, creative, and high-quality prompt word based on original prompt. The specific content and details of the image are all up to you, but it needs to be consistent with the original input instructions.


    2. The user entered detailed instructions (usually long sentences exceeding 100 words)
    You don't need to add any visual content, but rather polish the prompt. Your polishing mainly focuses on the following aspects:
    **Picture details**: emphasize the details in the original prompt
    **Special elements**: If there are elements such as text, symbols, etc. in the original prompt, you need to make their description more precise.
    Be careful! In this case, you must ensure that the newly generated prompt is strictly consistent with the original prompt, without losing or changing any semantic content.
    """

    def before_model_callback(callback_context: CallbackContext, llm_request: LlmRequest):
        user_prompt = f"This is the original prompt entered by the user: {prompt}, please polish or enhance it."
        llm_request.contents.append(Content(role='user', parts=[Part(text=user_prompt)]))

    
    llm = LlmAgent(
        name="prompt_enhancement",
        model=build_llm(),
        instruction=system_prompt,
        include_contents='none',
        before_model_callback=before_model_callback
    )
    
    try:
        enhanced_prompt = None
        async for event in llm.run_async(ctx):
            if event.is_final_response() and event.content and event.content.parts:
                generated_text = next((part.text for part in event.content.parts if part.text), None)
                if generated_text:
                    enhanced_prompt = generated_text
        if enhanced_prompt:
            return {
                'status': 'success',
                'message': enhanced_prompt
            }
        else:
            return {
                'status': 'error',
                'message': "LLmAgent calling failed"
            }
            

    except Exception as e:
        error_text = f"LlmAgent failed: {str(e)}"
        logger.error(error_text)
        return {
            'status': 'error',
            'message': error_text
        }

def _normalize_aspect_ratio(raw_value: str) -> str:
    """Normalize arbitrary aspect ratio hints into one supported Gemini value."""
    supported = {"1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"}
    value = str(raw_value or "").strip()
    return value if value in supported else "16:9"


async def gemini_image_generation(
    ctx: InvocationContext,
    prompt: str,
    *,
    aspect_ratio: str = "16:9",
    resolution: str = "1K",
) -> ImageGenerationResult:
    """Generate one image with Gemini image preview."""
    try:
        normalized_ratio = _normalize_aspect_ratio(aspect_ratio)

        def before_model_callback(
            callback_context: CallbackContext,
            llm_request: LlmRequest,
        ) -> None:
            llm_request.contents.append(Content(role="user", parts=[Part(text=prompt)]))

        llm = LlmAgent(
            name="media_gemini_image_generation",
            model="gemini-3.1-flash-image-preview",
            instruction="Generate an image according to the prompt.",
            include_contents="none",
            generate_content_config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=normalized_ratio,
                    image_size=resolution,
                ),
            ),
            before_model_callback=before_model_callback,
        )

        text_message = ""
        image_data: bytes | None = None
        async for event in llm.run_async(ctx):
            if not event.content or not event.content.parts:
                continue
            for part in event.content.parts:
                if part.text is not None:
                    text_message = part.text
                elif part.inline_data is not None:
                    image_data = part.inline_data.data

        if image_data:
            return ImageGenerationResult(
                status="success",
                message=image_data,
                provider="gemini",
                model_name="gemini-3.1-flash-image-preview",
            )

        return ImageGenerationResult(
            status="error",
            message=text_message or "gemini returned no image",
            provider="gemini",
            model_name="gemini-3.1-flash-image-preview",
        )
    except Exception as exc:
        return ImageGenerationResult(
            status="error",
            message=f"gemini exception: {exc}",
            provider="gemini",
            model_name="gemini-3.1-flash-image-preview",
        )


async def seedream_image_generation(prompt: str, ark_api_key: str) -> ImageGenerationResult:
    """Generate one image with Seedream when the optional SDK is available."""
    if not ark_api_key:
        return ImageGenerationResult(
            status="error",
            message="ARK_API_KEY is not set.",
            provider="seedream",
            model_name="doubao-seedream-5-0-260128",
        )

    try:
        from volcenginesdkarkruntime import Ark
    except Exception as exc:
        return ImageGenerationResult(
            status="error",
            message=f"seedream SDK unavailable: {exc}",
            provider="seedream",
            model_name="doubao-seedream-5-0-260128",
        )

    try:
        client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=ark_api_key,
        )
        response = client.images.generate(
            model="doubao-seedream-5-0-260128",
            prompt=prompt,
            size="2K",
            output_format="png",
            response_format="b64_json",
            watermark=False,
        )
        if getattr(response, "error", None):
            return ImageGenerationResult(
                status="error",
                message=f"seedream generation failed: {response.error}",
                provider="seedream",
                model_name="doubao-seedream-5-0-260128",
            )

        for item in getattr(response, "data", []) or []:
            image_base64 = getattr(item, "b64_json", None)
            if image_base64:
                import base64

                return ImageGenerationResult(
                    status="success",
                    message=base64.b64decode(image_base64),
                    provider="seedream",
                    model_name="doubao-seedream-5-0-260128",
                )

        return ImageGenerationResult(
            status="error",
            message="seedream returned empty images",
            provider="seedream",
            model_name="doubao-seedream-5-0-260128",
        )
    except Exception as exc:
        logger.opt(exception=exc).error(
            "seedream exception: error_type={} error={!r}",
            type(exc).__name__,
            exc,
        )
        return ImageGenerationResult(
            status="error",
            message=f"seedream exception: {exc}",
            provider="seedream",
            model_name="doubao-seedream-5-0-260128",
        )


async def gpt_image_generation(
    prompt: str,
    openai_api_key: str,
    *,
    size: str = "1024x1024",
    quality: str = "high",
) -> ImageGenerationResult:
    """Generate one image with OpenAI GPT Image 2."""
    if not openai_api_key:
        return ImageGenerationResult(
            status="error",
            message="OPENAI_API_KEY is not set.",
            provider="gpt_image",
            model_name=_OPENAI_GPT_IMAGE_MODEL,
        )

    def _generate() -> ImageGenerationResult:
        try:
            client = OpenAI(api_key=openai_api_key)
            result = client.images.generate(
                model=_OPENAI_GPT_IMAGE_MODEL,
                prompt=prompt,
                size=size,
                quality=quality,
                output_format="png",
            )
            image_base64 = getattr(result.data[0], "b64_json", None) if getattr(result, "data", None) else None
            if not image_base64:
                return ImageGenerationResult(
                    status="error",
                    message="gpt-image returned empty images",
                    provider="gpt_image",
                    model_name=_OPENAI_GPT_IMAGE_MODEL,
                )
            return ImageGenerationResult(
                status="success",
                message=base64.b64decode(image_base64),
                provider="gpt_image",
                model_name=_OPENAI_GPT_IMAGE_MODEL,
            )
        except Exception as exc:
            logger.opt(exception=exc).error(
                "gpt-image exception: error_type={} error={!r}",
                type(exc).__name__,
                exc,
            )
            return ImageGenerationResult(
                status="error",
                message=f"gpt-image exception: {exc}",
                provider="gpt_image",
                model_name=_OPENAI_GPT_IMAGE_MODEL,
            )

    return await asyncio.to_thread(_generate)


async def dashscope_image_generation(
    prompt: str,
    *,
    model_name: str = "",
    size: str = "",
    resolution: str = "",
    negative_prompt: str = "",
    prompt_extend: bool | None = None,
    watermark: bool = False,
    thinking_mode: bool | None = None,
) -> ImageGenerationResult:
    """Generate one image with DashScope text-to-image models."""
    api_key = _get_dashscope_api_key()
    current_model = normalize_dashscope_image_model_name(model_name)
    if not api_key:
        return ImageGenerationResult(
            status="error",
            message="DASHSCOPE_API_KEY is not set.",
            provider="dashscope",
            model_name=current_model,
        )

    try:
        current_size = _normalize_dashscope_image_size(current_model, size, resolution)
        payload = _build_dashscope_image_payload(
            prompt=prompt,
            model_name=current_model,
            size=current_size,
            negative_prompt=str(negative_prompt or "").strip(),
            prompt_extend=prompt_extend,
            watermark=watermark,
            thinking_mode=thinking_mode,
        )
        headers = _build_dashscope_headers(api_key, async_task=current_model == _DASHSCOPE_WAN_IMAGE_MODEL)

        if current_model == _DASHSCOPE_WAN_IMAGE_MODEL:
            create_payload = await asyncio.to_thread(
                _submit_dashscope_generation_sync,
                endpoint=_DASHSCOPE_IMAGE_GENERATION_ENDPOINT,
                headers=headers,
                payload=payload,
            )
            task_id = _extract_dashscope_task_id(create_payload)
            poll_headers = _build_dashscope_headers(api_key)
            success_statuses = {"SUCCEEDED", "SUCCESS", "COMPLETED"}
            failure_statuses = {"FAILED", "UNKNOWN", "CANCELED", "CANCELLED"}
            for _ in range(120):
                task_payload = await asyncio.to_thread(
                    _get_dashscope_task_sync,
                    task_id=task_id,
                    headers=poll_headers,
                )
                task_status = _extract_dashscope_task_status(task_payload)
                if task_status in success_statuses:
                    image_url = _extract_dashscope_image_url(task_payload)
                    image_bytes = await asyncio.to_thread(_download_dashscope_binary_sync, image_url)
                    return ImageGenerationResult(
                        status="success",
                        message=image_bytes,
                        provider="dashscope",
                        model_name=current_model,
                    )
                if task_status in failure_statuses:
                    output = task_payload.get("output", {})
                    error_message = output.get("message", "") if isinstance(output, dict) else ""
                    raise ValueError(
                        f"DashScope image generation failed with task_status={task_status}: "
                        f"{error_message or 'unknown error'}"
                    )
                await asyncio.sleep(5)
            return ImageGenerationResult(
                status="error",
                message="dashscope image generation timed out while polling task status.",
                provider="dashscope",
                model_name=current_model,
            )

        response_payload = await asyncio.to_thread(
            _submit_dashscope_generation_sync,
            endpoint=_DASHSCOPE_MULTIMODAL_GENERATION_ENDPOINT,
            headers=headers,
            payload=payload,
        )
        image_url = _extract_dashscope_image_url(response_payload)
        image_bytes = await asyncio.to_thread(_download_dashscope_binary_sync, image_url)
        return ImageGenerationResult(
            status="success",
            message=image_bytes,
            provider="dashscope",
            model_name=current_model,
        )
    except Exception as exc:
        logger.opt(exception=exc).error(
            "dashscope image generation failed: error_type={} error={!r}",
            type(exc).__name__,
            exc,
        )
        return ImageGenerationResult(
            status="error",
            message=f"dashscope exception: {exc}",
            provider="dashscope",
            model_name=current_model,
        )


async def nano_banana_image_generation_tool(
    ctx: InvocationContext,
    prompt: str,
    aspect_ratio="16:9",
    resolution="1K",
) -> dict[str, Any]:
    # aspect_ratio = "16:9"  # "1:1","2:3","3:2","3:4","4:3","4:5","5:4","9:16","16:9","21:9"
    # resolution = "2K"  # "1K", "2K", "4K"
    logger.info("calling nano banana for image generation ...")

    result = await gemini_image_generation(
        ctx,
        prompt,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )
    if result.status == "success" and isinstance(result.message, (bytes, bytearray)):
        logger.info(f"nano_banana completed image generation, binary size={len(result.message)}")
    return {
        "status": result.status,
        "message": result.message,
        "provider": result.provider,
        "model_name": result.model_name,
        "usage": result.usage,
    }


async def seedream_image_generation_tool(prompt: str) -> AsyncGenerator[dict[str, Any], None]:
    logger.info("calling seedream for image generation ...")
    ark_api_key = os.environ.get("ARK_API_KEY") or ""
    result = await seedream_image_generation(prompt, ark_api_key)
    return {
        "status": result.status,
        "message": result.message,
        "provider": result.provider,
        "model_name": result.model_name,
        "usage": result.usage,
    }


async def gpt_image_generation_tool(prompt: str) -> dict[str, Any]:
    """Run GPT Image generation with one normalized prompt."""
    logger.info("calling gpt-image for image generation ...")
    openai_api_key = str(API_CONFIG.OPENAI_API_KEY).strip() or os.environ.get("OPENAI_API_KEY") or ""
    result = await gpt_image_generation(
        prompt,
        openai_api_key,
        size=os.environ.get("OPENAI_GPT_IMAGE_SIZE", "1024x1024"),
        quality=os.environ.get("OPENAI_GPT_IMAGE_QUALITY", "high"),
    )
    return {
        "status": result.status,
        "message": result.message,
        "provider": result.provider,
        "model_name": result.model_name,
        "usage": result.usage,
    }
