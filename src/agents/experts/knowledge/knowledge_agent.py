from __future__ import annotations

from typing import Any, AsyncGenerator, Literal

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.models import LlmRequest
from google.genai.types import Content, Part
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from conf.llm import build_llm, resolve_llm_model_name
from conf.system import SYS_CONFIG
from src.logger import logger
from src.agents.experts.schema_utils import (
    as_non_empty_string_list,
    clean_string,
    current_output_dict,
)
from src.runtime.workspace import load_local_file_part


class KnowledgeAgentParameters(BaseModel):
    """Structured request contract read from ``current_parameters``."""

    model_config = {"extra": "ignore"}

    prompt: str = Field(description="The design or image-generation task to analyze.")
    input_paths: list[str] = Field(
        default_factory=list,
        description="Optional workspace-relative reference image paths.",
    )

    @model_validator(mode="before")
    @classmethod
    def _support_legacy_input_path(cls, value: Any) -> Any:
        """Map legacy ``input_path`` into the normalized ``input_paths`` field."""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "input_paths" not in data and "input_path" in data:
            data["input_paths"] = data.get("input_path")
        return data

    @field_validator("prompt", mode="before")
    @classmethod
    def _strip_prompt(cls, value: Any) -> str:
        """Strip the prompt while preserving the existing empty-prompt behavior."""
        return clean_string(value)

    @field_validator("input_paths", mode="before")
    @classmethod
    def _normalize_input_paths(cls, value: Any) -> list[str]:
        """Normalize reference image path input."""
        return as_non_empty_string_list(value)


class KnowledgeAgentOutput(BaseModel):
    """Structured ``current_output`` contract emitted by ``KnowledgeAgent``."""

    status: Literal["success", "error"]
    message: str
    output_text: str | None = None

    @field_validator("message", mode="before")
    @classmethod
    def _strip_message(cls, value: Any) -> str:
        """Normalize the user-visible status message."""
        return clean_string(value)

    def to_current_output(self) -> dict[str, Any]:
        """Convert to the stable dictionary contract stored in session state."""
        return current_output_dict(self)


def _parse_knowledge_parameters(raw_parameters: Any) -> KnowledgeAgentParameters | None:
    """Parse session parameters without raising from ADK callbacks."""
    try:
        return KnowledgeAgentParameters.model_validate(raw_parameters or {})
    except ValidationError:
        return None


async def knowledge_before_model_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """Attach the structured KnowledgeAgent request to the child LLM call."""
    current_parameters = _parse_knowledge_parameters(callback_context.state.get("current_parameters", {}))
    if current_parameters is None:
        return

    if current_parameters.prompt:
        llm_request.contents.append(
            Content(role="user", parts=[Part(text=f"Current task is: {current_parameters.prompt}")])
        )

    if not current_parameters.input_paths:
        return

    file_parts = [Part(text="Here are some workspace pictures you can refer to: \n")]
    for i, file_path in enumerate(current_parameters.input_paths):
        file_parts.append(Part(text=f"image{i + 1}, path: {file_path}"))
        file_parts.append(load_local_file_part(file_path))

    llm_request.contents.append(Content(role="user", parts=file_parts))



