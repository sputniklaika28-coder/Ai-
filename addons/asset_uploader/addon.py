"""アセットアップローダー ツールアドオン。

画像・BGMなどのローカルファイルをCCFoliaにアップロードする機能を提供する。
既存の core/asset_uploader.py をラップする。
"""

from __future__ import annotations

import json
import logging

from core.addons.addon_base import AddonContext, ToolAddon, ToolExecutionContext

logger = logging.getLogger(__name__)

ASSET_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "upload_asset",
            "description": "ローカルファイルをCCFoliaにアップロードする（画像・BGM）",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "アップロードするファイルパス"},
                    "asset_type": {
                        "type": "string",
                        "enum": ["background", "token", "bgm"],
                        "description": "アセット種別",
                    },
                },
                "required": ["file_path", "asset_type"],
            },
        },
    },
]


class AssetUploaderAddon(ToolAddon):
    """アセットアップローダー ツールアドオン。"""

    def on_load(self, context: AddonContext) -> None:
        self._context = context
        logger.info("アセットアップローダーアドオンをロードしました")

    def get_tools(self) -> list[dict]:
        return ASSET_TOOLS

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        adapter = context.adapter

        if tool_name == "upload_asset" and adapter:
            try:
                url = adapter.upload_asset(
                    tool_args.get("file_path", ""),
                    tool_args.get("asset_type", "background"),
                )
                return False, json.dumps({"url": url or "", "ok": url is not None})
            except NotImplementedError:
                return False, json.dumps(
                    {"error": "このアダプターは upload_asset に対応していません"}
                )

        return False, json.dumps({"error": f"未対応ツール: {tool_name}"}, ensure_ascii=False)
