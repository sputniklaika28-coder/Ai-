"""tests/test_dice_roller.py — DiceRoller のユニットテスト。

乱数生成器をシードして再現性のあるテストにする。
"""

from __future__ import annotations

import random

import pytest

from core.dice_roller import (
    GREAT_FAILURE_MARGIN,
    GREAT_SUCCESS_MARGIN,
    CheckResult,
    DiceRoller,
    RollResult,
)


# ──────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────


def _rng(seed: int = 42) -> random.Random:
    return random.Random(seed)


def _fixed_roll(dice: list[int], notation: str = "2d6") -> RollResult:
    """固定出目の RollResult を直接構築する（パーサーテスト用）。"""
    count, sides, modifier = DiceRoller.parse(notation)
    return RollResult(
        notation=notation,
        dice=dice,
        modifier=modifier,
        dice_count=count,
        dice_sides=sides,
    )


# ──────────────────────────────────────
# parse() のテスト
# ──────────────────────────────────────


class TestParse:
    def test_simple_notation(self):
        count, sides, modifier = DiceRoller.parse("2d6")
        assert count == 2
        assert sides == 6
        assert modifier == 0

    def test_positive_modifier(self):
        count, sides, modifier = DiceRoller.parse("1d20+5")
        assert count == 1
        assert sides == 20
        assert modifier == 5

    def test_negative_modifier(self):
        count, sides, modifier = DiceRoller.parse("3d6-2")
        assert count == 3
        assert sides == 6
        assert modifier == -2

    def test_uppercase_d(self):
        count, sides, modifier = DiceRoller.parse("2D6")
        assert count == 2
        assert sides == 6

    def test_large_dice(self):
        count, sides, modifier = DiceRoller.parse("1d100+10")
        assert sides == 100
        assert modifier == 10

    def test_invalid_notation_raises(self):
        with pytest.raises(ValueError):
            DiceRoller.parse("invalid")

    def test_missing_d_raises(self):
        with pytest.raises(ValueError):
            DiceRoller.parse("26")

    def test_zero_count_raises(self):
        with pytest.raises(ValueError):
            DiceRoller.parse("0d6")

    def test_one_sided_raises(self):
        with pytest.raises(ValueError):
            DiceRoller.parse("2d1")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            DiceRoller.parse("")

    def test_whitespace_stripped(self):
        count, sides, _ = DiceRoller.parse("  2d6  ")
        assert count == 2
        assert sides == 6


# ──────────────────────────────────────
# roll() のテスト
# ──────────────────────────────────────


class TestRoll:
    def test_returns_roll_result(self):
        result = DiceRoller.roll("2d6", rng=_rng())
        assert isinstance(result, RollResult)

    def test_dice_count_matches(self):
        result = DiceRoller.roll("3d6", rng=_rng())
        assert len(result.dice) == 3

    def test_dice_values_in_range(self):
        result = DiceRoller.roll("2d6", rng=_rng())
        for d in result.dice:
            assert 1 <= d <= 6

    def test_total_equals_sum_plus_modifier(self):
        result = DiceRoller.roll("2d6+3", rng=_rng())
        assert result.total == sum(result.dice) + 3

    def test_negative_modifier_applied(self):
        result = DiceRoller.roll("2d6-2", rng=_rng())
        assert result.total == sum(result.dice) - 2

    def test_notation_preserved(self):
        result = DiceRoller.roll("1d20+5", rng=_rng())
        assert result.notation == "1d20+5"

    def test_dice_count_stored(self):
        result = DiceRoller.roll("3d8", rng=_rng())
        assert result.dice_count == 3
        assert result.dice_sides == 8

    def test_natural_excludes_modifier(self):
        result = DiceRoller.roll("2d6+10", rng=_rng())
        assert result.natural == sum(result.dice)

    def test_reproducible_with_same_seed(self):
        r1 = DiceRoller.roll("2d6", rng=random.Random(99))
        r2 = DiceRoller.roll("2d6", rng=random.Random(99))
        assert r1.dice == r2.dice

    def test_different_seeds_can_differ(self):
        """異なるシードで少なくとも 1 回は異なる結果になることを確認。"""
        results = set()
        for seed in range(20):
            r = DiceRoller.roll("2d6", rng=random.Random(seed))
            results.add(r.total)
        assert len(results) > 1, "20 種のシードで全て同じ結果は統計的に起こりえない"


# ──────────────────────────────────────
# RollResult プロパティのテスト
# ──────────────────────────────────────


