"""セッションコパイロット ツール��ドオン。

シーン遷移・イベント駆動型自動対応を提供するセッション管理機能。
既存の core/session_copilot.py をラップする。
"""

from __future__ import annotations

import json
import logging

from core.addons.addon_base import AddonContext, ToolAddon, ToolExecutionContext

logger = logging.getLogger(__name__)

COPILOT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "transition_scene",
            "description": "登録済みシーンに遷移する（背景・BGM・キャラクター一括変更）",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_name": {"type": "string", "description": "遷移先のシーン名"},
                },
                "required": ["scene_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_scenes",
            "description": "登録済みシーンの一覧を取得する",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_scene",
            "description": "新しいシーンを登録する",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_definition": {
                        "type": "object",
                        "description": "シーン定義（name, background_image, bgm, characters）",
                    },
                },
                "required": ["scene_definition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_copilot_mode",
            "description": "コパイロットのモードを切り替える（auto: 自動実行, assist: 提案のみ）",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["auto", "assist"]},
                },
                "required": ["mode"],
            },
        },
    },
]


class SessionCopilotAddon(ToolAddon):
    """セッションコパイロット ツールアドオン。"""

    def on_load(self, context: AddonContext) -> None:
        self._context = context
        logger.info("セッションコパイロットアドオンをロードしました")

    def get_tools(self) -> list[dict]:
        return COPILOT_TOOLS

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        connector = context.connector
        copilot = getattr(connector, "_copilot", None)

        if tool_name == "transition_scene" and copilot:
            results = copilot.transition_to(tool_args.get("scene_name", ""))
            return False, json.dumps({"results": results}, ensure_ascii=False)

        if tool_name == "list_scenes" and copilot:
            scenes = copilot.list_scenes()
            return False, json.dumps({"scenes": scenes}, ensure_ascii=False)

        if tool_name == "register_scene" and copilot:
            try:
                from session_copilot import SceneDefinition
                defn = SceneDefinition.from_dict(tool_args.get("scene_definition", {}))
                copilot.register_scene(defn)
                return False, json.dumps({"ok": True, "scene": defn.name})
            except Exception as e:
                return False, json.dumps({"error": str(e)})

        if tool_name == "set_copilot_mode" and copilot:
            copilot.mode = tool_args.get("mode", "auto")
            return False, json.dumps({"ok": True, "mode": copilot.mode})

        if not copilot:
            return False, json.dumps({"error": "コパイロットが初期化されていません"})

        return False, json.dumps({"error": f"未対応ツール: {tool_name}"}, ensure_ascii=False)
