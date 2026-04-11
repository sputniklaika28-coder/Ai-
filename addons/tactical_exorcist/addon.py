"""タクティカル祓魔師 TRPG ルールシステムアドオン。

ゲーム固有のフェイズキーワード・プロンプトテンプレート・世界観設定を
Core Engine に提供する。

付属機能:
  - char_maker.py  : タクティカル祓魔師専用キャラクターメーカー (GUI)
  - system_generator.py : NPC/敵/アイテム/シナリオフック生成ツール
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.addons.addon_base import AddonContext, RuleSystemAddon, ToolExecutionContext

logger = logging.getLogger(__name__)


class TacticalExorcistAddon(RuleSystemAddon):
    """タクティカル祓魔師 TRPG ルールシステム。

    Provides:
      - フェイズ検出キーワード
      - GMシステムプロンプト
      - 世界観テキスト
      - キャラクターシート解釈
      - NPC/敵/アイテム/シナリオフック生成ツール (system_generator 経由)
    """

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
        ws_filename = self.manifest.world_setting or "world_setting_compressed.txt"
        ws_path = self._root / ws_filename
        if ws_path.exists():
            try:
                self._world_setting = ws_path.read_text(encoding="utf-8")
                logger.info("世界観設定読み込み: %s (%d文字)", ws_path, len(self._world_setting))
            except Exception as e:
                logger.warning("世界観設定読み込み失敗: %s", e)

        # 専用ジェネレーター読み込み
        try:
            from importlib.util import module_from_spec, spec_from_file_location

            gen_path = self._root / "system_generator.py"
            if gen_path.exists():
                spec = spec_from_file_location("tactical_exorcist.system_generator", gen_path)
                if spec and spec.loader:
                    mod = module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    self._generator = mod.TacticalExorcistGenerator()
                    logger.info("システムジェネレーター読み込み完了")
        except Exception as e:
            logger.warning("システムジェネレーター読み込み失敗: %s", e)

    # ──────────────────────────────────────────
    # RuleSystemAddon インターフェース
    # ──────────────────────────────────────────

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

    def interpret_character_sheet(self, sheet_data: dict) -> str:
        """キャラクターシートを自然言語テキストに変換する。"""
        name = sheet_data.get("name", "不明")
        alias = sheet_data.get("alias", "")
        hp = sheet_data.get("hp", "?")
        sp = sheet_data.get("sp", "?")
        body = sheet_data.get("body", "?")
        soul = sheet_data.get("soul", "?")
        skill = sheet_data.get("skill", "?")
        magic = sheet_data.get("magic", "?")
        evasion = sheet_data.get("evasion", "?")
        mobility = sheet_data.get("mobility", "?")
        armor = sheet_data.get("armor", "?")
        memo = sheet_data.get("memo", "")

        items = sheet_data.get("items", {})
        item_parts = []
        item_labels = {
            "katashiro": "形代",
            "haraegushi": "祓串",
            "shimenawa": "注連鋼縄",
            "juryudan": "呪瘤檀",
            "ireikigu": "医霊器具",
            "meifuku": "名伏",
            "jutsuyen": "術延起点",
        }
        for key, label in item_labels.items():
            val = items.get(key, 0)
            if val > 0:
                item_parts.append(f"{label}×{val}")

        skills = sheet_data.get("skills", [])
        weapons = sheet_data.get("weapons", [])

        lines = [f"【{name}】" + (f"（{alias}）" if alias else "")]
        lines.append(f"体力:{hp} 霊力:{sp} 回避D:{evasion} 機動力:{mobility} 装甲:{armor}")
        lines.append(f"体:{body} 霊:{soul} 巧:{skill} 術:{magic}")
        if item_parts:
            lines.append("装備: " + "、".join(item_parts))
        if skills:
            lines.append("特技: " + "、".join(s.get("name", "") for s in skills))
        if weapons:
            lines.append("武器: " + "、".join(w.get("name", "") for w in weapons))
        if memo:
            lines.append(f"設定: {memo[:80]}{'…' if len(memo) > 80 else ''}")
        return "\n".join(lines)

    # ──────────────────────────────────────────
    # ツール定義（system_generator 付属ツール）
    # ──────────────────────────────────────────

    def get_tools(self) -> list[dict]:
        """システムジェネレーターが提供するツールを返す。"""
        if self._generator is None:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": "generate_npc",
                    "description": "タクティカル祓魔師世界観に合ったNPCを生成する",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "concept": {
                                "type": "string",
                                "description": "NPCのコンセプト（例: 厳格な上官、謎めいた情報屋）",
                            },
                            "role": {
                                "type": "string",
                                "enum": ["ally", "neutral", "enemy"],
                                "description": "NPCの役割",
                            },
                        },
                        "required": ["concept"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_enemy",
                    "description": "タクティカル祓魔師用の敵（霊体・怪異）を生成する",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "concept": {
                                "type": "string",
                                "description": "敵のコンセプト（例: 高速移動型の霊体、集団で行動する低級怪異）",
                            },
                            "threat_level": {
                                "type": "string",
                                "enum": ["weak", "normal", "strong", "boss"],
                                "description": "脅威レベル",
                            },
                        },
                        "required": ["concept"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_item",
                    "description": "タクティカル祓魔師世界観のアイテム・祭具を生成する",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "concept": {
                                "type": "string",
                                "description": "アイテムのコンセプト（例: 遠距離用の祓具、防御特化の結界アイテム）",
                            },
                        },
                        "required": ["concept"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_scenario_hook",
                    "description": "タクティカル祓魔師のシナリオフック（導入きっかけ）を生成する",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "setting": {
                                "type": "string",
                                "description": "場所や状況の概要（例: 廃校、繁華街の夜）",
                            },
                            "num_hooks": {
                                "type": "integer",
                                "description": "生成するフック数（1〜5）",
                                "minimum": 1,
                                "maximum": 5,
                            },
                        },
                        "required": ["setting"],
                    },
                },
            },
        ]

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        """ジェネレーターツールを実行する。"""
        if self._generator is None:
            return False, json.dumps({"error": "ジェネレーターが利用できません"}, ensure_ascii=False)

        try:
            if tool_name == "generate_npc":
                result = self._generator.generate_npc(
                    self._lm_client,
                    concept=tool_args.get("concept", ""),
                    role=tool_args.get("role", "neutral"),
                )
            elif tool_name == "generate_enemy":
                result = self._generator.generate_enemy(
                    self._lm_client,
                    concept=tool_args.get("concept", ""),
                    threat_level=tool_args.get("threat_level", "normal"),
                )
            elif tool_name == "generate_item":
                result = self._generator.generate_item(
                    self._lm_client,
                    concept=tool_args.get("concept", ""),
                )
            elif tool_name == "generate_scenario_hook":
                result = self._generator.generate_scenario_hook(
                    self._lm_client,
                    setting=tool_args.get("setting", ""),
                    num_hooks=tool_args.get("num_hooks", 3),
                )
            else:
                return False, json.dumps({"error": f"未知のツール: {tool_name}"}, ensure_ascii=False)

            return False, json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.error("ツール実行エラー %s: %s", tool_name, e)
            return False, json.dumps({"error": str(e)}, ensure_ascii=False)

    # ──────────────────────────────────────────
    # キャラメーカー起動ヘルパー
    # ──────────────────────────────────────────

    def launch_char_maker(self) -> None:
        """タクティカル祓魔師専用キャラクターメーカーをGUIで起動する。"""
        import subprocess
        import sys

        char_maker_path = self.addon_dir / "char_maker.py"
        if not char_maker_path.exists():
            logger.error("char_maker.py が見つかりません: %s", char_maker_path)
            return
        subprocess.Popen([sys.executable, str(char_maker_path)])
        logger.info("キャラクターメーカーを起動しました")
