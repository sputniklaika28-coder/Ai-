"""tests/test_combat_engine.py — CombatEngine のユニットテスト。

DiceRoller をモックして決定論的にテストする。
"""

from __future__ import annotations

import random
from unittest.mock import MagicMock, patch

import pytest

from core.combat_engine import CombatEngine, TacticalExorcistRules
from core.dice_roller import CheckResult, DiceRoller, RollResult
from core.game_state import CombatantState, GameState
from core.schemas import CombatActionResult, GameIntention


# ──────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────


def _make_pc(name: str = "アリス", hp: int = 10, body: int = 4, skill: int = 4, armor: int = 0) -> CombatantState:
    return CombatantState(
        name=name, hp=hp, max_hp=hp,
        sp=3, max_sp=3,
        initiative=5,
        body=body, soul=3, skill=skill, armor=armor,
    )


def _make_enemy(name: str = "ゴブリン", hp: int = 6, armor: int = 0) -> CombatantState:
    return CombatantState(
        name=name, hp=hp, max_hp=hp,
        initiative=3,
        is_enemy=True,
        body=2, skill=2, armor=armor,
    )


def _make_state(*combatants: CombatantState) -> GameState:
    gs = GameState()
    for c in combatants:
        gs.add_combatant(c)
    return gs


def _make_engine(*combatants: CombatantState) -> CombatEngine:
    gs = _make_state(*combatants)
    return CombatEngine(gs)


def _make_intention(
    actor: str,
    target: str | None = None,
    action_type: str = "attack",
    skill_name: str | None = None,
    item_name: str | None = None,
    dialogue: str | None = None,
) -> GameIntention:
    return GameIntention(
        actor=actor,
        target=target,
        action_type=action_type,
        skill_name=skill_name,
        item_name=item_name,
        dialogue=dialogue,
    )


def _fixed_check(dice: list[int], sides: int, modifier: int, difficulty: int, notation: str = "") -> CheckResult:
    """固定出目の CheckResult を作成する。"""
    if not notation:
        notation = f"{len(dice)}d{sides}"
    roll = RollResult(
        notation=notation,
        dice=dice,
        modifier=modifier,
        dice_count=len(dice),
        dice_sides=sides,
    )
    return CheckResult(roll=roll, difficulty=difficulty)


# ──────────────────────────────────────
# resolve() — 攻撃アクション
# ──────────────────────────────────────


