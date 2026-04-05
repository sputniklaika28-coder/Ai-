"""タクティカル祓魔師 TRPG ルールシステムアドオン。

ゲーム固有のフェイズキーワード・プロンプトテンプレート・世界観設定を
Core Engine に提供する。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.addons.addon_base import AddonContext, RuleSystemAddon

logger = logging.getLogger(__name__)


class TacticalExorcistAddon(RuleSystemAddon):
    """タクティカル祓魔師 TRPG ルールシステム。"""

    # ──────────────────────────────────────────
    # フェイズ定義（旧 SessionContext._PHASE_KEYWORDS / _PHASE_ORDER から抽出）
    # ──────────────────────────────────────────

    PHASE_KEYWORDS: dict[str, list[str]] = {
        "combat": ["戦闘開始", "戦闘スタート", "エンカウント", "敵が現れ"],
        "mission": ["ミッション開始", "ミッションフェイズ", "突入"],
        "assessment": ["査定フェイズ", "帰還"],
        "briefing": ["ブリーフィング"],
    }

    PHASE_ORDER: dict[str, int] = {
        "free": 0,
        "briefing": 1,
        "mission": 2,
        "combat": 3,
        "assessment": 4,
    }

    def on_load(self, context: AddonContext) -> None:
        self._root = self.addon_dir
        self._prompts: dict | None = None
        self._world_setting: str = ""

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
        ws_filename = self.manifest.world_setting or "world_setting_compressed.txt"
        ws_path = self._root / ws_filename
        if ws_path.exists():
            try:
                self._world_setting = ws_path.read_text(encoding="utf-8")
                logger.info("世界観設定読み込み: %s (%d文字)", ws_path, len(self._world_setting))
            except Exception as e:
                logger.warning("世界観設定読み込み失敗: %s", e)

    def get_system_prompt_override(self) -> str | None:
        """GMプロンプトのシステムテキストを返す。"""
        if self._prompts is None:
            return None
        templates = self._prompts.get("templates", {})
        meta_gm = templates.get("meta_gm_template", {})
        return meta_gm.get("system")

    def get_world_setting(self) -> str:
        return self._world_setting

    def get_phase_keywords(self) -> dict[str, list[str]]:
        return self.PHASE_KEYWORDS

    def get_prompt_templates(self) -> dict | None:
        return self._prompts
