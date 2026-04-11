"""[システム名] TRPG ルールシステムアドオン — テンプレート。

新しいTRPGシステムを追加する際は、このファイルをコピーして
YourSystemAddon クラスをカスタマイズしてください。

手順:
  1. このフォルダを addons/<your_system_id>/ にコピーする
  2. addon.json の id / name / description などを書き換える
  3. addon.py の YourSystemAddon クラスをシステムに合わせて実装する
  4. world_setting.txt に世界観テキストを書く
  5. prompts.json に GMプロンプトを書く
  6. char_maker.py でキャラ作成GUIを実装する（任意）
  7. system_generator.py でコンテンツ生成ツールを実装する（任意）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.addons.addon_base import AddonContext, RuleSystemAddon, ToolExecutionContext

logger = logging.getLogger(__name__)


class YourSystemAddon(RuleSystemAddon):
    """新しいTRPGシステムのルールシステムアドオン。

    このクラスは タクティカル祓魔師 アドオンと同じ構造を持つテンプレートです。
    """

    # ──────────────────────────────────────────────────────────────────────────
    # フェイズ定義
    # ゲーム中の「局面（フェイズ）」を表すキーワードを定義します。
    # AIはチャットのメッセージからこれらのキーワードを検出して、
    # 現在のフェイズを判断します。
    # ──────────────────────────────────────────────────────────────────────────

    PHASE_KEYWORDS: dict[str, list[str]] = {
        # 例: "combat": ["戦闘開始", "戦闘スタート"],
        # 例: "investigation": ["調査開始", "現地に向かう"],
    }

    PHASE_ORDER: dict[str, int] = {
        # 例: "free": 0, "investigation": 1, "combat": 2, "epilogue": 3,
        "free": 0,
    }

    def on_load(self, context: AddonContext) -> None:
        """アドオンロード時の初期化処理。"""
        self._root = self.addon_dir
        self._prompts: dict | None = None
        self._world_setting: str = ""
        self._generator = None
        self._lm_client = context.lm_client

        # プロンプトテンプレート読み込み
        prompts_path = self._root / (self.manifest.prompts_override or "prompts.json")
        if prompts_path.exists():
            try:
                with open(prompts_path, encoding="utf-8") as f:
                    self._prompts = json.load(f)
                logger.info("プロンプトテンプレート読み込み: %s", prompts_path)
            except Exception as e:
                logger.warning("プロンプト読み込み失敗: %s", e)

        # 世界観設定読み込み
        ws_filename = self.manifest.world_setting or "world_setting.txt"
        ws_path = self._root / ws_filename
        if ws_path.exists():
            try:
                self._world_setting = ws_path.read_text(encoding="utf-8")
                logger.info("世界観設定読み込み: %s (%d文字)", ws_path, len(self._world_setting))
            except Exception as e:
                logger.warning("世界観設定読み込み失敗: %s", e)

        # 専用ジェネレーター読み込み（system_generator.py が存在する場合）
        try:
            from importlib.util import module_from_spec, spec_from_file_location

            gen_path = self._root / "system_generator.py"
            if gen_path.exists():
                spec = spec_from_file_location("your_system.system_generator", gen_path)
                if spec and spec.loader:
                    mod = module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    self._generator = mod.YourSystemGenerator()
                    logger.info("システムジェネレーター読み込み完了")
        except Exception as e:
            logger.warning("システムジェネレーター読み込み失敗: %s", e)

    # ──────────────────────────────────────────────────────────────────────────
    # RuleSystemAddon インターフェース（必要に応じてオーバーライド）
    # ──────────────────────────────────────────────────────────────────────────

    def get_system_prompt_override(self) -> str | None:
        """GMプロンプトのシステムテキストを返す。prompts.json から読み込む。"""
        if self._prompts is None:
            return None
        templates = self._prompts.get("templates", {})
        gm = templates.get("gm_template", {})
        return gm.get("system")

    def get_world_setting(self) -> str:
        """世界観設定テキストを返す。"""
        return self._world_setting

    def get_phase_keywords(self) -> dict[str, list[str]]:
        """フェイズ検出キーワードを返す。"""
        return self.PHASE_KEYWORDS

    def get_prompt_templates(self) -> dict | None:
        """プロンプトテンプレート辞書を返す。"""
        return self._prompts

    def interpret_character_sheet(self, sheet_data: dict) -> str:
        """キャラクターシートを自然言語テキストに変換する。

        TODO: このシステムのキャラクターフィールドに合わせて実装してください。
        """
        name = sheet_data.get("name", "不明")
        memo = sheet_data.get("memo", "")
        return f"【{name}】{memo[:80]}"

    # ──────────────────────────────────────────────────────────────────────────
    # ツール定義（system_generator.py が存在する場合に自動追加）
    # ──────────────────────────────────────────────────────────────────────────

    def get_tools(self) -> list[dict]:
        """ジェネレータツールを返す。system_generator.py で追加ツールを定義できます。"""
        if self._generator is None:
            return []
        # TODO: ジェネレーターが提供するツールを返す
        return []

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        """ジェネレーターツールを実行する。"""
        return False, json.dumps({"error": f"未知のツール: {tool_name}"}, ensure_ascii=False)

    # ──────────────────────────────────────────────────────────────────────────
    # キャラメーカー起動ヘルパー
    # ──────────────────────────────────────────────────────────────────────────

    def launch_char_maker(self) -> None:
        """専用キャラクターメーカーをGUIで起動する（char_maker.py が存在する場合）。"""
        import subprocess
        import sys

        char_maker_path = self.addon_dir / "char_maker.py"
        if not char_maker_path.exists():
            logger.warning("char_maker.py が見つかりません: %s", char_maker_path)
            return
        subprocess.Popen([sys.executable, str(char_maker_path)])
        logger.info("キャラクターメーカーを起動しました")
