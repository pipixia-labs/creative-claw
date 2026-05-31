import base64
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.agents.experts.music_generation import tool as music_tool
from src.agents.experts.music_generation.music_generation_expert import (
    MusicGenerationExpert,
    MusicGenerationOutput,
    MusicGenerationParameters,
    MusicGenerationResultItem,
)
from src.agents.experts.speech_synthesis import tool as speech_tool
from src.agents.experts.speech_synthesis.speech_synthesis_expert import (
    SpeechSynthesisExpert,
    SpeechSynthesisOutput,
    SpeechSynthesisParameters,
    SpeechSynthesisResultItem,
)


def _build_ctx(state: dict) -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(
            state=state,
            app_name="test_app",
            user_id="user_1",
            id="session_1",
        ),
    )


class SpeechSynthesisToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_bytedance_tts_tool_assembles_streamed_audio(self) -> None:
        encoded_audio = base64.b64encode(b"audio-bytes").decode("utf-8")
        captured_call: dict[str, object] = {}

        class _FakeResponse:
            status_code = 200
            headers = {"X-Tt-Logid": "log-123"}
            text = ""

            def iter_lines(self, decode_unicode=True):
                yield f"data: {json.dumps({'code': 0, 'data': encoded_audio})}"
                yield json.dumps({"code": 0, "sentence": {"text": "hello world"}})
                yield json.dumps({"code": 20000000, "usage": {"characters": 11}})

            def close(self) -> None:
                return None

        class _FakeSession:
            def post(self, url, headers=None, json=None, stream=None, timeout=None):
                captured_call["url"] = url
                captured_call["headers"] = headers
                captured_call["json"] = json
                captured_call["stream"] = stream
                captured_call["timeout"] = timeout
                return _FakeResponse()

            def close(self) -> None:
                return None

        with (
            patch.dict(
                os.environ,
                {
                    "VOLCENGINE_APPID": "app-id",
                    "VOLCENGINE_ACCESS_TOKEN": "access-token",
                },
                clear=False,
            ),
            patch("src.agents.experts.speech_synthesis.tool.requests.Session", return_value=_FakeSession()),
        ):
            result = await speech_tool.speech_synthesis_tool(
                user_id="user-1",
                text="hello world",
                voice_name="解说小明",
                audio_format="mp3",
                enable_timestamp=True,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["message"], b"audio-bytes")
        self.assertEqual(result["speaker"], "zh_male_jieshuoxiaoming_uranus_bigtts")
        self.assertEqual(result["voice_name"], "解说小明 2.0")
        self.assertEqual(result["usage"], {"characters": 11})
        self.assertEqual(result["log_id"], "log-123")
        self.assertEqual(captured_call["url"], "https://openspeech.bytedance.com/api/v3/tts/unidirectional")
        self.assertEqual(captured_call["headers"]["X-Api-App-Id"], "app-id")
        self.assertEqual(captured_call["headers"]["X-Api-Resource-Id"], "seed-tts-2.0")
        self.assertEqual(captured_call["json"]["req_params"]["text"], "hello world")
        self.assertEqual(
            captured_call["json"]["req_params"]["speaker"],
            "zh_male_jieshuoxiaoming_uranus_bigtts",
        )
        self.assertTrue(captured_call["json"]["req_params"]["audio_params"]["enable_timestamp"])

    async def test_bytedance_tts_tool_preserves_explicit_legacy_resource(self) -> None:
        encoded_audio = base64.b64encode(b"audio-bytes").decode("utf-8")
        captured_call: dict[str, object] = {}

        class _FakeResponse:
            status_code = 200
            headers = {"X-Tt-Logid": "log-legacy"}
            text = ""

            def iter_lines(self, decode_unicode=True):
                yield json.dumps({"code": 0, "data": encoded_audio})
                yield json.dumps({"code": 20000000, "usage": {"characters": 11}})

            def close(self) -> None:
                return None

        class _FakeSession:
            def post(self, url, headers=None, json=None, stream=None, timeout=None):
                captured_call["headers"] = headers
                captured_call["json"] = json
                return _FakeResponse()

            def close(self) -> None:
                return None

        with (
            patch.dict(
                os.environ,
                {
                    "VOLCENGINE_APPID": "app-id",
                    "VOLCENGINE_ACCESS_TOKEN": "access-token",
                },
                clear=False,
            ),
            patch("src.agents.experts.speech_synthesis.tool.requests.Session", return_value=_FakeSession()),
        ):
            result = await speech_tool.speech_synthesis_tool(
                user_id="user-1",
                text="hello world",
                speaker="demo-speaker",
                resource_id="seed-tts-1.0",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["speaker"], "demo-speaker")
        self.assertEqual(captured_call["headers"]["X-Api-Resource-Id"], "seed-tts-1.0")
        self.assertEqual(captured_call["json"]["req_params"]["speaker"], "demo-speaker")

    async def test_bytedance_tts_tool_rejects_unknown_seed_tts_2_voice(self) -> None:
        result = await speech_tool.speech_synthesis_tool(
            user_id="user-1",
            text="hello world",
            speaker="demo-speaker",
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("Unsupported Seed TTS 2.0 voice", result["message"])
        self.assertIn("demo-speaker", result["message"])


class SpeechSynthesisExpertTests(unittest.IsolatedAsyncioTestCase):
    def test_speech_synthesis_parameters_schema_normalizes_public_contract(self) -> None:
        parameters = SpeechSynthesisParameters.model_validate(
            {
                "text": " hello ",
                "audio_format": "ogg",
                "sample_rate": "48000",
                "language": "zh-CN",
                "enable_timestamp": "true",
            }
        )

        self.assertEqual(parameters.text, "hello")
        self.assertEqual(parameters.audio_format, "mp3")
        self.assertEqual(parameters.sample_rate_value, 48000)
        self.assertEqual(parameters.explicit_language, "zh-CN")
        self.assertTrue(parameters.enable_timestamp)

    def test_speech_synthesis_result_schema_preserves_item_shape(self) -> None:
        result = SpeechSynthesisResultItem.model_validate(
            {
                "output_path": " generated/speech.mp3 ",
                "audio_format": "mp3",
                "usage": {"characters": 5},
                "sentence_count": 1,
            }
        ).to_result()

        self.assertEqual(result["output_path"], "generated/speech.mp3")
        self.assertEqual(result["usage"], {"characters": 5})

    def test_speech_synthesis_output_schema_preserves_error_shape(self) -> None:
        output = SpeechSynthesisOutput(status="error", message="boom")

        self.assertEqual(output.to_current_output(), {"status": "error", "message": "boom"})

    async def test_speech_synthesis_requires_text_or_ssml(self) -> None:
        agent = SpeechSynthesisExpert(name="SpeechSynthesisExpert")
        ctx = _build_ctx({"current_parameters": {}})

        events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("must include: text or ssml", current_output["message"])

    async def test_speech_synthesis_expert_emits_output_file(self) -> None:
        agent = SpeechSynthesisExpert(name="SpeechSynthesisExpert")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "text": "hello world",
                    "audio_format": "wav",
                    "sample_rate": "16000",
                    "voice_name": "解说小明",
                },
                "turn_index": 1,
                "step": 2,
                "expert_step": 3,
            }
        )

        with (
            patch(
                "src.agents.experts.speech_synthesis.speech_synthesis_expert.speech_synthesis_tool",
                new=AsyncMock(
                    return_value={
                        "status": "success",
                        "message": b"audio-bytes",
                        "speaker": "zh_male_jieshuoxiaoming_uranus_bigtts",
                        "voice_name": "解说小明 2.0",
                        "model_name": "seed-tts-2.0",
                        "usage": {"characters": 11},
                        "log_id": "log-1",
                        "sentences": [{"text": "hello world"}],
                        "provider": "bytedance_tts",
                    }
                ),
            ) as tool_mock,
            patch(
                "src.agents.experts.speech_synthesis.speech_synthesis_expert.save_binary_output",
                return_value=Path("/tmp/session_1_speech.wav"),
            ),
            patch(
                "src.agents.experts.speech_synthesis.speech_synthesis_expert.build_workspace_file_record",
                return_value={
                    "name": "session_1_speech.wav",
                    "path": "generated/session_1/session_1_speech.wav",
                    "source": "expert",
                },
            ),
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        tool_mock.assert_awaited_once()
        tool_kwargs = tool_mock.await_args.kwargs
        self.assertEqual(tool_kwargs["text"], "hello world")
        self.assertEqual(tool_kwargs["audio_format"], "wav")
        self.assertEqual(tool_kwargs["sample_rate"], 16000)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "success")
        self.assertEqual(current_output["results"][0]["sentence_count"], 1)
        self.assertEqual(
            events[0].actions.state_delta["speech_synthesis_results"][0]["output_path"],
            "generated/session_1/session_1_speech.wav",
        )


class MusicGenerationToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_music_generation_decodes_hex_audio_and_builds_instrumental_lyrics(self) -> None:
        captured_call: dict[str, object] = {}

        class _FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "base_resp": {"status_code": 0},
                    "data": {"audio": b"music-bytes".hex()},
                }

        def _fake_post(url, headers=None, json=None, timeout=None):
            captured_call["url"] = url
            captured_call["headers"] = headers
            captured_call["json"] = json
            captured_call["timeout"] = timeout
            return _FakeResponse()

        with (
            patch.dict(os.environ, {"MINIMAX_API_KEY": "minimax-key"}, clear=False),
            patch("src.agents.experts.music_generation.tool.requests.post", side_effect=_fake_post),
        ):
            result = await music_tool.music_generation_tool(
                prompt="cinematic orchestral background music",
                instrumental=True,
                audio_format="mp3",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["message"], b"music-bytes")
        self.assertTrue(result["instrumental"])
        self.assertIn("[Inst]", result["lyrics_used"])
        self.assertEqual(captured_call["url"], "https://api.minimax.io/v1/music_generation")
        self.assertEqual(captured_call["headers"]["Authorization"], "Bearer minimax-key")
        self.assertEqual(captured_call["json"]["model"], "music-2.5")
        self.assertEqual(captured_call["json"]["prompt"], "cinematic orchestral background music")


