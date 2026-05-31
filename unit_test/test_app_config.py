import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from google.adk.models import Gemini, LiteLlm

from conf.app_config import (
    build_default_config,
    get_config_path,
    initialize_runtime_config,
    load_app_config,
    save_app_config,
)
from conf.llm import (
    DEEPSEEK_V4_MODEL_NAMES,
    build_llm,
    get_known_provider_models,
    resolve_llm_model_name,
    resolve_structured_output_mode,
)
from conf.openai_codex import OpenAICodexLlm
from conf.schema import CreativeClawConfig, ProviderConfig


class AppConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dotenv_patch = patch.dict(
            os.environ,
            {"CREATIVE_CLAW_DOTENV_PATH": str(Path(tempfile.gettempdir()) / "creative-claw-test-missing.env")},
            clear=False,
        )
        self.dotenv_patch.start()
        self.addCleanup(self.dotenv_patch.stop)

    def test_initialize_runtime_config_creates_conf_and_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config_path, workspace_path, created = initialize_runtime_config(force=False)

            self.assertTrue(created)
            self.assertEqual(config_path, get_config_path())
            self.assertTrue(config_path.is_file())
            self.assertTrue(workspace_path.is_dir())

            data = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(data["workspace"], str(workspace_path))
            self.assertEqual(data["providers"]["ollama"]["api_base"], "http://localhost:11434/v1")
            self.assertEqual(data["providers"]["openrouter"]["api_base"], "https://openrouter.ai/api/v1")
            self.assertEqual(
                data["providers"]["openai_codex"]["api_base"],
                "https://chatgpt.com/backend-api/codex/responses",
            )
            self.assertEqual(data["providers"]["azure_openai"]["api_version"], "2024-10-21")
            self.assertEqual(data["llm"]["structured_output_mode"], "auto")

    def test_build_default_config_applies_recommended_provider_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = build_default_config()

            self.assertEqual(config.providers.ollama.api_base, "http://localhost:11434/v1")
            self.assertEqual(config.providers.custom.api_base, "https://your-openai-compatible-endpoint/v1")
            self.assertEqual(config.providers.openai_codex.api_base, "https://chatgpt.com/backend-api/codex/responses")
            self.assertEqual(config.providers.deepseek.api_base, "https://api.deepseek.com")
            self.assertEqual(config.providers.azure_openai.api_base, "https://your-resource.openai.azure.com")
            self.assertEqual(config.providers.azure_openai.api_version, "2024-10-21")

    def test_load_app_config_syncs_sdk_environment_variables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(
                workspace=str((get_config_path().parent / "workspace").resolve()),
            )
            config.providers.openai = ProviderConfig(api_key="openai-key")
            config.providers.gemini = ProviderConfig(api_key="google-key")
            config.services.ark_api_key = "ark-key"
            config.services.kling_access_key = "kling-access-key"
            config.services.kling_secret_key = "kling-secret-key"
            config.services.kling_api_base = "https://kling.example.com"
            config.services.volcengine_app_id = "volc-app-id"
            config.services.volcengine_access_token = "volc-access-token"
            config.services.tencentcloud_secret_id = "tc-secret-id"
            config.services.tencentcloud_secret_key = "tc-secret-key"
            config.services.tencentcloud_region = "ap-shanghai"
            save_app_config(config)

            loaded = load_app_config(reload=True)

            self.assertEqual(loaded.providers.openai.api_key, "openai-key")
            self.assertEqual(os.environ["OPENAI_API_KEY"], "openai-key")
            self.assertEqual(os.environ["GOOGLE_API_KEY"], "google-key")
            self.assertEqual(os.environ["ARK_API_KEY"], "ark-key")
            self.assertEqual(os.environ["KLING_ACCESS_KEY"], "kling-access-key")
            self.assertEqual(os.environ["KLING_SECRET_KEY"], "kling-secret-key")
            self.assertEqual(os.environ["KLING_API_BASE"], "https://kling.example.com")
            self.assertEqual(os.environ["VOLCENGINE_APPID"], "volc-app-id")
            self.assertEqual(os.environ["VOLCENGINE_ACCESS_TOKEN"], "volc-access-token")
            self.assertEqual(os.environ["TENCENTCLOUD_SECRET_ID"], "tc-secret-id")
            self.assertEqual(os.environ["TENCENTCLOUD_SECRET_KEY"], "tc-secret-key")
            self.assertEqual(os.environ["TENCENTCLOUD_REGION"], "ap-shanghai")

    def test_load_app_config_falls_back_to_environment_for_empty_api_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {
                "CREATIVE_CLAW_HOME": tmp_dir,
                "OPENAI_API_KEY": "env-openai-key",
                "GOOGLE_API_KEY": "env-google-key",
                "ARK_API_KEY": "env-ark-key",
                "KLING_ACCESS_KEY": "env-kling-access-key",
                "KLING_SECRET_KEY": "env-kling-secret-key",
                "KLING_API_BASE": "https://env-kling.example.com",
                "VOLCENGINE_APPID": "env-volc-app-id",
                "VOLCENGINE_ACCESS_TOKEN": "env-volc-access-token",
                "TENCENTCLOUD_SECRET_ID": "env-tc-secret-id",
                "TENCENTCLOUD_SECRET_KEY": "env-tc-secret-key",
                "TENCENTCLOUD_REGION": "ap-shanghai",
            },
            clear=False,
        ):
            config = CreativeClawConfig(
                workspace=str((get_config_path().parent / "workspace").resolve()),
            )
            save_app_config(config)

            loaded = load_app_config(reload=True)

            self.assertEqual(loaded.providers.openai.api_key, "env-openai-key")
            self.assertEqual(loaded.providers.gemini.api_key, "env-google-key")
            self.assertEqual(loaded.services.ark_api_key, "env-ark-key")
            self.assertEqual(loaded.services.kling_access_key, "env-kling-access-key")
            self.assertEqual(loaded.services.kling_secret_key, "env-kling-secret-key")
            self.assertEqual(loaded.services.kling_api_base, "https://env-kling.example.com")
            self.assertEqual(loaded.services.volcengine_app_id, "env-volc-app-id")
            self.assertEqual(loaded.services.volcengine_access_token, "env-volc-access-token")
            self.assertEqual(loaded.services.tencentcloud_secret_id, "env-tc-secret-id")
            self.assertEqual(loaded.services.tencentcloud_secret_key, "env-tc-secret-key")
            self.assertEqual(loaded.services.tencentcloud_region, "ap-shanghai")

    def test_load_app_config_prefers_conf_json_over_environment_for_api_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {
                "CREATIVE_CLAW_HOME": tmp_dir,
                "OPENAI_API_KEY": "env-openai-key",
            },
            clear=False,
        ):
            config = CreativeClawConfig(
                workspace=str((get_config_path().parent / "workspace").resolve()),
            )
            config.providers.openai = ProviderConfig(api_key="conf-openai-key")
            save_app_config(config)

            loaded = load_app_config(reload=True)

            self.assertEqual(loaded.providers.openai.api_key, "conf-openai-key")

    def test_load_app_config_loads_dotenv_before_environment_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, tempfile.TemporaryDirectory() as dotenv_dir:
            dotenv_path = Path(dotenv_dir) / ".env"
            dotenv_path.write_text(
                "\n".join(
                    [
                        'OPENAI_API_KEY="dotenv-openai-key"',
                        'DOC2X_API_KEY="dotenv-doc2x-key"',
                        "SERPER_API_KEY=dotenv-serper-key # local search key",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "CREATIVE_CLAW_HOME": tmp_dir,
                    "CREATIVE_CLAW_DOTENV_PATH": str(dotenv_path),
                },
                clear=True,
            ):
                config = CreativeClawConfig(
                    workspace=str((get_config_path().parent / "workspace").resolve()),
                )
                save_app_config(config)

                loaded = load_app_config(reload=True)

                self.assertEqual(loaded.providers.openai.api_key, "dotenv-openai-key")
                self.assertEqual(loaded.services.serper_api_key, "dotenv-serper-key")
                self.assertEqual(os.environ["OPENAI_API_KEY"], "dotenv-openai-key")
                self.assertEqual(os.environ["SERPER_API_KEY"], "dotenv-serper-key")
                self.assertEqual(os.environ["DOC2X_API_KEY"], "dotenv-doc2x-key")

    def test_load_app_config_does_not_override_existing_environment_with_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, tempfile.TemporaryDirectory() as dotenv_dir:
            dotenv_path = Path(dotenv_dir) / ".env"
            dotenv_path.write_text(
                "\n".join(
                    [
                        "OPENAI_API_KEY=dotenv-openai-key",
                        "DOC2X_API_KEY=dotenv-doc2x-key",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "CREATIVE_CLAW_HOME": tmp_dir,
                    "CREATIVE_CLAW_DOTENV_PATH": str(dotenv_path),
                    "OPENAI_API_KEY": "shell-openai-key",
                    "DOC2X_API_KEY": "shell-doc2x-key",
                },
                clear=True,
            ):
                config = CreativeClawConfig(
                    workspace=str((get_config_path().parent / "workspace").resolve()),
                )
                save_app_config(config)

                loaded = load_app_config(reload=True)

                self.assertEqual(loaded.providers.openai.api_key, "shell-openai-key")
                self.assertEqual(os.environ["OPENAI_API_KEY"], "shell-openai-key")
                self.assertEqual(os.environ["DOC2X_API_KEY"], "shell-doc2x-key")

    def test_build_llm_returns_litellm_for_openai(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.llm.provider = "openai"
            config.llm.model = "gpt-5.4"
            config.providers.openai = ProviderConfig(api_key="openai-key")
            save_app_config(config)
            load_app_config(reload=True)

            llm = build_llm()

            self.assertIsInstance(llm, LiteLlm)
            self.assertEqual(llm.model, "openai/gpt-5.4")
            self.assertEqual(resolve_llm_model_name(), "openai/gpt-5.4")

    def test_build_llm_returns_codex_oauth_model_for_openai_codex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.llm.provider = "openai_codex"
            config.llm.model = "gpt-5.5"
            save_app_config(config)
            load_app_config(reload=True)

            llm = build_llm()

            self.assertIsInstance(llm, OpenAICodexLlm)
            self.assertEqual(llm.model, "openai_codex/gpt-5.5")
            self.assertEqual(llm.api_base, "https://chatgpt.com/backend-api/codex/responses")
            self.assertEqual(resolve_llm_model_name(), "openai_codex/gpt-5.5")

    def test_build_llm_resolves_openai_codex_model_reference_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.llm.provider = "openai"
            config.llm.model = "gpt-5.4"
            save_app_config(config)
            load_app_config(reload=True)

            llm = build_llm("openai-codex/gpt-5.5")

            self.assertIsInstance(llm, OpenAICodexLlm)
            self.assertEqual(llm.model, "openai_codex/gpt-5.5")
            self.assertEqual(resolve_llm_model_name("openai-codex/gpt-5.5"), "openai_codex/gpt-5.5")

    def test_build_llm_uses_litellm_for_deepseek_v4_pro(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.llm.provider = "deepseek"
            config.llm.model = "deepseek-v4-pro"
            config.providers.deepseek = ProviderConfig(api_key="deepseek-key")
            save_app_config(config)
            load_app_config(reload=True)

            with patch("conf.llm.LiteLlm", return_value=object()) as mocked_litellm:
                build_llm()

            mocked_litellm.assert_called_once_with(
                model="deepseek/deepseek-v4-pro",
                api_key="deepseek-key",
                api_base="https://api.deepseek.com",
            )
            self.assertEqual(resolve_llm_model_name(), "deepseek/deepseek-v4-pro")
            self.assertIn("deepseek-v4-pro", DEEPSEEK_V4_MODEL_NAMES)
            self.assertEqual(resolve_structured_output_mode(), "prompt_json")

    def test_build_llm_resolves_deepseek_v4_flash_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.llm.provider = "openai"
            config.llm.model = "gpt-5.4"
            config.providers.deepseek = ProviderConfig(api_key="deepseek-key")
            save_app_config(config)
            load_app_config(reload=True)

            with patch("conf.llm.LiteLlm", return_value=object()) as mocked_litellm:
                build_llm("deepseek/deepseek-v4-flash")

            mocked_litellm.assert_called_once_with(
                model="deepseek/deepseek-v4-flash",
                api_key="deepseek-key",
                api_base="https://api.deepseek.com",
            )
            self.assertEqual(
                resolve_llm_model_name("deepseek/deepseek-v4-flash"),
                "deepseek/deepseek-v4-flash",
            )
            self.assertIn("deepseek-v4-flash", get_known_provider_models("deepseek"))

    def test_structured_output_mode_auto_keeps_native_for_openai(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.llm.provider = "openai"
            config.llm.model = "gpt-5.4"
            save_app_config(config)
            load_app_config(reload=True)

            self.assertEqual(resolve_structured_output_mode(), "native")

    def test_structured_output_mode_auto_keeps_native_for_gemini(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.llm.provider = "gemini"
            config.llm.model = "gemini-2.5-flash"
            save_app_config(config)
            load_app_config(reload=True)

            self.assertEqual(resolve_structured_output_mode(), "native")

    def test_structured_output_mode_can_force_prompt_json_for_native_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.llm.provider = "openai"
            config.llm.model = "gpt-5.4"
            config.llm.structured_output_mode = "prompt_json"
            save_app_config(config)
            load_app_config(reload=True)

            self.assertEqual(resolve_structured_output_mode(), "prompt_json")

    def test_structured_output_mode_can_force_native(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.llm.provider = "deepseek"
            config.llm.model = "deepseek-v4-pro"
            config.llm.structured_output_mode = "native"
            save_app_config(config)
            load_app_config(reload=True)

            self.assertEqual(resolve_structured_output_mode(), "native")

    def test_build_llm_returns_gemini_for_gemini_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.llm.provider = "gemini"
            config.llm.model = "gemini-2.5-flash"
            config.providers.gemini = ProviderConfig(api_key="google-key")
            save_app_config(config)
            load_app_config(reload=True)

            llm = build_llm()

            self.assertIsInstance(llm, Gemini)
            self.assertEqual(llm.model, "gemini-2.5-flash")

    def test_build_llm_uses_default_api_base_for_openrouter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.llm.provider = "openrouter"
            config.llm.model = "openai/gpt-4.1-mini"
            config.providers.openrouter = ProviderConfig(api_key="router-key")
            save_app_config(config)
            load_app_config(reload=True)

            with patch("conf.llm.LiteLlm", return_value=object()) as mocked_litellm:
                build_llm()

            mocked_litellm.assert_called_once_with(
                model="openrouter/openai/gpt-4.1-mini",
                api_key="router-key",
                api_base="https://openrouter.ai/api/v1",
            )

    def test_build_llm_uses_custom_api_base_for_custom_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.llm.provider = "custom"
            config.llm.model = "my-model"
            config.providers.custom = ProviderConfig(
                api_key="custom-key",
                api_base="https://llm.example.com/v1",
                extra_headers={"X-Test": "demo"},
            )
            save_app_config(config)
            load_app_config(reload=True)

            with patch("conf.llm.LiteLlm", return_value=object()) as mocked_litellm:
                build_llm()

            mocked_litellm.assert_called_once_with(
                model="my-model",
                api_key="custom-key",
                api_base="https://llm.example.com/v1",
                extra_headers={"X-Test": "demo"},
                custom_llm_provider="openai",
            )

    def test_build_llm_uses_configured_api_version_for_azure_openai(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.llm.provider = "azure_openai"
            config.llm.model = "gpt-4.1"
            config.providers.azure_openai = ProviderConfig(
                api_key="azure-key",
                api_base="https://demo-resource.openai.azure.com",
                api_version="2025-01-01-preview",
            )
            save_app_config(config)
            load_app_config(reload=True)

            with patch("conf.llm.LiteLlm", return_value=object()) as mocked_litellm:
                build_llm()

            mocked_litellm.assert_called_once_with(
                model="azure/gpt-4.1",
                api_key="azure-key",
                api_base="https://demo-resource.openai.azure.com",
                api_version="2025-01-01-preview",
            )

    def test_build_llm_uses_default_api_version_for_azure_openai_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.llm.provider = "azure_openai"
            config.llm.model = "gpt-4.1-mini"
            config.providers.azure_openai = ProviderConfig(
                api_key="azure-key",
                api_base="https://demo-resource.openai.azure.com",
            )
            save_app_config(config)
            load_app_config(reload=True)

            with patch("conf.llm.LiteLlm", return_value=object()) as mocked_litellm:
                build_llm()

            mocked_litellm.assert_called_once_with(
                model="azure/gpt-4.1-mini",
                api_key="azure-key",
                api_base="https://demo-resource.openai.azure.com",
                api_version="2024-10-21",
            )
