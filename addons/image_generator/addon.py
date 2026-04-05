"""画像生成ツールアドオン。

ComfyUI と連携してシーン背景・キャラクターイラスト・マップを AI 生成する。
ルームビルダーやアセットアップローダーと組み合わせ、生成画像を
CCFolia ルームに直接反映できる。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from core.addons.addon_base import AddonContext, ToolAddon, ToolExecutionContext

logger = logging.getLogger(__name__)

# ──────────────────────────────────────
# プリセットスタイル
# ──────────────────────────────────────

IMAGE_STYLES: dict[str, dict[str, str]] = {
    "fantasy_landscape": {
        "name": "ファンタジー風景",
        "suffix": ", fantasy art, detailed background, dramatic lighting, concept art, "
                  "high quality, 4k, artstation",
        "negative": "photo, realistic, modern, urban, text, watermark, low quality",
    },
    "dark_gothic": {
        "name": "ダークゴシック",
        "suffix": ", dark gothic style, moody atmosphere, dim lighting, oil painting, "
                  "detailed, dramatic shadows",
        "negative": "bright, cheerful, cartoon, chibi, text, watermark, low quality",
    },
    "anime_character": {
        "name": "アニメキャラクター",
        "suffix": ", anime style, character portrait, detailed face, "
                  "illustration, high quality, vibrant colors",
        "negative": "photo, realistic, 3d render, deformed, bad anatomy, text, watermark",
    },
    "tactical_map": {
        "name": "戦術マップ",
        "suffix": ", top-down tactical map, grid overlay, fantasy dungeon, "
                  "detailed floor plan, game asset, clean lines",
        "negative": "perspective view, character, portrait, blurry, text, watermark",
    },
    "watercolor": {
        "name": "水彩画風",
        "suffix": ", watercolor painting, soft edges, artistic, "
                  "pastel colors, traditional media, beautiful composition",
        "negative": "photo, digital art, sharp lines, text, watermark, low quality",
    },
}

# ──────────────────────────────────────
# ツール定義
# ──────────────────────────────────────

IMAGE_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "ComfyUIを使って画像を生成する。シーンの背景画像、キャラクターイラスト、"
                "マップなどを自然言語プロンプトから生成できる。"
                "生成した画像はローカルに保存され、upload_asset ツールで CCFolia にアップロード可能。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "生成したい画像の説明（英語推奨）",
                    },
                    "negative_prompt": {
                        "type": "string",
                        "description": "除外したい要素（省略可）",
                    },
                    "style": {
                        "type": "string",
                        "description": (
                            "プリセットスタイル: fantasy_landscape, dark_gothic, "
                            "anime_character, tactical_map, watercolor（省略可）"
                        ),
                    },
                    "width": {
                        "type": "integer",
                        "description": "画像幅（デフォルト1024）",
                    },
                    "height": {
                        "type": "integer",
                        "description": "画像高さ（デフォルト1024）",
                    },
                    "steps": {
                        "type": "integer",
                        "description": "サンプリングステップ数（デフォルト20）",
                    },
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_scene_background",
            "description": (
                "現在のシーンに合わせた背景画像を生成し、自動的にルーム背景として設定する。"
                "シーンの雰囲気を自然言語で指定するだけで、適切なスタイルが自動選択される。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_description": {
                        "type": "string",
                        "description": "シーンの状況説明（日本語OK、内部で英語プロンプトに変換）",
                    },
                    "mood": {
                        "type": "string",
                        "description": "雰囲気: bright, dark, mysterious, peaceful, tense, epic",
                    },
                },
                "required": ["scene_description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_image_styles",
            "description": "利用可能な画像生成プリセットスタイル一覧を返す",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class ImageGeneratorAddon(ToolAddon):
    """ComfyUI 連携画像生成アドオン。"""

    def __init__(self) -> None:
        self._context: AddonContext | None = None
        self._client: Any = None  # ComfyUIClient (遅延初期化)
        self._output_dir: Path | None = None

    def on_load(self, context: AddonContext) -> None:
        self._context = context
        self._output_dir = context.root_dir / "generated_images"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        logger.info("画像生成アドオンをロードしました (output_dir=%s)", self._output_dir)

    def on_unload(self) -> None:
        self._client = None
        logger.info("画像生成アドオンをアンロードしました")

    def _get_client(self):
        """ComfyUIClient を遅延初期化して返す。"""
        if self._client is None:
            from .comfyui_client import ComfyUIClient, ComfyUIConfig
            self._client = ComfyUIClient(ComfyUIConfig())
        return self._client

    def get_tools(self) -> list[dict]:
        return IMAGE_TOOLS

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        if tool_name == "generate_image":
            return self._handle_generate_image(tool_args, context)
        if tool_name == "generate_scene_background":
            return self._handle_generate_scene_background(tool_args, context)
        if tool_name == "list_image_styles":
            return self._handle_list_styles()
        return False, json.dumps({"error": f"未対応ツール: {tool_name}"}, ensure_ascii=False)

    # ──────────────────────────────────────
    # ツール実装
    # ──────────────────────────────────────

    def _handle_generate_image(
        self, args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        """generate_image ツールの実行。"""
        client = self._get_client()
        prompt_text = args.get("prompt", "")
        negative = args.get("negative_prompt", "")
        style_key = args.get("style", "")
        width = args.get("width")
        height = args.get("height")
        steps = args.get("steps")

        # スタイルプリセット適用
        if style_key and style_key in IMAGE_STYLES:
            style = IMAGE_STYLES[style_key]
            prompt_text += style["suffix"]
            if not negative:
                negative = style["negative"]

        if not negative:
            negative = "low quality, blurry, deformed, watermark, text"

        # システムメッセージで進捗通知
        connector = context.connector
        if hasattr(connector, "_post_system_message"):
            connector._post_system_message(
                context.char_name,
                "🎨 画像を生成中... (ComfyUI)",
            )

        result = client.generate(
            prompt=prompt_text,
            negative_prompt=negative,
            width=width,
            height=height,
            steps=steps,
            output_dir=self._output_dir,
        )

        if result.success:
            response = {
                "success": True,
                "image_path": result.image_path,
                "prompt_id": result.prompt_id,
                "elapsed_seconds": round(result.elapsed_seconds, 1),
                "hint": "upload_asset ツールでこの画像を CCFolia にアップロードできます",
            }
            if hasattr(connector, "_post_system_message"):
                connector._post_system_message(
                    context.char_name,
                    f"🎨 画像生成完了 ({result.elapsed_seconds:.1f}秒): {result.image_path}",
                )
        else:
            response = {
                "success": False,
                "error": result.error,
            }

        return False, json.dumps(response, ensure_ascii=False)

    def _handle_generate_scene_background(
        self, args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        """generate_scene_background ツールの実行。

        シーン説明からプロンプトを構築し、背景画像を生成 → ルーム設定。
        """
        client = self._get_client()
        scene_desc = args.get("scene_description", "")
        mood = args.get("mood", "")

        # シーン→プロンプト変換
        prompt_text = self._build_scene_prompt(scene_desc, mood)
        negative = "text, watermark, low quality, blurry, deformed, modern objects, UI elements"

        connector = context.connector
        if hasattr(connector, "_post_system_message"):
            connector._post_system_message(
                context.char_name,
                f"🎨 シーン背景を生成中: {scene_desc[:30]}...",
            )

        result = client.generate(
            prompt=prompt_text,
            negative_prompt=negative,
            width=1920,
            height=1080,
            output_dir=self._output_dir,
        )

        if not result.success:
            return False, json.dumps({
                "success": False,
                "error": result.error,
            }, ensure_ascii=False)

        # asset_uploader が利用可能なら背景設定を案内
        response: dict[str, Any] = {
            "success": True,
            "image_path": result.image_path,
            "elapsed_seconds": round(result.elapsed_seconds, 1),
            "generated_prompt": prompt_text,
            "hint": (
                "set_room_background ツールでこの画像をルーム背景に設定できます。"
                f"パス: {result.image_path}"
            ),
        }

        if hasattr(connector, "_post_system_message"):
            connector._post_system_message(
                context.char_name,
                f"🎨 背景画像生成完了 ({result.elapsed_seconds:.1f}秒)",
            )

        return False, json.dumps(response, ensure_ascii=False)

    def _handle_list_styles(self) -> tuple[bool, str | None]:
        """list_image_styles ツールの実行。"""
        styles = []
        for key, style in IMAGE_STYLES.items():
            styles.append({
                "id": key,
                "name": style["name"],
            })
        return False, json.dumps({"styles": styles}, ensure_ascii=False)

    # ──────────────────────────────────────
    # ヘルパー
    # ──────────────────────────────────────

    @staticmethod
    def _build_scene_prompt(scene_description: str, mood: str = "") -> str:
        """シーン説明 (日本語可) から英語ベースのプロンプトを構築。

        NOTE: 本格運用では LLM を使って日→英翻訳するが、
        ここでは簡易的にキーワードマッピングで構築する。
        """
        base = f"{scene_description}, fantasy scene background"

        mood_map = {
            "bright": "bright sunlight, warm colors, cheerful atmosphere",
            "dark": "dark atmosphere, dim lighting, ominous shadows",
            "mysterious": "mysterious atmosphere, fog, ethereal glow, magical",
            "peaceful": "peaceful scene, calm, serene, soft lighting",
            "tense": "tense atmosphere, dramatic lighting, high contrast",
            "epic": "epic scale, grand, cinematic composition, dramatic sky",
        }

        if mood and mood in mood_map:
            base += f", {mood_map[mood]}"

        base += (
            ", detailed environment, concept art, "
            "high quality, 4k, artstation, digital painting"
        )
        return base
