"""system_generator.py — [システム名]専用コンテンツジェネレーター テンプレート。

このモジュールは addon.py の付属機能です。
世界観に合ったNPC・敵・アイテム・シナリオフックを LLM で生成します。

使い方:
  addon.py から YourSystemGenerator をインポートしてインスタンス化します。
  クラス名は addon.py 内の以下の行に合わせてください:
    self._generator = mod.YourSystemGenerator()
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_SYS_GENERATOR = (
    "あなたはTRPG『[システム名]』専用のデータジェネレーターです。"
    "必ず指定されたJSON形式のみを出力してください。"
    "Markdownコードブロックや説明文は一切出力しないでください。"
)


class YourSystemGenerator:
    """[システム名]専用コンテンツジェネレーター。

    TODO: このシステムに合ったプロンプトとメソッドを実装してください。
    タクティカル祓魔師の system_generator.py を参考にしてください。
    """

    def generate_npc(self, lm_client: Any, concept: str, **kwargs: Any) -> dict:
        """NPCを生成する。"""
        prompt = f"コンセプト「{concept}」のNPCをJSON形式で生成してください。"
        return self._generate_json(lm_client, prompt, "NPC")

    def generate_enemy(self, lm_client: Any, concept: str, **kwargs: Any) -> dict:
        """敵キャラクターを生成する。"""
        prompt = f"コンセプト「{concept}」の敵をJSON形式で生成してください。"
        return self._generate_json(lm_client, prompt, "敵")

    def generate_item(self, lm_client: Any, concept: str, **kwargs: Any) -> dict:
        """アイテムを生成する。"""
        prompt = f"コンセプト「{concept}」のアイテムをJSON形式で生成してください。"
        return self._generate_json(lm_client, prompt, "アイテム")

    def generate_scenario_hook(
        self, lm_client: Any, setting: str, num_hooks: int = 3, **kwargs: Any
    ) -> dict:
        """シナリオフックを生成する。"""
        prompt = (
            f"場所・状況「{setting}」のシナリオフックを{num_hooks}個、"
            'JSON形式 {"hooks": [...]} で生成してください。'
        )
        return self._generate_json(lm_client, prompt, "シナリオフック")

    def _generate_json(self, lm_client: Any, prompt: str, label: str) -> dict:
        """LLM を呼び出し JSON をパースして返す共通ヘルパー。"""
        try:
            raw = lm_client.generate_response(
                system_prompt=_SYS_GENERATOR,
                user_message=prompt,
                temperature=0.75,
                max_tokens=800,
                timeout=None,
            )
            if not raw:
                return {"error": "LLMが空のレスポンスを返しました"}

            clean = raw.replace("```json", "").replace("```", "").strip()
            for start_char in ("{", "["):
                idx = clean.find(start_char)
                if idx > 0:
                    clean = clean[idx:]
                    break

            return json.loads(clean)
        except json.JSONDecodeError as e:
            logger.error("%s生成: JSONパースエラー: %s", label, e)
            return {"error": f"JSONパースエラー: {e}"}
        except Exception as e:
            logger.error("%s生成: エラー: %s", label, e)
            return {"error": str(e)}