class MusicGenerationExpertTests(unittest.IsolatedAsyncioTestCase):
    def test_music_generation_parameters_schema_normalizes_public_contract(self) -> None:
        parameters = MusicGenerationParameters.model_validate(
            {
                "prompt": " cinematic ",
                "lyrics": " ",
                "audio_format": "aac",
                "sample_rate": "48000",
                "bitrate": "128000",
                "model": "",
            }
        )

        self.assertEqual(parameters.prompt, "cinematic")
        self.assertTrue(parameters.instrumental)
        self.assertEqual(parameters.audio_format, "mp3")
        self.assertEqual(parameters.sample_rate_value, 48000)
        self.assertEqual(parameters.bitrate_value, 128000)
        self.assertEqual(parameters.model_name, "music-2.5")

    def test_music_generation_result_schema_preserves_item_shape(self) -> None:
        result = MusicGenerationResultItem.model_validate(
            {
                "output_path": " generated/music.mp3 ",
                "audio_format": "mp3",
                "instrumental": True,
                "lyrics_used": " [Inst] ",
                "provider": "minimax",
                "model_name": "music-2.5",
            }
        ).to_result()

        self.assertEqual(result["output_path"], "generated/music.mp3")
        self.assertEqual(result["lyrics_used"], "[Inst]")

    def test_music_generation_output_schema_preserves_error_shape(self) -> None:
        output = MusicGenerationOutput(status="error", message="boom")

        self.assertEqual(output.to_current_output(), {"status": "error", "message": "boom"})

    async def test_music_generation_requires_prompt(self) -> None:
        agent = MusicGenerationExpert(name="MusicGenerationExpert")
        ctx = _build_ctx({"current_parameters": {"instrumental": True}})

        events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("must include: prompt", current_output["message"])

    async def test_music_generation_expert_emits_output_file(self) -> None:
        agent = MusicGenerationExpert(name="MusicGenerationExpert")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "prompt": "cinematic orchestral background music",
                    "instrumental": True,
                    "audio_format": "wav",
                    "sample_rate": "48000",
                },
                "turn_index": 1,
                "step": 2,
                "expert_step": 3,
            }
        )

        with (
            patch(
                "src.agents.experts.music_generation.music_generation_expert.music_generation_tool",
                new=AsyncMock(
                    return_value={
                        "status": "success",
                        "message": b"music-bytes",
                        "instrumental": True,
                        "lyrics_used": "[Inst]",
                        "provider": "minimax",
                        "model_name": "music-2.5",
                    }
                ),
            ) as tool_mock,
            patch(
                "src.agents.experts.music_generation.music_generation_expert.save_binary_output",
                return_value=Path("/tmp/session_1_music.wav"),
            ),
            patch(
                "src.agents.experts.music_generation.music_generation_expert.build_workspace_file_record",
                return_value={
                    "name": "session_1_music.wav",
                    "path": "generated/session_1/session_1_music.wav",
                    "source": "expert",
                },
            ),
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        tool_mock.assert_awaited_once()
        tool_kwargs = tool_mock.await_args.kwargs
        self.assertEqual(tool_kwargs["prompt"], "cinematic orchestral background music")
        self.assertEqual(tool_kwargs["audio_format"], "wav")
        self.assertEqual(tool_kwargs["sample_rate"], 48000)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "success")
        self.assertEqual(current_output["results"][0]["lyrics_used"], "[Inst]")
        self.assertEqual(
            events[0].actions.state_delta["music_generation_results"][0]["output_path"],
            "generated/session_1/session_1_music.wav",
        )


if __name__ == "__main__":
    unittest.main()