class TestResolveAttack:
    def test_attack_success_deals_damage(self):
        """攻撃成功時にダメージが入り、target HP が減ること。"""
        pc = _make_pc("アリス", body=4, armor=0)
        enemy = _make_enemy("ゴブリン", hp=6, armor=0)
        engine = _make_engine(pc, enemy)

        # 2d6 → [5,5] = 10 (難易度 < 10 なので成功)
        success_check = _fixed_check([5, 5], 6, 0, difficulty=7)
        with patch.object(DiceRoller, "check", return_value=success_check):
            result = engine.resolve(_make_intention("アリス", target="ゴブリン"))

        assert result.success is True
        assert result.damage_dealt > 0
        assert result.hp_after == enemy.hp  # 反映後の HP

    def test_attack_failure_no_damage(self):
        """攻撃失敗時はダメージがないこと。"""
        pc = _make_pc("アリス")
        enemy = _make_enemy("ゴブリン")
        engine = _make_engine(pc, enemy)

        failure_check = _fixed_check([1, 2], 6, 0, difficulty=7)  # total=3 < 7
        with patch.object(DiceRoller, "check", return_value=failure_check):
            result = engine.resolve(_make_intention("アリス", target="ゴブリン"))

        assert result.success is False
        assert result.damage_dealt == 0

    def test_attack_returns_combat_action_result(self):
        pc = _make_pc("アリス")
        enemy = _make_enemy("ゴブリン")
        engine = _make_engine(pc, enemy)

        success_check = _fixed_check([4, 4], 6, 0, difficulty=7)
        with patch.object(DiceRoller, "check", return_value=success_check):
            result = engine.resolve(_make_intention("アリス", target="ゴブリン"))

        assert isinstance(result, CombatActionResult)
        assert result.action_type == "attack"
        assert result.actor == "アリス"
        assert result.target == "ゴブリン"

    def test_attack_roll_included_in_result(self):
        pc = _make_pc("アリス")
        enemy = _make_enemy("ゴブリン")
        engine = _make_engine(pc, enemy)

        success_check = _fixed_check([4, 4], 6, 0, difficulty=7)
        with patch.object(DiceRoller, "check", return_value=success_check):
            result = engine.resolve(_make_intention("アリス", target="ゴブリン"))

        assert result.roll is not None
        assert result.roll.total == 8

    def test_attack_damage_reduced_by_armor(self):
        """攻撃者 body - 防御者 armor = ダメージ（最小1）。"""
        pc = _make_pc("アリス", body=4)
        enemy = _make_enemy("重装ゴブリン", armor=2)  # 4 - 2 = 2 ダメージ
        engine = _make_engine(pc, enemy)

        success_check = _fixed_check([5, 5], 6, 0, difficulty=7)
        with patch.object(DiceRoller, "check", return_value=success_check):
            result = engine.resolve(_make_intention("アリス", target="重装ゴブリン"))

        assert result.damage_dealt == 2

    def test_attack_minimum_damage_is_1(self):
        """装甲が攻撃力を上回っても最小1ダメージ。"""
        pc = _make_pc("アリス", body=1)
        enemy = _make_enemy("鋼鉄ゴブリン", armor=10)
        engine = _make_engine(pc, enemy)

        success_check = _fixed_check([5, 5], 6, 0, difficulty=7)
        with patch.object(DiceRoller, "check", return_value=success_check):
            result = engine.resolve(_make_intention("アリス", target="鋼鉄ゴブリン"))

        assert result.damage_dealt >= 1

    def test_critical_success_adds_condition(self):
        """クリティカル成功で対象に「朱印」状態が付与される。"""
        pc = _make_pc("アリス", body=4)
        enemy = _make_enemy("ゴブリン")
        engine = _make_engine(pc, enemy)

        crit_check = _fixed_check([6, 6], 6, 0, difficulty=7)  # 全最大値 → critical
        with patch.object(DiceRoller, "check", return_value=crit_check):
            result = engine.resolve(_make_intention("アリス", target="ゴブリン"))

        assert result.success is True
        assert "朱印" in result.conditions_added
        assert enemy.has_condition("朱印")

    def test_critical_success_bonus_damage(self):
        """クリティカル成功でダメージ +2。"""
        pc = _make_pc("アリス", body=4, armor=0)
        enemy = _make_enemy("ゴブリン", armor=0)
        engine = _make_engine(pc, enemy)

        normal_check = _fixed_check([4, 4], 6, 0, difficulty=7)
        crit_check = _fixed_check([6, 6], 6, 0, difficulty=7)

        with patch.object(DiceRoller, "check", return_value=normal_check):
            normal_result = engine.resolve(_make_intention("アリス", target="ゴブリン"))

        # HP をリセット
        enemy.hp = enemy.max_hp

        with patch.object(DiceRoller, "check", return_value=crit_check):
            crit_result = engine.resolve(_make_intention("アリス", target="ゴブリン"))

        assert crit_result.damage_dealt == normal_result.damage_dealt + 2

    def test_critical_failure_adds_condition_to_attacker(self):
        """ファンブルで攻撃者に「よろめき」が付与される。"""
        pc = _make_pc("アリス")
        enemy = _make_enemy("ゴブリン")
        engine = _make_engine(pc, enemy)

        fumble_check = _fixed_check([1, 1], 6, 0, difficulty=7)
        with patch.object(DiceRoller, "check", return_value=fumble_check):
            result = engine.resolve(_make_intention("アリス", target="ゴブリン"))

        assert result.success is False
        assert "よろめき" in result.conditions_added
        assert pc.has_condition("よろめき")

    def test_attack_unknown_actor_returns_error(self):
        engine = _make_engine(_make_pc("アリス"))
        result = engine.resolve(_make_intention("存在しない", target="ゴブリン"))
        assert result.success is False
        assert result.error is not None

    def test_attack_unknown_target_returns_error(self):
        engine = _make_engine(_make_pc("アリス"))
        result = engine.resolve(_make_intention("アリス", target="存在しない"))
        assert result.success is False
        assert result.error is not None

    def test_attack_without_target_returns_error(self):
        engine = _make_engine(_make_pc("アリス"))
        result = engine.resolve(_make_intention("アリス", target=None))
        assert result.success is False
        assert result.error is not None


