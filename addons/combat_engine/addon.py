"""addon.py — 戦闘解決エンジン アドオン (Phase 3)。

LLM が呼び出せるツール群:
  roll_dice            — ダイスロール（判定あり・なし両対応）
  get_game_state       — 現在のゲーム状態スナップショット取得
  register_combatant   — 戦闘参加者を登録
  apply_hp_change      — HP を直接変更（回復・ダメージ）
  add_condition        — 状態異常を付与
  remove_condition     — 状態異常を解除
  set_initiative       — イニシアティブ値を設定
  start_combat         — 戦闘開始（イニシアティブ順決定）
  advance_turn         — ターンを進める
  end_combat           — 戦闘終了

セッション開始時に on_load() で GameState と CombatEngine が初期化される。
GameState はセッションディレクトリに game_state.json として永続化される。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from core.addons.addon_base import AddonBase, AddonContext, ToolExecutionContext
except ModuleNotFoundError:
    from addons.addon_base import AddonBase, AddonContext, ToolExecutionContext  # type: ignore


class CombatEngineAddon(AddonBase):
    """戦闘解決エンジン + ゲーム状態管理アドオン。"""

    def __init__(self) -> None:
        self._game_state: Any | None = None  # GameState
        self._engine: Any | None = None       # CombatEngine
        self._state_path: Path | None = None

    # ──────────────────────────────────────
    # ライフサイクル
    # ──────────────────────────────────────

    def on_load(self, context: AddonContext) -> None:
        from core.combat_engine import CombatEngine
        from core.game_state import GameState

        session_dir = getattr(context, "session_dir", None)
        if session_dir:
            self._state_path = Path(session_dir) / "game_state.json"
            if self._state_path.exists():
                try:
                    self._game_state = GameState.load(self._state_path)
                    logger.info("CombatEngineAddon: game_state.json から復元")
                except Exception as e:
                    logger.warning("CombatEngineAddon: 状態復元失敗 → 新規作成: %s", e)
                    self._game_state = GameState()
            else:
                self._game_state = GameState()
        else:
            self._game_state = GameState()

        self._engine = CombatEngine(self._game_state)
        logger.info("CombatEngineAddon: 初期化完了")

    def on_unload(self) -> None:
        self._save_state()
        self._game_state = None
        self._engine = None

    def _save_state(self) -> None:
        if self._game_state and self._state_path:
            try:
                self._game_state.save(self._state_path)
            except Exception as e:
                logger.error("CombatEngineAddon: 状態保存失敗: %s", e)

    # ──────────────────────────────────────
    # ツール定義
    # ──────────────────────────────────────

    def get_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "roll_dice",
                    "description": (
                        "指定されたダイス記法でダイスをロールする。"
                        "difficulty を指定すると成否判定も行う。"
                        "全ての判定・攻撃ロールはこのツールを使うこと。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "notation": {
                                "type": "string",
                                "description": "ダイス記法 (例: '2d6', '1d20+3', '3d6-2')",
                            },
                            "difficulty": {
                                "type": "integer",
                                "description": "難易度（省略時は単純ロールのみ）",
                            },
                            "character_name": {
                                "type": "string",
                                "description": "ロールするキャラクター名（ログ用）",
                            },
                            "purpose": {
                                "type": "string",
                                "description": "ロールの目的 (例: '命中判定', '回避')",
                            },
                        },
                        "required": ["notation"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_game_state",
                    "description": (
                        "現在のゲーム状態スナップショットを取得する。"
                        "全参加者の HP・状態異常・フェーズ・ターン順が含まれる。"
                        "GM ナレーション前に必ず確認すること。"
                    ),
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
                    "name": "register_combatant",
                    "description": (
                        "戦闘参加者（PC・NPC・敵）を登録する。"
                        "戦闘開始前に全参加者を登録すること。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "キャラクター名",
                            },
                            "hp": {
                                "type": "integer",
                                "description": "最大 HP（現在 HP も同値で初期化）",
                            },
                            "sp": {
                                "type": "integer",
                                "description": "最大 SP（精神力）",
                            },
                            "body": {
                                "type": "integer",
                                "description": "体格ステータス",
                            },
                            "soul": {
                                "type": "integer",
                                "description": "精神ステータス",
                            },
                            "skill": {
                                "type": "integer",
                                "description": "技術ステータス",
                            },
                            "armor": {
                                "type": "integer",
                                "description": "装甲値",
                            },
                            "mobility": {
                                "type": "integer",
                                "description": "機動力（イニシアティブとして使用）",
                            },
                            "is_enemy": {
                                "type": "boolean",
                                "description": "敵キャラクターか否か",
                            },
                        },
                        "required": ["name", "hp"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "apply_hp_change",
                    "description": (
                        "キャラクターの HP を変更する。"
                        "正の値で回復、負の値でダメージ。"
                        "ルールエンジン外で直接 HP を変更したい場合に使用。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "character_name": {
                                "type": "string",
                                "description": "対象キャラクター名",
                            },
                            "delta": {
                                "type": "integer",
                                "description": "HP 変化量（正 = 回復, 負 = ダメージ）",
                            },
                            "reason": {
                                "type": "string",
                                "description": "変化の理由（ログ・ナレーション用）",
                            },
                        },
                        "required": ["character_name", "delta"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_condition",
                    "description": "キャラクターに状態異常を付与する。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "character_name": {
                                "type": "string",
                                "description": "対象キャラクター名",
                            },
                            "condition": {
                                "type": "string",
                                "description": "付与する状態異常 (例: '出血', '気絶', '朱印')",
                            },
                        },
                        "required": ["character_name", "condition"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "remove_condition",
                    "description": "キャラクターの状態異常を解除する。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "character_name": {
                                "type": "string",
                                "description": "対象キャラクター名",
                            },
                            "condition": {
                                "type": "string",
                                "description": "解除する状態異常",
                            },
                        },
                        "required": ["character_name", "condition"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "set_initiative",
                    "description": "キャラクターのイニシアティブ値を設定する。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "character_name": {
                                "type": "string",
                                "description": "対象キャラクター名",
                            },
                            "value": {
                                "type": "integer",
                                "description": "イニシアティブ値（高いほど先手）",
                            },
                        },
                        "required": ["character_name", "value"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "start_combat",
                    "description": (
                        "戦闘を開始する。イニシアティブ順にターン順を決定する。"
                        "全参加者の register_combatant と set_initiative を完了してから呼ぶこと。"
                    ),
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
                    "name": "advance_turn",
                    "description": (
                        "ターンを 1 つ進める。"
                        "次の行動者名とラウンドが進んだかを返す。"
                    ),
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
                    "name": "end_combat",
                    "description": "戦闘を終了してフェーズを探索に戻す。",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
        ]

    # ──────────────────────────────────────
    # ツール実行
    # ──────────────────────────────────────

    def execute_tool(
        self,
        tool_name: str,
        tool_args: dict,
        context: ToolExecutionContext,
    ) -> tuple[bool, str | None]:
        dispatch = {
            "roll_dice": self._exec_roll_dice,
            "get_game_state": self._exec_get_game_state,
            "register_combatant": self._exec_register_combatant,
            "apply_hp_change": self._exec_apply_hp_change,
            "add_condition": self._exec_add_condition,
            "remove_condition": self._exec_remove_condition,
            "set_initiative": self._exec_set_initiative,
            "start_combat": self._exec_start_combat,
            "advance_turn": self._exec_advance_turn,
            "end_combat": self._exec_end_combat,
        }
        fn = dispatch.get(tool_name)
        if fn is None:
            return False, json.dumps({"error": f"未知のツール: {tool_name}"}, ensure_ascii=False)

        try:
            return fn(tool_args, context)
        except Exception as e:
            logger.exception("CombatEngineAddon: ツール実行エラー [%s]: %s", tool_name, e)
            return False, json.dumps({"error": str(e)}, ensure_ascii=False)

    # ── 個別ツール実装 ──────────────────

    def _exec_roll_dice(self, args: dict, _ctx: Any) -> tuple[bool, str]:
        from core.dice_roller import DiceRoller

        notation = args.get("notation", "2d6")
        difficulty = args.get("difficulty")
        char_name = args.get("character_name", "")
        purpose = args.get("purpose", "")

        if difficulty is not None:
            check = DiceRoller.check(notation, int(difficulty))
            schema = check.to_schema()
            log_msg = f"[roll] {char_name or '?'} {purpose}: {check.to_narration()}"
            logger.info(log_msg)
            return True, schema.model_dump_json(ensure_ascii=False)
        else:
            result = DiceRoller.roll(notation)
            from core.schemas import RollResultSchema
            schema = RollResultSchema(
                notation=result.notation,
                dice=result.dice,
                modifier=result.modifier,
                total=result.total,
                narration=result.to_narration(),
            )
            logger.info("[roll] %s: %s", char_name or "?", result.to_narration())
            return True, schema.model_dump_json(ensure_ascii=False)

    def _exec_get_game_state(self, _args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._game_state is None:
            return False, json.dumps({"error": "GameState 未初期化"}, ensure_ascii=False)
        snapshot = self._game_state.to_snapshot()
        return True, snapshot.model_dump_json(ensure_ascii=False)

    def _exec_register_combatant(self, args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._game_state is None:
            return False, json.dumps({"error": "GameState 未初期化"}, ensure_ascii=False)

        from core.game_state import CombatantState

        name = args.get("name", "")
        if not name:
            return False, json.dumps({"error": "name は必須です"}, ensure_ascii=False)

        hp = int(args.get("hp", 5))
        combatant = CombatantState(
            name=name,
            hp=hp,
            max_hp=hp,
            sp=int(args.get("sp", 0)),
            max_sp=int(args.get("sp", 0)),
            initiative=int(args.get("mobility", 3)),
            is_enemy=bool(args.get("is_enemy", False)),
            body=int(args.get("body", 3)),
            soul=int(args.get("soul", 3)),
            skill=int(args.get("skill", 3)),
            armor=int(args.get("armor", 0)),
        )
        self._game_state.add_combatant(combatant)
        self._save_state()

        return True, json.dumps({
            "registered": name,
            "hp": hp,
            "is_enemy": combatant.is_enemy,
        }, ensure_ascii=False)

    def _exec_apply_hp_change(self, args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._engine is None:
            return False, json.dumps({"error": "CombatEngine 未初期化"}, ensure_ascii=False)

        char_name = args.get("character_name", "")
        delta = int(args.get("delta", 0))
        reason = args.get("reason", "")

        success, msg = self._engine.apply_hp_change(char_name, delta, reason)
        self._save_state()

        if success:
            target = self._game_state.get_combatant(char_name)  # type: ignore
            return True, json.dumps({
                "message": msg,
                "hp": target.hp if target else None,
                "max_hp": target.max_hp if target else None,
            }, ensure_ascii=False)
        return False, json.dumps({"error": msg}, ensure_ascii=False)

    def _exec_add_condition(self, args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._game_state is None:
            return False, json.dumps({"error": "GameState 未初期化"}, ensure_ascii=False)

        char_name = args.get("character_name", "")
        condition = args.get("condition", "")
        target = self._game_state.get_combatant(char_name)
        if target is None:
            return False, json.dumps({"error": f"'{char_name}' が見つかりません"}, ensure_ascii=False)

        added = target.add_condition(condition)
        self._save_state()
        return True, json.dumps({
            "character": target.name,
            "condition": condition,
            "added": added,
            "conditions": target.conditions,
        }, ensure_ascii=False)

    def _exec_remove_condition(self, args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._game_state is None:
            return False, json.dumps({"error": "GameState 未初期化"}, ensure_ascii=False)

        char_name = args.get("character_name", "")
        condition = args.get("condition", "")
        target = self._game_state.get_combatant(char_name)
        if target is None:
            return False, json.dumps({"error": f"'{char_name}' が見つかりません"}, ensure_ascii=False)

        removed = target.remove_condition(condition)
        self._save_state()
        return True, json.dumps({
            "character": target.name,
            "condition": condition,
            "removed": removed,
            "conditions": target.conditions,
        }, ensure_ascii=False)

    def _exec_set_initiative(self, args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._game_state is None:
            return False, json.dumps({"error": "GameState 未初期化"}, ensure_ascii=False)

        char_name = args.get("character_name", "")
        value = int(args.get("value", 0))
        target = self._game_state.get_combatant(char_name)
        if target is None:
            return False, json.dumps({"error": f"'{char_name}' が見つかりません"}, ensure_ascii=False)

        target.initiative = value
        self._save_state()
        return True, json.dumps({
            "character": target.name,
            "initiative": value,
        }, ensure_ascii=False)

    def _exec_start_combat(self, _args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._game_state is None:
            return False, json.dumps({"error": "GameState 未初期化"}, ensure_ascii=False)

        turn_order = self._game_state.start_combat()
        self._save_state()
        return True, json.dumps({
            "phase": "combat",
            "round": 1,
            "turn_order": turn_order,
            "current_actor": self._game_state.current_actor,
        }, ensure_ascii=False)

    def _exec_advance_turn(self, _args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._game_state is None:
            return False, json.dumps({"error": "GameState 未初期化"}, ensure_ascii=False)

        next_actor, new_round = self._game_state.advance_turn()
        self._save_state()
        return True, json.dumps({
            "current_actor": next_actor,
            "round_number": self._game_state.round_number,
            "new_round": new_round,
        }, ensure_ascii=False)

    def _exec_end_combat(self, _args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._game_state is None:
            return False, json.dumps({"error": "GameState 未初期化"}, ensure_ascii=False)

        self._game_state.end_combat()
        self._save_state()
        return True, json.dumps({
            "phase": "exploration",
            "message": "戦闘終了。探索フェーズに戻りました。",
        }, ensure_ascii=False)