class KnowledgeAgent(BaseAgent):
    model_config = {"arbitrary_types_allowed": True}
    llm: LlmAgent

    def __init__(
        self,
        name: str,
        description: str = "",
        llm_model: str = "",
    ):
        if not llm_model:
            llm_model = SYS_CONFIG.llm_model
        logger.info(f"KnowledgeAgent: using llm: {resolve_llm_model_name(llm_model)}")
        description = "Analyze input requirement, output refined design scheme or enhanced prompt"

        # The LLM does not automatically receive prior session content.
        llm = LlmAgent(
            name=name,
            model=build_llm(llm_model),
            description=description,
            instruction=knowledge_intruction,
            before_model_callback=knowledge_before_model_callback,
            include_contents="none",
        )
        
        super().__init__(
            name=name,
            description=description,
            llm=llm,
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run the knowledge LLM and persist a structured ``current_output``."""
        current_parameters = _parse_knowledge_parameters(ctx.session.state.get("current_parameters", {}))
        if current_parameters is None:
            error_text = f"Missing parameters provided to {self.name}, must include: prompt"
            current_output = KnowledgeAgentOutput(status="error", message=error_text).to_current_output()
            logger.error(error_text)

            yield Event(
                author=self.name,
                content=Content(role="model", parts=[Part(text=error_text)]),
                actions=EventActions(state_delta={"current_output": current_output}),
            )
            return
        
        text_list = []
        async for event in self.llm.run_async(ctx):
            if event.partial:
                yield event
                continue
            if event.is_final_response() and event.content and event.content.parts:
                generated_text = next((part.text for part in event.content.parts if part.text), None)
                if not generated_text:
                    continue
                yield event  # model response will be appended to session
                text_list.append(generated_text)

        if len(text_list) == 0:
            message = f"{self.name} generate response failed"
            logger.error(message)
            current_output = KnowledgeAgentOutput(status="error", message=message).to_current_output()
        else:
            message = f"{self.name} has completed the design."
            output_text = "\n".join(text_list)
            current_output = KnowledgeAgentOutput(
                status="success",
                message=message,
                output_text=output_text,
            ).to_current_output()
        
        yield Event(
            author="KnowledgeAgent",
            content=Content(role="model", parts=[Part(text=message)]),
            actions=EventActions(state_delta={"current_output": current_output}),
        )



knowledge_intruction = """
You are a professional visual designer who will accept an image generation task, sometimes with several reference images.
Your task is to output image design schemes based on requirements and references, and then generate prompts based on the schemes to guide subsequent image generation.

# task input
 - Design requirement: user's description of image, or a design request of a product or image.
 - Reference images: images of varying quantities used for reference

# task output
 - Design scheme: Detailed image design proposal that includes specific settings for the foreground and background of the image, as well as specific designs for visual elements that may exist in the image
 - Image generation prompt: Prompt based on the design used to guide subsequent image generation

# Task steps (strictly follow)
Firstly, you need to understand the user's image generation or design task and the number of designs and prompts that need to be output, and then output them in the following two steps:
Step 1: Output a specific design proposal in markdown format, including detailed explanations of the overall and local designs
Step 2: Refer to the design scheme generated in the previous step and generate one or more image prompts

# Design scheme requirements
Your visual design proposal must include the following elements:
1. Foreground visual elements
 - If the task involves designing a certain product, based on existing knowledge and provided information, use your imagination and creativity to generate a detailed design plan for the product
 - Detailed description of all foreground elements, including style, color, texture, etc
 - Detailed design descriptions of other necessary special elements that may be included in the task, such as fonts, symbols, logos, etc.

2. Screen layout
 - Design the overall layout of the image, including detailed layouts of all foreground and background elements
 - The layout of possible special elements, such as logos, fonts, special symbols, or lighting.
 - Layout can add personalized design to meet design goals, especially for product display images, where a real-life scenario can be designed to highlight the product.

3. Macro atmosphere
 - Detailed overall visual atmosphere design, including color tones, environment, and implicit information and emotions that the image needs to convey, such as furniture conveying a warm atmosphere, while art posters conveying avant-garde design.
 - Various professional photography parameters, such as exposure, saturation, contrast
 - Other artistic styles that can enhance visual expression.

# Image generation prompt requirements
After generating the design scheme, you must generate one or more prompts based on the scheme. You must obey the following rules:
1. Strictly comply with design requirements
 - The prompt must include all the contents of the design proposal and strictly consistent with the design.
 - Do not add elements that are not included in the design or modify the the design in the prompt

2. Compact and refined language
 - Prompt needs to remove redundant information while including all design scheme information
 - Prompt is plain text and special formats such as md or json are prohibited.

3. Output one or more prompts
 - Sometimes users require multiple prompts. When outputting, you must use formats such as prompt1: ***, prompt2: *** .

# Special attention
1. In the two-step execution process, you need to indicate at the beginning of the output of the first and second steps respectively: 'This is/are the design scheme(s):', 'This is/are the image generation prompt(s)'
2. Sometimes users may request to output multiple different designs and prompts for the same goal, and in this case, you need to specify the number of generated designs and prompts and output the request numbers of design and prompt.
3. If the user requests to output multiple images, it is prohibited to generate multiple images by generating a grid like image. Instead, multiple different prompts must be output.
4. If a user requests to design a certain product, what you should actually design is the display image of the product. You can use your imagination appropriately. For example, you can design the following scene for a vacuum cleaner product promotional image: a family happily using a vacuum cleaner to clean the house.
5. For product or poster design proposals, you must carefully consider whether to add text and symbols to enhance the artistic effect.

"""
