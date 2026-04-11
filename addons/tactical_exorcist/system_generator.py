"""system_generator.py — タクティカル祓魔師専用コンテンツジェネレーター。

このモジュールは tactical_exorcist アドオンの付属機能です。
世界観に合ったNPC・敵・アイテム・シナリオフックを LLM を使って生成します。

addon.py から TacticalExorcistGenerator をインスタンス化して利用します。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── プロンプトテンプレート ──────────────────────────────────────────────────────

_SYS_GENERATOR = (
    "あなたはTRPG『タクティカル祓魔師』専用のデータジェネレーターです。"
    "必ず指定されたJSON形式のみを出力してください。"
    "Markdownコードブロック(```json)や説明文は一切出力しないでください。"
)

_NPC_PROMPT = """
タクティカル祓魔師の世界観（現代日本の都市部を舞台に、霊体・怪異と戦う祓魔師組織が存在する）に合ったNPCを生成してください。

コンセプト: {concept}
役割: {role}

以下のJSON形式で出力してください:
{{
  "name": "NPC名",
  "gender": "性別（男/女/不明）",
  "age": "年齢（または外見年齢）",
  "affiliation": "所属組織や立場",
  "personality": "性格の特徴（1〜2文）",
  "appearance": "外見の特徴（1〜2文）",
  "role": "{role}",
  "specialty": "得意分野や特技",
  "hook": "PLとの関係を作るきっかけ（1文）",
  "quote": "特徴的なセリフ例"
}}
"""

_ENEMY_PROMPT = """
タクティカル祓魔師の世界観に合った敵（霊体・怪異・堕落した祓魔師など）を生成してください。

コンセプト: {concept}
脅威レベル: {threat_level}

以下のJSON形式で出力してください:
{{
  "name": "敵の名称",
  "type": "種別（霊体/怪異/変異体/堕魔師など）",
  "threat_level": "{threat_level}",
  "appearance": "外見の描写（1〜2文）",
  "behavior": "行動パターンや習性（1〜2文）",
  "hp": 数値,
  "armor": 数値,
  "mobility": 数値,
  "attacks": [
    {{"name": "攻撃名", "description": "効果の説明", "damage": "ダメージ量（例: 2d6）"}}
  ],
  "special_ability": "特殊能力や弱点（1〜2文）",
  "reward": "撃破時に得られる情報やアイテムのヒント"
}}
"""

_ITEM_PROMPT = """
タクティカル祓魔師の世界観に合った祭具・アイテムを生成してください。
（祭具とは、霊体・怪異への攻撃や結界に用いる道具です）

コンセプト: {concept}

以下のJSON形式で出力してください:
{{
  "name": "アイテム名",
  "category": "カテゴリ（攻撃祭具/防御祭具/補助祭具/消耗品）",
  "description": "外見と概要（1〜2文）",
  "effect": "ゲーム内での効果説明",
  "usage": "使用方法や条件",
  "rarity": "レアリティ（一般/希少/極稀）",
  "lore": "この祭具にまつわる逸話（1〜2文）"
}}
"""

_SCENARIO_HOOK_PROMPT = """
タクティカル祓魔師のシナリオ導入（シナリオフック）を生成してください。
シナリオフックとは、プレイヤーキャラクターがミッションを受けるきっかけとなる出来事や依頼です。

場所・状況: {setting}
生成数: {num_hooks}個

