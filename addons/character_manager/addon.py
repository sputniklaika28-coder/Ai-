"""character_manager addon — 全ルールシステム共通のキャラクターデータ保存庫。

core/character_manager.py の CharacterManager を共通サービスとして提供し、
各システムアドオンが生成したキャラクターデータをファイルへ永続化・読み出しします。

提供するツール:
  - save_character     : キャラクターデータを JSON ファイルに保存
  - load_character     : 保存済みキャラクターを名前で読み込み
  - list_characters    : 保存済みキャラクター一覧を返す
  - delete_character   : 保存済みキャラクターを削除
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.addons.addon_base import AddonContext, ToolAddon, ToolExecutionContext

logger = logging.getLogger(__name__)


class CharacterManagerAddon(ToolAddon):
    """キャラクターデータの永続化を担う共通アドオン。

    保存先: configs/saved_pcs/<name>.json
    （core/char_maker.py および tactical_exorcist/char_maker.py と互換）
    """

    def on_load(self, context: AddonContext) -> None:
        self._root_dir = context.root_dir
        self._saved_pcs_dir = self._root_dir / "configs" / "saved_pcs"
        self._saved_pcs_dir.mkdir(parents=True, exist_ok=True)

        # core の CharacterManager も保持（enabled キャラクター参照用）
        self._core_manager = context.character_manager
        logger.info(
            "CharacterManagerAddon ロード完了。保存先: %s", self._saved_pcs_dir
        )

    # ── ツール定義 ────────────────────────────────────────────────────────────

    def get_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "save_character",
                    "description": "キャラクターデータをファイルに保存する",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "キャラクター名（ファイル名に使われる）",
                            },
                            "data": {
                                "type": "object",
                                "description": "保存するキャラクターデータ（JSON オブジェクト）",
                            },
                        },
                        "required": ["name", "data"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "load_character",
                    "description": "保存済みキャラクターを名前で読み込む",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "読み込むキャラクター名",
                            },
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_characters",
                    "description": "保存済みキャラクターの名前一覧を返す",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_character",
                    "description": "保存済みキャラクターをファイルから削除する",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "削除するキャラクター名",
                            },
                        },
                        "required": ["name"],
                    },
                },
            },
        ]

    # ── ツール実行 ────────────────────────────────────────────────────────────

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        try:
            if tool_name == "save_character":
                return False, self._save_character(
                    tool_args["name"], tool_args["data"]
                )
            elif tool_name == "load_character":
                return False, self._load_character(tool_args["name"])
            elif tool_name == "list_characters":
                return False, self._list_characters()
            elif tool_name == "delete_character":
                return False, self._delete_character(tool_args["name"])
            else:
                return False, json.dumps(
                    {"error": f"未知のツール: {tool_name}"}, ensure_ascii=False
                )
        except KeyError as e:
            return False, json.dumps(
                {"error": f"必須パラメータが不足しています: {e}"}, ensure_ascii=False
            )
        except Exception as e:
            logger.error("ツール実行エラー %s: %s", tool_name, e)
            return False, json.dumps({"error": str(e)}, ensure_ascii=False)

    # ── 内部ロジック ──────────────────────────────────────────────────────────

    def _save_character(self, name: str, data: dict) -> str:
        """キャラクターデータをJSONファイルに保存する。"""
        if not name or not name.strip():
            return json.dumps({"success": False, "error": "名前が空です"}, ensure_ascii=False)

        safe_name = "".join(c for c in name if c.isalnum() or c in " _-（）")
        file_path = self._saved_pcs_dir / f"{safe_name}.json"
        data["name"] = name  # 名前フィールドを確実に含める
        file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("キャラクター保存: %s", file_path)
        return json.dumps(
            {"success": True, "saved_to": str(file_path), "name": name},
            ensure_ascii=False,
        )

    def _load_character(self, name: str) -> str:
        """保存済みキャラクターを読み込む。"""
        file_path = self._saved_pcs_dir / f"{name}.json"
        if not file_path.exists():
            # 部分一致で検索
            matches = list(self._saved_pcs_dir.glob(f"*{name}*.json"))
            if not matches:
                return json.dumps(
                    {"success": False, "error": f"{name} というキャラクターが見つかりません"},
                    ensure_ascii=False,
                )
            file_path = matches[0]

        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            return json.dumps({"success": True, "character": data}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    def _list_characters(self) -> str:
        """保存済みキャラクター一覧を返す。"""
        names = [f.stem for f in sorted(self._saved_pcs_dir.glob("*.json"))]
        return json.dumps({"characters": names, "count": len(names)}, ensure_ascii=False)

    def _delete_character(self, name: str) -> str:
        """保存済みキャラクターを削除する。"""
        file_path = self._saved_pcs_dir / f"{name}.json"
        if not file_path.exists():
            return json.dumps(
                {"success": False, "error": f"{name} が見つかりません"}, ensure_ascii=False
            )
        file_path.unlink()
        logger.info("キャラクター削除: %s", file_path)
        return json.dumps({"success": True, "deleted": name}, ensure_ascii=False)

    # ── 直接アクセス用 API（他のアドオンが Python から呼ぶ用）────────────────

    def get_character(self, name: str) -> dict | None:
        """キャラクターデータを dict で返す（ツール非経由）。"""
        file_path = self._saved_pcs_dir / f"{name}.json"
        if file_path.exists():
            try:
                return json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def get_all_character_names(self) -> list[str]:
        """保存済みキャラクター名の一覧を返す（ツール非経由）。"""
        return [f.stem for f in sorted(self._saved_pcs_dir.glob("*.json"))]
