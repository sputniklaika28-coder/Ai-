"""combat_engine.py — TRPG 戦闘アクション解決エンジン。

GameIntention (LLM が解釈したプレイヤーの行動意図) を受け取り、
ゲームルールに従って決定論的に解決する。

解決フロー:
  1. 攻撃側・防御側を GameState から取得
  2. DiceRoller で判定ロール
  3. 成否・ダメージを算出して GameState に反映
  4. CombatActionResult を返す（LLM がナレーションに使用）

ルールシステムは RuleSystem プロトコルで差し替え可能。
デフォルトは TacticalExorcistRules（祓魔師ルール）。

使用例::
    engine = CombatEngine(game_state)
    intention = GameIntention(
        actor="アリス", target="ゴブリンA",
        action_type="attack", skill_name="精密射撃"
    )
    result = engine.resolve(intention)
    print(result.narration_hint)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from core.dice_roller import CheckResult, DiceRoller, RollResult
from core.game_state import CombatantState, GameState
from core.schemas import CombatActionResult, GameIntention, RollResultSchema

logger = logging.getLogger(__name__)


# ──────────────────────────────────────
# ルールシステム プロトコル
# ──────────────────────────────────────


@runtime_checkable
class RuleSystem(Protocol):
    """ゲームシステム固有のルールを定義するプロトコル。

    新しいゲームシステムに対応するにはこのプロトコルを実装する。
    """

    def default_dice(self) -> str:
        """判定に使うデフォルトのダイス記法 (例: '2d6')。"""
        ...

    def attack_difficulty(self, attacker: CombatantState, target: CombatantState) -> int:
        """通常攻撃の難易度を計算する。"""
        ...

    def calc_damage(
        self,
        attacker: CombatantState,
        target: CombatantState,
        check: CheckResult,
        skill_name: str | None,
    ) -> int:
        """ダメージ値を計算する（防御貫通後の最終値）。"""
        ...

    def on_critical_success(
        self,
        attacker: CombatantState,
        target: CombatantState,
        base_damage: int,
    ) -> tuple[int, list[str]]:
        """クリティカル成功時の追加効果。(追加ダメージ, 付与状態異常) を返す。"""
        ...

    def on_critical_failure(
        self,
        attacker: CombatantState,
    ) -> list[str]:
        """ファンブル時にアクター自身に付与する状態異常リスト。"""
        ...


# ──────────────────────────────────────
# デフォルトルール: TacticalExorcistRules
# ──────────────────────────────────────


class TacticalExorcistRules:
    """タクティカル祓魔師ルールシステム。

    判定: 2d6 + 修正値 ≥ 難易度
    攻撃難易度: 7 (基準) − 攻撃側 skill + 防御側 mobility の差分を加算
    ダメージ: body − target.armor (最小 1)
    クリティカル: 全ダイス最大値 → ダメージ +2、「朱印」状態を付与
    ファンブル: 全ダイス 1 → 攻撃側に「よろめき」を付与
    """

    _DEFAULT_DIFFICULTY = 7

    def default_dice(self) -> str:
        return "2d6"

    def attack_difficulty(self, attacker: CombatantState, target: CombatantState) -> int:
        # 技術差と機動力差で難易度が変わる
        skill_diff = attacker.skill - target.skill
        mobility_penalty = max(0, target.initiative - attacker.initiative) // 2
        return max(3, self._DEFAULT_DIFFICULTY - skill_diff + mobility_penalty)

    def calc_damage(
        self,
        attacker: CombatantState,
        target: CombatantState,
        check: CheckResult,
        skill_name: str | None,
    ) -> int:
        # 精神系スキルは soul 基準、それ以外は body 基準
        if skill_name and any(kw in skill_name for kw in ("霊", "魂", "魔", "祈", "符")):
            base = attacker.soul
        else:
            base = attacker.body
        damage = max(1, base - target.armor)
        return damage

    def on_critical_success(
        self,
        attacker: CombatantState,
        target: CombatantState,
        base_damage: int,
    ) -> tuple[int, list[str]]:
        return 2, ["朱印"]  # ダメージ +2、「朱印」状態を付与

    def on_critical_failure(
        self,
        attacker: CombatantState,
    ) -> list[str]:
        return ["よろめき"]


# ──────────────────────────────────────
# CombatEngine 本体
# ──────────────────────────────────────


class CombatEngine:
    """GameIntention を受け取り、ゲームルールに従って戦闘を解決するエンジン。

    Args:
        game_state: セッションの現在状態。
        rules: ルールシステム実装（省略時は TacticalExorcistRules）。
    """

    def __init__(
        self,
        game_state: GameState,
        rules: RuleSystem | None = None,
    ) -> None:
        self._state = game_state
        self._rules: RuleSystem = rules or TacticalExorcistRules()

    @property
    def game_state(self) -> GameState:
        return self._state

    # ──────────────────────────────────
    # メイン解決メソッド
    # ──────────────────────────────────

    def resolve(self, intention: GameIntention) -> CombatActionResult:
        """GameIntention を解決して CombatActionResult を返す。

        Args:
            intention: LLM が解釈したプレイヤーの行動意図。

        Returns:
            解決結果。error フィールドが None なら成功。
        """
        action_type = intention.action_type

        if action_type == "attack":
            return self._resolve_attack(intention)
        if action_type == "skill":
            return self._resolve_skill(intention)
        if action_type == "item":
            return self._resolve_item(intention)
        if action_type == "move":
            return self._resolve_move(intention)
        if action_type == "dialogue":
            return self._resolve_dialogue(intention)

        return self._error_result(
            intention,
            f"未対応のアクション種別: {action_type!r}",
        )

    # ──────────────────────────────────
    # アクション別解決ロジック
    # ──────────────────────────────────

    def _resolve_attack(self, intention: GameIntention) -> CombatActionResult:
        """通常攻撃を解決する。"""
        attacker = self._state.get_combatant(intention.actor)
        if attacker is None:
            return self._error_result(intention, f"攻撃者 '{intention.actor}' が見つかりません")

        if intention.target is None:
            return self._error_result(intention, "攻撃には対象が必要です (target)")

        target = self._state.get_combatant(intention.target)
        if target is None:
            return self._error_result(intention, f"対象 '{intention.target}' が見つかりません")

        # 判定
        difficulty = self._rules.attack_difficulty(attacker, target)
        check = DiceRoller.check(self._rules.default_dice(), difficulty)

        conditions_added: list[str] = []
        conditions_removed: list[str] = []
        damage = 0
        hp_after: int | None = None

        if check.success:
            damage = self._rules.calc_damage(attacker, target, check, intention.skill_name)

            if check.degree == "critical_success":
                extra_dmg, extra_conds = self._rules.on_critical_success(attacker, target, damage)
                damage += extra_dmg
                for cond in extra_conds:
                    if target.add_condition(cond):
                        conditions_added.append(cond)

            actual = target.apply_damage(damage)
            hp_after = target.hp
            narration_hint = (
                f"{attacker.name} が {target.name} に攻撃! "
                f"{check.degree_label} → {actual} ダメージ "
                f"(残 HP: {target.hp}/{target.max_hp})"
            )
        else:
            if check.degree == "critical_failure":
                for cond in self._rules.on_critical_failure(attacker):
                    if attacker.add_condition(cond):
                        conditions_added.append(cond)
            narration_hint = (
                f"{attacker.name} の攻撃は外れた! "
                f"{check.to_narration()}"
            )

        logger.info(
            "CombatEngine [attack]: %s → %s | %s | damage=%d",
            attacker.name, target.name, check.degree, damage,
        )

        return CombatActionResult(
            actor=attacker.name,
            target=target.name,
            action_type="attack",
            roll=check.to_schema(),
            success=check.success,
            damage_dealt=damage,
            hp_after=hp_after,
            conditions_added=conditions_added,
            conditions_removed=conditions_removed,
            narration_hint=narration_hint,
        )

    def _resolve_skill(self, intention: GameIntention) -> CombatActionResult:
        """スキルアクションを解決する。

        SP 消費 → 判定 → 効果適用 の順で処理する。
        スキル効果は narration_hint として LLM に渡す。
        """
        actor = self._state.get_combatant(intention.actor)
        if actor is None:
            return self._error_result(intention, f"キャラクター '{intention.actor}' が見つかりません")

        skill_name = intention.skill_name or "不明なスキル"

        # 汎用スキル判定（難易度は固定値 8）
        difficulty = 8
        check = DiceRoller.check(self._rules.default_dice(), difficulty)

        conditions_added: list[str] = []
        damage = 0
        hp_after: int | None = None

        target = self._state.get_combatant(intention.target) if intention.target else None

        if check.success and target is not None:
            damage = self._rules.calc_damage(actor, target, check, skill_name)
            target.apply_damage(damage)
            hp_after = target.hp

        narration_hint = (
            f"{actor.name} が「{skill_name}」を使用! "
            f"{check.to_narration()}"
        )
        if target and check.success:
            narration_hint += f" → {target.name} に {damage} ダメージ"

        logger.info(
            "CombatEngine [skill]: %s %s | %s",
            actor.name, skill_name, check.degree,
        )

        return CombatActionResult(
            actor=actor.name,
            target=target.name if target else None,
            action_type="skill",
            roll=check.to_schema(),
            success=check.success,
            damage_dealt=damage,
            hp_after=hp_after,
            conditions_added=conditions_added,
            narration_hint=narration_hint,
        )

    def _resolve_item(self, intention: GameIntention) -> CombatActionResult:
        """アイテム使用を解決する（現状は HP 回復に限定）。

        アイテムごとの効果は narration_hint で LLM に任せる。
        """
        actor = self._state.get_combatant(intention.actor)
        if actor is None:
            return self._error_result(intention, f"キャラクター '{intention.actor}' が見つかりません")

        item_name = intention.item_name or "不明なアイテム"
        target_name = intention.target or intention.actor
        target = self._state.get_combatant(target_name) or actor

        # アイテムは判定不要（成功前提）
        narration_hint = (
            f"{actor.name} が「{item_name}」を使用。"
            f"GMは効果を描写してください。"
        )

        logger.info("CombatEngine [item]: %s uses %s", actor.name, item_name)

        return CombatActionResult(
            actor=actor.name,
            target=target.name,
            action_type="item",
            roll=None,
            success=True,
            damage_dealt=0,
            hp_after=target.hp,
            narration_hint=narration_hint,
        )

    def _resolve_move(self, intention: GameIntention) -> CombatActionResult:
        """移動アクションを解決する（位置追跡なし）。"""
        actor = self._state.get_combatant(intention.actor)
        name = actor.name if actor else intention.actor

        narration_hint = f"{name} が移動した。GMは移動先を描写してください。"
        logger.info("CombatEngine [move]: %s", name)

        return CombatActionResult(
            actor=name,
            target=None,
            action_type="move",
            roll=None,
            success=True,
            narration_hint=narration_hint,
        )

    def _resolve_dialogue(self, intention: GameIntention) -> CombatActionResult:
        """台詞・対話アクションを解決する（判定なし）。"""
        actor = self._state.get_combatant(intention.actor)
        name = actor.name if actor else intention.actor
        dialogue = intention.dialogue or ""

        narration_hint = f"{name}: 「{dialogue}」"
        logger.info("CombatEngine [dialogue]: %s", name)

        return CombatActionResult(
            actor=name,
            target=intention.target,
            action_type="dialogue",
            roll=None,
            success=True,
            narration_hint=narration_hint,
        )

    # ──────────────────────────────────
    # 直接 HP / 状態操作 API
    # (ツールから直接呼ばれる操作)
    # ──────────────────────────────────

    def apply_hp_change(
        self,
        character_name: str,
        delta: int,
        reason: str = "",
    ) -> tuple[bool, str]:
        """キャラクターの HP を直接変更する。

        Args:
            character_name: 対象名。
            delta: HP 変化量（正 = 回復, 負 = ダメージ）。
            reason: 変化の理由（ログ用）。

        Returns:
            (成功したか, メッセージ文字列) のタプル。
        """
        target = self._state.get_combatant(character_name)
        if target is None:
            return False, f"キャラクター '{character_name}' が見つかりません"

        before = target.hp
        if delta >= 0:
            actual = target.heal(delta)
            msg = f"{target.name}: HP +{actual} ({before} → {target.hp}/{target.max_hp})"
        else:
            actual = target.apply_damage(-delta)
            msg = f"{target.name}: HP -{actual} ({before} → {target.hp}/{target.max_hp})"

        if reason:
            msg += f" [{reason}]"

        logger.info("CombatEngine [hp_change]: %s", msg)
        return True, msg

    # ──────────────────────────────────
    # ユーティリティ
    # ──────────────────────────────────

    def _error_result(
        self,
        intention: GameIntention,
        message: str,
    ) -> CombatActionResult:
        """エラー結果を返すヘルパー。"""
        logger.warning("CombatEngine エラー: %s", message)
        return CombatActionResult(
            actor=intention.actor,
            target=intention.target,
            action_type=intention.action_type,
            roll=None,
            success=False,
            narration_hint=f"[エラー] {message}",
            error=message,
        )
