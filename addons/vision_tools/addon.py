"""Vision盤面解析 ツールアドオン。

VLMによる盤面の視覚的解析、自然言語ベースの駒配置、シーン描写機能を提供する。
既存の core/vision_canvas_controller.py をラップする。
"""

from __future__ import annotations

import json
import logging

from core.addons.addon_base import AddonContext, ToolAddon, ToolExecutionContext

logger = logging.getLogger(__name__)

VISION_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "analyze_board_vision",
            "description": "VLMで盤面を視覚的に解析し、Canvas上の駒や地形を検出する",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "解析の焦点（省略可）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "place_piece_at_location",
            "description": "自然言語で指定した位置にコマを配置する（VLMで座標を特定）",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "配置位置の説明（例: 十字路、部屋の中央）",
                    },
                    "character_json": {
                        "type": "object",
                        "description": "CCFolia形式のキャラクターデータ",
                    },
                },
                "required": ["description", "character_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_board_scene",
            "description": "VLMで現在の盤面状態を自然言語で説明する",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class VisionToolsAddon(ToolAddon):
    """Vision盤面解析 ツールアドオン。"""

    def on_load(self, context: AddonContext) -> None:
        self._context = context
        logger.info("Vision盤面解析アドオンをロードしました")

    def get_tools(self) -> list[dict]:
        return VISION_TOOLS

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        adapter = context.adapter

        if tool_name == "analyze_board_vision" and adapter:
            try:
                vision = adapter.get_vision_controller()
                pieces = vision.analyze_board(tool_args.get("query", ""))
                return False, json.dumps(pieces, ensure_ascii=False, default=str)
            except (NotImplementedError, AttributeError):
                return False, json.dumps({"error": "Vision 機能が利用できません"})

        if tool_name == "place_piece_at_location" and adapter:
            try:
                vision = adapter.get_vision_controller()
                ok = vision.place_piece_at_visual_location(
                    tool_args.get("description", ""),
                    tool_args.get("character_json", {}),
                )
                return False, json.dumps({"ok": ok})
            except (NotImplementedError, AttributeError):
                return False, json.dumps({"error": "Vision 機能が利用できません"})

        if tool_name == "describe_board_scene" and adapter:
            try:
                vision = adapter.get_vision_controller()
                desc = vision.describe_scene()
                return False, json.dumps({"description": desc})
            except (NotImplementedError, AttributeError):
                return False, json.dumps({"error": "Vision 機能が利用できません"})

        return False, json.dumps({"error": f"未対応ツール: {tool_name}"}, ensure_ascii=False)