# ──────────────────────────────────────
# resolve() — スキルアクション
# ──────────────────────────────────────


class TestResolveSkill:
    def test_skill_success_deals_damage_to_target(self):
        pc = _make_pc("アリス", body=4)
        enemy = _make_enemy("ゴブリン")
        engine = _make_engine(pc, enemy)

        success_check = _fixed_check([5, 5], 6, 0, difficulty=8)
        with patch.object(DiceRoller, "check", return_value=success_check):
            result = engine.resolve(_make_intention(
                "アリス", target="ゴブリン",
                action_type="skill", skill_name="精密射撃",
            ))

        assert result.success is True
        assert result.action_type == "skill"
        assert result.damage_dealt >= 0

    def test_skill_failure_no_damage(self):
        pc = _make_pc("アリス")
        enemy = _make_enemy("ゴブリン")
        engine = _make_engine(pc, enemy)

        failure_check = _fixed_check([1, 2], 6, 0, difficulty=8)
        with patch.object(DiceRoller, "check", return_value=failure_check):
            result = engine.resolve(_make_intention(
                "アリス", target="ゴブリン",
                action_type="skill", skill_name="精密射撃",
            ))

        assert result.success is False
        assert result.damage_dealt == 0

    def test_skill_with_spiritual_keyword_uses_soul(self):
        """「霊」を含むスキル名は soul 基準でダメージを計算する。"""
        pc = CombatantState(
            name="アリス", hp=10, max_hp=10,
            body=2, soul=6, skill=3, armor=0, initiative=5,
        )
        enemy = _make_enemy("ゴブリン", armor=0)
        engine = _make_engine(pc, enemy)

        success_check = _fixed_check([5, 5], 6, 0, difficulty=8)
        with patch.object(DiceRoller, "check", return_value=success_check):
            result = engine.resolve(_make_intention(
                "アリス", target="ゴブリン",
                action_type="skill", skill_name="霊光弾",  # 「霊」含む
            ))

        assert result.success is True
        # soul=6 基準: ダメージ >= soul - armor = 6
        assert result.damage_dealt >= 6

    def test_skill_no_target_succeeds(self):
        """対象なしスキルはエラーにならない。"""
        pc = _make_pc("アリス")
        engine = _make_engine(pc)

        success_check = _fixed_check([5, 5], 6, 0, difficulty=8)
        with patch.object(DiceRoller, "check", return_value=success_check):
            result = engine.resolve(_make_intention(
                "アリス", target=None,
                action_type="skill", skill_name="防御陣形",
            ))

        assert result.action_type == "skill"
        assert result.error is None


# ──────────────────────────────────────
# resolve() — アイテム・移動・台詞
# ──────────────────────────────────────


class TestResolveOtherActions:
    def test_item_always_succeeds(self):
        pc = _make_pc("アリス")
        engine = _make_engine(pc)

        result = engine.resolve(_make_intention(
            "アリス", action_type="item", item_name="回復薬",
        ))
        assert result.success is True
        assert result.action_type == "item"
        assert result.roll is None  # 判定なし

    def test_move_always_succeeds(self):
        pc = _make_pc("アリス")
        engine = _make_engine(pc)

        result = engine.resolve(_make_intention("アリス", action_type="move"))
        assert result.success is True
        assert result.roll is None

    def test_dialogue_always_succeeds(self):
        pc = _make_pc("アリス")
        engine = _make_engine(pc)

        result = engine.resolve(_make_intention(
            "アリス", action_type="dialogue",
            dialogue="降参しろ！",
        ))
        assert result.success is True
        assert result.roll is None
        assert "降参しろ" in result.narration_hint

    def test_unknown_action_type_returns_error(self):
        pc = _make_pc("アリス")
        engine = _make_engine(pc)

        result = engine.resolve(_make_intention("アリス", action_type="unknown_action"))
        assert result.success is False
        assert result.error is not None


# ──────────────────────────────────────
# apply_hp_change()
# ──────────────────────────────────────


