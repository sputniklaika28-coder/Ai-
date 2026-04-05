"""compat.py — 後方互換ブリッジ。

addons/ ディレクトリにアドオンが存在しない場合、
旧来のハードコードされたツール定義をフォールバックとして提供する。
"""

from __future__ import annotations

import json
import logging

from .addon_base import AddonContext, ToolAddon, ToolExecutionContext

logger = logging.getLogger(__name__)


class LegacyToolsAddon(ToolAddon):
    """旧来のハードコードツールをラップする後方互換アドオン。

    addons/ にアドオンが1つも見つからない場合に AddonManager が
    自動的にこのアドオンを登録し、移行前と同一の動作を保証する。
    """

    def on_load(self, context: AddonContext) -> None:
        self._context = context
        logger.info("レガシー互換アドオンをロードしました（アドオン未検出時のフォールバック）")

    def get_tools(self) -> list[dict]:
        """旧来の ccfolia_connector.py から ASSET/VISION/ROOM/COPILOT/BUILD ツールを返す。"""
        tools: list[dict] = []
        try:
            import sys
            from pathlib import Path
            core_dir = Path(__file__).resolve().parent.parent
            if str(core_dir) not in sys.path:
                sys.path.insert(0, str(core_dir))
            from ccfolia_connector import (
                ASSET_TOOLS,
                BUILD_MODE_TOOLS,
                COPILOT_TOOLS,
                ROOM_TOOLS,
                VISION_TOOLS,
            )
            tools = ASSET_TOOLS + VISION_TOOLS + ROOM_TOOLS + COPILOT_TOOLS + BUILD_MODE_TOOLS
        except ImportError as e:
            logger.warning("レガシーツール定義の読み込みに失敗: %s", e)
        return tools

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        """コネクターに委譲してレガシーツールを実行する。"""
        connector = context.connector
        if connector and hasattr(connector, "_execute_tool_legacy"):
            return connector._execute_tool_legacy(
                tool_name, tool_args, context.char_name, context.tool_call_id
            )
        return False, json.dumps(
            {"error": f"レガシーツール {tool_name} の実行に失敗しました"}, ensure_ascii=False
        )
