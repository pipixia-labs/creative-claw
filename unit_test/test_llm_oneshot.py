import unittest
from types import SimpleNamespace

from google.genai.types import Blob, Content, Part

from src.runtime.llm_oneshot import run_oneshot_llm


class LlmOneShotTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_oneshot_llm_injects_parts_and_collects_visible_outputs(self) -> None:
        captured_contents: list[Content] = []

        class _FakeEvent:
            def __init__(self, *, parts: list[Part], final: bool) -> None:
                self.content = Content(role="model", parts=parts)
                self._final = final

            def is_final_response(self) -> bool:
                return self._final

        class _FakeLlmAgent:
            def __init__(self, **kwargs) -> None:
                self.before_model_callback = kwargs["before_model_callback"]

            async def run_async(self, ctx):
                request = SimpleNamespace(contents=[])
                self.before_model_callback(SimpleNamespace(state={}), request)
                captured_contents.extend(request.contents)
                yield _FakeEvent(
                    parts=[
                        Part(text="draft"),
                        Part(inline_data=Blob(mime_type="image/png", data=b"png-data")),
                    ],
                    final=False,
                )
                yield _FakeEvent(
                    parts=[
                        Part(text="hidden thought", thought=True),
                        Part(text="final answer"),
                    ],
                    final=True,
                )

        result = await run_oneshot_llm(
            SimpleNamespace(),
            name="TestOneShotAgent",
            model="test-model",
            instruction="Return a short answer.",
            user_text="hello",
            user_parts=[Part(text="attached prompt")],
            agent_cls=_FakeLlmAgent,
        )

        self.assertEqual(captured_contents[0].role, "user")
        self.assertEqual(captured_contents[0].parts[0].text, "hello")
        self.assertEqual(captured_contents[0].parts[1].text, "attached prompt")
        self.assertEqual(result.texts, ["draft", "final answer"])
        self.assertEqual(result.text, "final answer")
        self.assertEqual(result.final_text, "final answer")
        self.assertEqual(result.image_data, b"png-data")


if __name__ == "__main__":
    unittest.main()