class TestApplyHpChange:
    def test_heal(self):
        pc = _make_pc("アリス", hp=10)
        pc.apply_damage(5)
        engine = _make_engine(pc)

        success, msg = engine.apply_hp_change("アリス", 3, "回復薬")
        assert success is True
        assert pc.hp == 8
        assert "3" in msg or "8" in msg

    def test_damage(self):
        pc = _make_pc("アリス", hp=10)
        engine = _make_engine(pc)

        success, msg = engine.apply_hp_change("アリス", -4, "罠")
        assert success is True
        assert pc.hp == 6

    def test_unknown_character_returns_false(self):
        engine = _make_engine(_make_pc("アリス"))
        success, msg = engine.apply_hp_change("存在しない", -3)
        assert success is False
        assert "見つかりません" in msg


# ──────────────────────────────────────
# TacticalExorcistRules 単体テスト
# ──────────────────────────────────────


class TestTacticalExorcistRules:
    def setup_method(self):
        self.rules = TacticalExorcistRules()

    def test_default_dice(self):
        assert self.rules.default_dice() == "2d6"

    def test_attack_difficulty_baseline(self):
        attacker = _make_pc(skill=3)
        target = _make_enemy()
        target.skill = 3
        target.initiative = 3
        attacker.initiative = 3
        diff = self.rules.attack_difficulty(attacker, target)
        assert diff == 7  # ベースライン

    def test_attack_difficulty_higher_attacker_skill(self):
        """攻撃者のスキルが高いと難易度が下がる。"""
        attacker = _make_pc(skill=5)
        target = _make_enemy()
        target.skill = 3
        target.initiative = 3
        attacker.initiative = 5
        diff = self.rules.attack_difficulty(attacker, target)
        assert diff < 7

    def test_attack_difficulty_minimum(self):
        """難易度の最小値は 3。"""
        attacker = CombatantState(name="a", hp=1, max_hp=1, skill=99)
        target = CombatantState(name="b", hp=1, max_hp=1, skill=1, initiative=0)
        attacker.initiative = 99
        diff = self.rules.attack_difficulty(attacker, target)
        assert diff >= 3

    def test_calc_damage_physical(self):
        attacker = _make_pc(body=4)
        target = _make_enemy(armor=1)
        check = _fixed_check([4, 4], 6, 0, difficulty=7)
        dmg = self.rules.calc_damage(attacker, target, check, "通常攻撃")
        assert dmg == 3  # 4 - 1

    def test_calc_damage_spiritual_keyword(self):
        attacker = CombatantState(name="a", hp=1, max_hp=1, body=2, soul=6)
        target = _make_enemy(armor=0)
        check = _fixed_check([4, 4], 6, 0, difficulty=7)
        dmg = self.rules.calc_damage(attacker, target, check, "霊撃")
        assert dmg == 6  # soul=6

    def test_on_critical_success(self):
        attacker = _make_pc()
        target = _make_enemy()
        extra_dmg, conditions = self.rules.on_critical_success(attacker, target, 3)
        assert extra_dmg == 2
        assert "朱印" in conditions

    def test_on_critical_failure(self):
        attacker = _make_pc()
        conditions = self.rules.on_critical_failure(attacker)
        assert "よろめき" in conditions


# ──────────────────────────────────────
# narration_hint のテスト
# ──────────────────────────────────────


class TestNarrationHint:
    def test_attack_success_hint_contains_actor_and_target(self):
        pc = _make_pc("アリス")
        enemy = _make_enemy("ゴブリン")
        engine = _make_engine(pc, enemy)

        success_check = _fixed_check([5, 5], 6, 0, difficulty=7)
        with patch.object(DiceRoller, "check", return_value=success_check):
            result = engine.resolve(_make_intention("アリス", target="ゴブリン"))

        assert "アリス" in result.narration_hint
        assert "ゴブリン" in result.narration_hint

    def test_attack_failure_hint_mentions_miss(self):
        pc = _make_pc("アリス")
        enemy = _make_enemy("ゴブリン")
        engine = _make_engine(pc, enemy)

        failure_check = _fixed_check([1, 2], 6, 0, difficulty=7)
        with patch.object(DiceRoller, "check", return_value=failure_check):
            result = engine.resolve(_make_intention("アリス", target="ゴブリン"))

        assert "外れ" in result.narration_hint

    def test_error_result_has_error_field(self):
        engine = _make_engine(_make_pc("アリス"))
        result = engine.resolve(_make_intention("存在しない", target="ゴブリン"))
        assert result.error is not None
        assert "[エラー]" in result.narration_hint
