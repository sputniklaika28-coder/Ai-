"""test_image_generator_addon.py — 画像生成アドオンのユニットテスト。

ComfyUI API はモックで差し替え、アドオンの統合ロジックを検証する。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

from image_generator.addon import (
    IMAGE_STYLES,
    IMAGE_TOOLS,
    ImageGeneratorAddon,
)
from image_generator.comfyui_client import (
    ComfyUIClient,
    ComfyUIConfig,
    GenerationResult,
)
from core.addons.addon_base import AddonContext, ToolExecutionContext


# ──────────────────────────────────────
# フィクスチャ
# ──────────────────────────────────────


@pytest.fixture
def addon(tmp_path: Path) -> ImageGeneratorAddon:
    """テスト用 ImageGeneratorAddon。"""
    a = ImageGeneratorAddon()
    a.addon_dir = tmp_path / "addons" / "image_generator"
    a.addon_dir.mkdir(parents=True, exist_ok=True)
    ctx = AddonContext(
        adapter=None,
        lm_client=MagicMock(),
        knowledge_manager=None,
        session_manager=MagicMock(),
        character_manager=MagicMock(),
        root_dir=tmp_path,
    )
    a.on_load(ctx)
    return a


@pytest.fixture
def tool_ctx() -> ToolExecutionContext:
    """テスト用 ToolExecutionContext。"""
    connector = MagicMock()
    connector._post_system_message = MagicMock()
    return ToolExecutionContext(
        char_name="テストGM",
        tool_call_id="call_001",
        adapter=MagicMock(),
        connector=connector,
    )


# ──────────────────────────────────────
# アドオン基本テスト
# ──────────────────────────────────────


class TestImageGeneratorAddonBasics:
    def test_get_tools_returns_three(self, addon):
        tools = addon.get_tools()
        assert len(tools) == 3
        names = [t["function"]["name"] for t in tools]
        assert "generate_image" in names
        assert "generate_scene_background" in names
        assert "list_image_styles" in names

    def test_on_load_creates_output_dir(self, addon, tmp_path):
        assert (tmp_path / "generated_images").is_dir()

    def test_on_unload_clears_client(self, addon):
        addon._client = MagicMock()
        addon.on_unload()
        assert addon._client is None

    def test_list_styles_tool(self, addon, tool_ctx):
        finished, result = addon.execute_tool("list_image_styles", {}, tool_ctx)
        assert not finished
        data = json.loads(result)
        assert "styles" in data
        assert len(data["styles"]) == len(IMAGE_STYLES)
        style_ids = [s["id"] for s in data["styles"]]
        assert "fantasy_landscape" in style_ids
        assert "tactical_map" in style_ids

    def test_unknown_tool_returns_error(self, addon, tool_ctx):
        finished, result = addon.execute_tool("unknown_tool", {}, tool_ctx)
        data = json.loads(result)
        assert "error" in data


# ──────────────────────────────────────
# generate_image テスト
# ──────────────────────────────────────


class TestGenerateImage:
    @patch("image_generator.addon.ImageGeneratorAddon._get_client")
    def test_successful_generation(self, mock_get_client, addon, tool_ctx):
        mock_client = MagicMock()
        mock_client.generate.return_value = GenerationResult(
            success=True,
            image_path="/tmp/test/trpg_gen_00001.png",
            prompt_id="abc-123",
            elapsed_seconds=5.2,
        )
        mock_get_client.return_value = mock_client

        finished, result = addon.execute_tool(
            "generate_image",
            {"prompt": "a dark castle entrance"},
            tool_ctx,
        )
        assert not finished
        data = json.loads(result)
        assert data["success"] is True
        assert data["image_path"] == "/tmp/test/trpg_gen_00001.png"
        assert data["elapsed_seconds"] == 5.2
        assert "hint" in data

    @patch("image_generator.addon.ImageGeneratorAddon._get_client")
    def test_generation_with_style(self, mock_get_client, addon, tool_ctx):
        mock_client = MagicMock()
        mock_client.generate.return_value = GenerationResult(
            success=True, image_path="/tmp/out.png", prompt_id="x", elapsed_seconds=3.0,
        )
        mock_get_client.return_value = mock_client

        addon.execute_tool(
            "generate_image",
            {"prompt": "a wizard tower", "style": "fantasy_landscape"},
            tool_ctx,
        )

        call_args = mock_client.generate.call_args
        prompt_sent = call_args.kwargs.get("prompt", call_args[1].get("prompt", ""))
        assert "fantasy art" in prompt_sent or "artstation" in prompt_sent

    @patch("image_generator.addon.ImageGeneratorAddon._get_client")
    def test_generation_failure(self, mock_get_client, addon, tool_ctx):
        mock_client = MagicMock()
        mock_client.generate.return_value = GenerationResult(
            success=False, error="ComfyUI サーバーに接続できません",
        )
        mock_get_client.return_value = mock_client

        finished, result = addon.execute_tool(
            "generate_image",
            {"prompt": "test"},
            tool_ctx,
        )
        data = json.loads(result)
        assert data["success"] is False
        assert "接続" in data["error"]

    @patch("image_generator.addon.ImageGeneratorAddon._get_client")
    def test_posts_system_message_on_start(self, mock_get_client, addon, tool_ctx):
        mock_client = MagicMock()
        mock_client.generate.return_value = GenerationResult(
            success=True, image_path="/tmp/x.png", prompt_id="p", elapsed_seconds=1.0,
        )
        mock_get_client.return_value = mock_client

        addon.execute_tool("generate_image", {"prompt": "test"}, tool_ctx)
        tool_ctx.connector._post_system_message.assert_called()


# ──────────────────────────────────────
# generate_scene_background テスト
# ──────────────────────────────────────


class TestGenerateSceneBackground:
    @patch("image_generator.addon.ImageGeneratorAddon._get_client")
    def test_scene_background_success(self, mock_get_client, addon, tool_ctx):
        mock_client = MagicMock()
        mock_client.generate.return_value = GenerationResult(
            success=True, image_path="/tmp/bg.png", prompt_id="bg1", elapsed_seconds=8.0,
        )
        mock_get_client.return_value = mock_client

        finished, result = addon.execute_tool(
            "generate_scene_background",
            {"scene_description": "薄暗い洞窟の入り口", "mood": "dark"},
            tool_ctx,
        )
        data = json.loads(result)
        assert data["success"] is True
        assert "generated_prompt" in data
        assert "hint" in data

        # 1920x1080 で生成
        call_args = mock_client.generate.call_args
        assert call_args.kwargs.get("width", call_args[1].get("width")) == 1920
        assert call_args.kwargs.get("height", call_args[1].get("height")) == 1080

    @patch("image_generator.addon.ImageGeneratorAddon._get_client")
    def test_scene_background_failure(self, mock_get_client, addon, tool_ctx):
        mock_client = MagicMock()
        mock_client.generate.return_value = GenerationResult(
            success=False, error="timeout",
        )
        mock_get_client.return_value = mock_client

        finished, result = addon.execute_tool(
            "generate_scene_background",
            {"scene_description": "test"},
            tool_ctx,
        )
        data = json.loads(result)
        assert data["success"] is False


# ──────────────────────────────────────
# ComfyUIClient テスト
# ──────────────────────────────────────


class TestComfyUIClient:
    def test_config_defaults(self):
        config = ComfyUIConfig()
        assert config.host == "127.0.0.1"
        assert config.port == 8188
        assert config.base_url == "http://127.0.0.1:8188"

    def test_build_workflow_prompt(self):
        client = ComfyUIClient(ComfyUIConfig())
        wf = client.build_workflow(prompt="a castle", seed=42)
        assert wf["6"]["inputs"]["text"] == "a castle"
        assert wf["3"]["inputs"]["seed"] == 42

    def test_build_workflow_dimensions(self):
        client = ComfyUIClient(ComfyUIConfig())
        wf = client.build_workflow(prompt="test", width=512, height=768)
        assert wf["5"]["inputs"]["width"] == 512
        assert wf["5"]["inputs"]["height"] == 768

    def test_build_workflow_custom_checkpoint(self):
        client = ComfyUIClient(ComfyUIConfig())
        wf = client.build_workflow(prompt="test", checkpoint="my_model.safetensors")
        assert wf["4"]["inputs"]["ckpt_name"] == "my_model.safetensors"

    def test_build_workflow_negative_prompt(self):
        client = ComfyUIClient(ComfyUIConfig())
        wf = client.build_workflow(prompt="test", negative_prompt="ugly, bad")
        assert wf["7"]["inputs"]["text"] == "ugly, bad"

    @patch("requests.get")
    def test_is_available_true(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200)
        client = ComfyUIClient(ComfyUIConfig())
        assert client.is_available() is True

    @patch("requests.get")
    def test_is_available_false_on_connection_error(self, mock_get):
        import requests as req
        mock_get.side_effect = req.ConnectionError()
        client = ComfyUIClient(ComfyUIConfig())
        assert client.is_available() is False

    @patch("requests.post")
    def test_queue_prompt(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"prompt_id": "test-prompt-123"}),
        )
        mock_post.return_value.raise_for_status = MagicMock()
        client = ComfyUIClient(ComfyUIConfig())
        pid = client.queue_prompt({"test": "workflow"})
        assert pid == "test-prompt-123"

    @patch("requests.post")
    def test_queue_prompt_raises_on_missing_id(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={}),
        )
        mock_post.return_value.raise_for_status = MagicMock()
        client = ComfyUIClient(ComfyUIConfig())
        with pytest.raises(RuntimeError, match="prompt_id"):
            client.queue_prompt({"test": "workflow"})

    def test_get_images_from_history(self):
        client = ComfyUIClient(ComfyUIConfig())
        history = {
            "outputs": {
                "9": {
                    "images": [
                        {"filename": "trpg_gen_00001.png", "subfolder": "", "type": "output"},
                    ]
                }
            }
        }
        images = client.get_images_from_history(history)
        assert len(images) == 1
        assert images[0]["filename"] == "trpg_gen_00001.png"

    @patch("requests.get")
    def test_download_image(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            content=b"\x89PNG fake image data",
        )
        mock_get.return_value.raise_for_status = MagicMock()
        client = ComfyUIClient(ComfyUIConfig())
        data = client.download_image("test.png")
        assert data == b"\x89PNG fake image data"

    def test_save_image(self, tmp_path):
        client = ComfyUIClient(ComfyUIConfig())
        img_data = b"\x89PNG test"
        path = client.save_image(img_data, tmp_path / "subdir" / "test.png")
        assert path.exists()
        assert path.read_bytes() == img_data

    @patch("requests.get")
    def test_generate_unavailable_server(self, mock_get):
        """サーバー未起動時のフォールバック。"""
        import requests as req
        mock_get.side_effect = req.ConnectionError()
        client = ComfyUIClient(ComfyUIConfig())
        result = client.generate(prompt="test")
        assert result.success is False
        assert "接続" in result.error


# ──────────────────────────────────────
# スタイルプリセット テスト
# ──────────────────────────────────────


class TestImageStyles:
    def test_all_styles_have_required_keys(self):
        for key, style in IMAGE_STYLES.items():
            assert "name" in style, f"{key}: name が不足"
            assert "suffix" in style, f"{key}: suffix が不足"
            assert "negative" in style, f"{key}: negative が不足"

    def test_style_count(self):
        assert len(IMAGE_STYLES) >= 5

    def test_build_scene_prompt_with_mood(self):
        prompt = ImageGeneratorAddon._build_scene_prompt("洞窟の中", "dark")
        assert "dark" in prompt.lower() or "dim" in prompt.lower()
        assert "concept art" in prompt.lower() or "4k" in prompt.lower()

    def test_build_scene_prompt_without_mood(self):
        prompt = ImageGeneratorAddon._build_scene_prompt("草原")
        assert "fantasy scene" in prompt.lower()


# ──────────────────────────────────────
# ツール定義の整合性テスト
# ──────────────────────────────────────


class TestToolDefinitions:
    def test_all_tools_have_function_schema(self):
        for tool in IMAGE_TOOLS:
            assert tool["type"] == "function"
            assert "name" in tool["function"]
            assert "description" in tool["function"]
            assert "parameters" in tool["function"]

    def test_generate_image_requires_prompt(self):
        gen_tool = next(t for t in IMAGE_TOOLS if t["function"]["name"] == "generate_image")
        assert "prompt" in gen_tool["function"]["parameters"]["required"]

    def test_scene_bg_requires_scene_description(self):
        bg_tool = next(
            t for t in IMAGE_TOOLS if t["function"]["name"] == "generate_scene_background"
        )
        assert "scene_description" in bg_tool["function"]["parameters"]["required"]