以下のJSON形式で出力してください:
{{
  "hooks": [
    {{
      "title": "フックのタイトル",
      "trigger": "きっかけとなる出来事（1〜2文）",
      "mission": "PLたちに依頼される内容（1〜2文）",
      "complication": "ミッション中に判明する問題や複雑化要因（1文）",
      "reward": "報酬や解決後に得られるもの"
    }}
  ]
}}
"""


# ── ジェネレータークラス ───────────────────────────────────────────────────────

class TacticalExorcistGenerator:
    """タクティカル祓魔師専用コンテンツジェネレーター。

    LMClient を受け取り、世界観に合ったNPC・敵・アイテム・シナリオフックを生成する。
    """

    def generate_npc(
        self,
        lm_client: Any,
        concept: str,
        role: str = "neutral",
    ) -> dict:
        """世界観に合ったNPCを生成する。

        Args:
            lm_client: LMClient インスタンス
            concept: NPCのコンセプト（例: "厳格な上官"）
            role: "ally" / "neutral" / "enemy"

        Returns:
            NPC データの dict。生成失敗時は {"error": ...} を返す。
        """
        role_label = {"ally": "味方", "neutral": "中立", "enemy": "敵対"}.get(role, role)
        prompt = _NPC_PROMPT.format(concept=concept, role=role_label)
        return self._generate_json(lm_client, prompt, "NPC")

    def generate_enemy(
        self,
        lm_client: Any,
        concept: str,
        threat_level: str = "normal",
    ) -> dict:
        """世界観に合った敵を生成する。

        Args:
            lm_client: LMClient インスタンス
            concept: 敵のコンセプト
            threat_level: "weak" / "normal" / "strong" / "boss"

        Returns:
            敵データの dict。
        """
        level_label = {
            "weak": "弱（雑魚）",
            "normal": "通常",
            "strong": "強敵",
            "boss": "ボス",
        }.get(threat_level, threat_level)
        prompt = _ENEMY_PROMPT.format(concept=concept, threat_level=level_label)
        return self._generate_json(lm_client, prompt, "敵")

    def generate_item(
        self,
        lm_client: Any,
        concept: str,
    ) -> dict:
        """世界観に合ったアイテム・祭具を生成する。

        Args:
            lm_client: LMClient インスタンス
            concept: アイテムのコンセプト

        Returns:
            アイテムデータの dict。
        """
        prompt = _ITEM_PROMPT.format(concept=concept)
        return self._generate_json(lm_client, prompt, "アイテム")

    def generate_scenario_hook(
        self,
        lm_client: Any,
        setting: str,
        num_hooks: int = 3,
    ) -> dict:
        """シナリオフックを生成する。

        Args:
            lm_client: LMClient インスタンス
            setting: 場所・状況の概要
            num_hooks: 生成するフック数（1〜5）

        Returns:
            {"hooks": [...]} 形式の dict。
        """
        num_hooks = max(1, min(5, num_hooks))
        prompt = _SCENARIO_HOOK_PROMPT.format(setting=setting, num_hooks=num_hooks)
        return self._generate_json(lm_client, prompt, "シナリオフック")

    # ── 内部ヘルパー ──────────────────────────────────────────────────────────

    def _generate_json(self, lm_client: Any, prompt: str, label: str) -> dict:
        """LLM を呼び出し JSON レスポンスをパースして返す。"""
        try:
            raw = lm_client.generate_response(
                system_prompt=_SYS_GENERATOR,
                user_message=prompt,
                temperature=0.75,
                max_tokens=800,
                timeout=None,
            )
            if not raw:
                logger.warning("%s生成: 空のレスポンスを受け取りました", label)
                return {"error": "LLMが空のレスポンスを返しました"}

            clean = raw.replace("```json", "").replace("```", "").strip()
            # 先頭の { または [ を探して不要なプレフィックスを取り除く
            for start_char in ("{", "["):
                idx = clean.find(start_char)
                if idx > 0:
                    clean = clean[idx:]
                    break

            data = json.loads(clean)
            logger.info("%s生成完了", label)
            return data
        except json.JSONDecodeError as e:
            logger.error("%s生成: JSONパースエラー: %s", label, e)
            return {"error": f"JSONパースエラー: {e}", "raw": raw if "raw" in dir() else ""}
        except Exception as e:
            logger.error("%s生成: エラー: %s", label, e)
            return {"error": str(e)}