class TestRollResultProperties:
    def test_is_max_all_max(self):
        result = _fixed_roll([6, 6], "2d6")
        assert result.is_max is True

    def test_is_max_not_all_max(self):
        result = _fixed_roll([6, 5], "2d6")
        assert result.is_max is False

    def test_is_min_all_ones(self):
        result = _fixed_roll([1, 1], "2d6")
        assert result.is_min is True

    def test_is_min_not_all_ones(self):
        result = _fixed_roll([1, 2], "2d6")
        assert result.is_min is False

    def test_to_narration_contains_notation(self):
        result = _fixed_roll([3, 4], "2d6")
        narration = result.to_narration()
        assert "2d6" in narration

    def test_to_narration_contains_total(self):
        result = _fixed_roll([3, 4], "2d6")
        narration = result.to_narration()
        assert "7" in narration

    def test_to_narration_with_positive_modifier(self):
        result = _fixed_roll([3, 4], "2d6+3")
        narration = result.to_narration()
        assert "+3" in narration
        assert "10" in narration  # 3+4+3

    def test_to_narration_with_negative_modifier(self):
        result = _fixed_roll([4, 4], "2d6-2")
        narration = result.to_narration()
        assert "-2" in narration
        assert "6" in narration  # 4+4-2


# ──────────────────────────────────────
# check() のテスト
# ──────────────────────────────────────


class TestCheck:
    def _make_check(self, dice: list[int], notation: str, difficulty: int) -> CheckResult:
        count, sides, modifier = DiceRoller.parse(notation)
        roll = RollResult(
            notation=notation,
            dice=dice,
            modifier=modifier,
            dice_count=count,
            dice_sides=sides,
        )
        return CheckResult(roll=roll, difficulty=difficulty)

    def test_success_when_total_meets_difficulty(self):
        check = self._make_check([4, 4], "2d6", difficulty=8)
        assert check.success is True
        assert check.margin == 0

    def test_success_when_total_exceeds_difficulty(self):
        check = self._make_check([5, 5], "2d6", difficulty=7)
        assert check.success is True
        assert check.margin == 3

    def test_failure_when_total_below_difficulty(self):
        check = self._make_check([2, 2], "2d6", difficulty=8)
        assert check.success is False
        assert check.margin == -4

    def test_margin_is_total_minus_difficulty(self):
        check = self._make_check([3, 5], "2d6", difficulty=7)
        assert check.margin == 1  # 8 - 7

    # ── degree ──

    def test_degree_critical_success(self):
        check = self._make_check([6, 6], "2d6", difficulty=7)
        assert check.degree == "critical_success"

    def test_degree_great_success(self):
        # margin >= GREAT_SUCCESS_MARGIN (5)
        check = self._make_check([6, 5], "2d6", difficulty=6)  # total=11, margin=5
        assert check.degree == "great_success"

    def test_degree_success(self):
        check = self._make_check([4, 4], "2d6", difficulty=8)  # margin=0
        assert check.degree == "success"

    def test_degree_failure(self):
        check = self._make_check([2, 3], "2d6", difficulty=8)  # margin=-3
        assert check.degree == "failure"

    def test_degree_great_failure(self):
        # margin <= GREAT_FAILURE_MARGIN (-5)
        check = self._make_check([1, 2], "2d6", difficulty=9)  # total=3, margin=-6
        assert check.degree == "great_failure"

    def test_degree_critical_failure(self):
        check = self._make_check([1, 1], "2d6", difficulty=7)
        assert check.degree == "critical_failure"

    def test_critical_failure_overrides_great_failure(self):
        """ファンブル（全1）は margin に関わらず critical_failure。"""
        check = self._make_check([1, 1], "2d6", difficulty=100)
        assert check.degree == "critical_failure"

    def test_to_narration_includes_difficulty(self):
        check = self._make_check([4, 4], "2d6", difficulty=8)
        narration = check.to_narration()
        assert "8" in narration

    def test_to_narration_includes_degree_label(self):
        check = self._make_check([4, 4], "2d6", difficulty=8)
        narration = check.to_narration()
        assert "成功" in narration  # "成功" or "大成功" etc.

    # ── to_schema() ──

    def test_to_schema_returns_roll_result_schema(self):
        from core.schemas import RollResultSchema

        check = self._make_check([4, 4], "2d6", difficulty=8)
        schema = check.to_schema()
        assert isinstance(schema, RollResultSchema)

    def test_to_schema_fields_match(self):
        check = self._make_check([3, 5], "2d6", difficulty=7)
        schema = check.to_schema()
        assert schema.notation == "2d6"
        assert schema.dice == [3, 5]
        assert schema.total == 8
        assert schema.difficulty == 7
        assert schema.success is True
        assert schema.margin == 1

    def test_to_schema_degree_set(self):
        check = self._make_check([6, 6], "2d6", difficulty=7)
        schema = check.to_schema()
        assert schema.degree == "critical_success"


# ──────────────────────────────────────
# roll_to_schema() のテスト
# ──────────────────────────────────────


def test_roll_to_schema_returns_schema():
    from core.schemas import RollResultSchema

    schema = DiceRoller.roll_to_schema("2d6")
    assert isinstance(schema, RollResultSchema)
    assert schema.notation == "2d6"
    assert len(schema.dice) == 2
    assert schema.narration != ""
