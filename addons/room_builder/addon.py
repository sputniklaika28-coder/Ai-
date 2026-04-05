"""ルームビルダー ツールアドオン。

CCFolia ルームの自動構築（背景・BGM・キャラクター一括配置）機能を提供する。
既存の core/room_builder.py をラップし、ツール定義と実行ロジックを担う。
"""

from __future__ import annotations

import json
import logging

from core.addons.addon_base import AddonContext, ToolAddon, ToolExecutionContext

logger = logging.getLogger(__name__)

ROOM_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "build_room",
            "description": "構造化定義からCCFoliaルームを自動構築する（背景・BGM・キャラクター一括配置）",
            "parameters": {
                "type": "object",
                "properties": {
                    "room_definition": {
                        "type": "object",
                        "description": "ルーム定義（name, background_image, bgm, characters）",
                    },
                },
                "required": ["room_definition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_room_background",
            "description": "現在のルームの背景画像をアップロードして設定する",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string", "description": "背景画像の��ーカルパス"},
                },
                "required": ["image_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_room_bgm",
            "description": "ルームにBGMを追加する",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "音声ファイルパス"},
                    "name": {"type": "string", "description": "BGM名"},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "place_room_character",
            "description": "ルームにキャラクターを配置する",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "キャラクター名"},
                    "position": {"type": "string", "description": "配置位置の説明（自然言語）"},
                    "grid_x": {"type": "integer", "description": "グリッドX座標（省略可）"},
                    "grid_y": {"type": "integer", "description": "グリッドY座標（省略可）"},
                    "ccfolia_data": {"type": "object", "description": "CCFolia形式キャラクターデータ"},
                },
                "required": ["name", "ccfolia_data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enter_build_mode",
            "description": "ビルドモードに入る（RP停止、部屋構築専念）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exit_build_mode",
            "description": "ビルドモードを終了する（RP再開）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class RoomBuilderAddon(ToolAddon):
    """ルームビルダー ツールアドオン。"""

    def on_load(self, context: AddonContext) -> None:
        self._context = context
        logger.info("ルームビルダーアドオンをロードしました")

    def get_tools(self) -> list[dict]:
        return ROOM_TOOLS

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        connector = context.connector
        adapter = context.adapter

        if tool_name == "build_room" and adapter:
            if hasattr(connector, "_build_status") and connector._build_status.is_active:
                return False, json.dumps({"error": "ビルド中です。完了までお待ちください。"})
            if hasattr(connector, "enter_build_mode"):
                connector.enter_build_mode(context.char_name)
            try:
                from room_builder import RoomBuilder, RoomDefinition

                defn = RoomDefinition.from_dict(tool_args.get("room_definition", {}))
                build_status = getattr(connector, "_build_status", None)
                if build_status:
                    build_status.total_steps = (
                        1 + bool(defn.background_image)
                        + len(defn.bgm) + len(defn.characters)
                    )

                def on_progress(result):
                    if build_status:
                        build_status.completed_steps += 1
                        build_status.current_step = result.step
                    status = "✓" if result.success else "✗"
                    if hasattr(connector, "_post_system_message"):
                        step_count = build_status.completed_steps if build_status else "?"
                        total = build_status.total_steps if build_status else "?"
                        connector._post_system_message(
                            context.char_name,
                            f"[ビルド {step_count}/{total}] "
                            f"{status} {result.step}: {result.detail or result.error}",
                        )
                    if not result.success and build_status:
                        build_status.errors.append(result.error)

                builder = RoomBuilder(adapter=adapter, on_progress=on_progress)
                results = builder.build_room(defn)
                summary = [
                    {"step": r.step, "success": r.success, "detail": r.detail, "error": r.error}
                    for r in results
                ]
                return False, json.dumps({"results": summary}, ensure_ascii=False)
            except Exception as e:
                if hasattr(connector, "_build_status"):
                    connector._build_status.errors.append(str(e))
                return False, json.dumps({"error": str(e)})
            finally:
                if hasattr(connector, "exit_build_mode"):
                    connector.exit_build_mode(context.char_name)

        if tool_name == "set_room_background" and adapter:
            try:
                from room_builder import RoomBuilder
                builder = RoomBuilder(adapter=adapter)
                r = builder.set_background(tool_args.get("image_path", ""))
                return False, json.dumps({"ok": r.success, "detail": r.detail, "error": r.error})
            except Exception as e:
                return False, json.dumps({"error": str(e)})

        if tool_name == "add_room_bgm" and adapter:
            try:
                from room_builder import RoomBuilder
                builder = RoomBuilder(adapter=adapter)
                r = builder.add_bgm(tool_args.get("file_path", ""), tool_args.get("name", ""))
                return False, json.dumps({"ok": r.success, "detail": r.detail, "error": r.error})
            except Exception as e:
                return False, json.dumps({"error": str(e)})

        if tool_name == "place_room_character" and adapter:
            try:
                from room_builder import CharacterPlacement, RoomBuilder
                builder = RoomBuilder(adapter=adapter)
                char = CharacterPlacement(
                    name=tool_args.get("name", ""),
                    position=tool_args.get("position", ""),
                    grid_x=tool_args.get("grid_x"),
                    grid_y=tool_args.get("grid_y"),
                    ccfolia_data=tool_args.get("ccfolia_data", {}),
                )
                r = builder.place_character(char)
                return False, json.dumps({"ok": r.success, "detail": r.detail, "error": r.error})
            except Exception as e:
                return False, json.dumps({"error": str(e)})

        if tool_name == "enter_build_mode":
            build_status = getattr(connector, "_build_status", None)
            if build_status and build_status.is_active:
                return False, json.dumps({"error": "既にビルドモード中です"})
            if hasattr(connector, "enter_build_mode"):
                connector.enter_build_mode(context.char_name)
            return False, json.dumps({"ok": True, "build_mode": "building"})

        if tool_name == "exit_build_mode":
            build_status = getattr(connector, "_build_status", None)
            if build_status and not build_status.is_active:
                return False, json.dumps({"error": "ビルドモードではありません"})
            if hasattr(connector, "exit_build_mode"):
                connector.exit_build_mode(context.char_name)
            return False, json.dumps({"ok": True, "build_mode": "idle"})

        return False, json.dumps({"error": f"未対応ツール: {tool_name}"}, ensure_ascii=False)
