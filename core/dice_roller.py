"""dice_roller.py — ダイスロールエンジン。

TRPG で使用するダイス記法 (NdS+M) を解析してロールし、
成否判定や程度 (degree) を計算する。

使用例::
    result = DiceRoller.roll("2d6+3")
    print(result.total)  # → 例: 12

    check = DiceRoller.check("2d6", difficulty=8)
    print(check.degree)  # → "success" / "failure" / ...
    print(check.to_schema())  # → RollResultSchema
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

# ──────────────────────────────────────
# 定数
# ──────────────────────────────────────

#: 成功程度のラベル（LLM ナレーションへの補助）
DEGREE_LABELS: dict[str, str] = {
    "critical_success": "クリティカル成功",
    "great_success": "大成功",
    "success": "成功",
    "failure": "失敗",
    "great_failure": "大失敗",
    "critical_failure": "ファンブル",
}

#: 大成功判定: 成功マージン ≥ この値
GREAT_SUCCESS_MARGIN = 5

#: 大失敗判定: 失敗マージン ≤ この値（絶対値）
GREAT_FAILURE_MARGIN = -5

# ──────────────────────────────────────
# 結果データクラス
# ──────────────────────────────────────


@dataclass
class RollResult:
    """ダイスロール 1 回の結果。"""

    notation: str
    """使用したダイス記法 (例: '2d6+3')。"""

    dice: list[int]
    """各ダイスの出目リスト。"""

    modifier: int
    """加算する修正値 (+/- 両対応)。"""

    dice_count: int
    """ダイス個数。"""

    dice_sides: int
    """ダイスの面数。"""

    @property
    def natural(self) -> int:
        """修正値を除いたダイス合計。"""
        return sum(self.dice)

    @property
    def total(self) -> int:
        """最終合計値 (sum(dice) + modifier)。"""
        return self.natural + self.modifier

    @property
    def is_max(self) -> bool:
        """全ダイスが最大値か（クリティカル成功判定用）。"""
        return all(d == self.dice_sides for d in self.dice)

    @property
    def is_min(self) -> bool:
        """全ダイスが 1 か（ファンブル判定用）。"""
        return all(d == 1 for d in self.dice)

    def to_narration(self) -> str:
        """ナレーションに挿入できる文字列を返す。

        例: '[2d6+3: 4+5+3 = 12]'
        """
        dice_str = "+".join(str(d) for d in self.dice)
        mod_str = ""
        if self.modifier > 0:
            mod_str = f"+{self.modifier}"
        elif self.modifier < 0:
            mod_str = str(self.modifier)
        return f"[{self.notation}: {dice_str}{mod_str} = {self.total}]"


@dataclass
class CheckResult:
    """判定ロール (ダイスロール + 難易度比較) の結果。"""

    roll: RollResult
    difficulty: int

    @property
    def margin(self) -> int:
        """成功マージン。正 = 成功、負 = 失敗。"""
        return self.roll.total - self.difficulty

    @property
    def success(self) -> bool:
        """成否。"""
        return self.margin >= 0

    @property
    def degree(self) -> str:
        """成否の程度ラベル。"""
        if self.roll.is_max and self.success:
            return "critical_success"
        if self.roll.is_min:
            return "critical_failure"
        if self.margin >= GREAT_SUCCESS_MARGIN:
            return "great_success"
        if self.margin >= 0:
            return "success"
        if self.margin <= GREAT_FAILURE_MARGIN:
            return "great_failure"
        return "failure"

    @property
    def degree_label(self) -> str:
        """日本語ラベル。"""
        return DEGREE_LABELS.get(self.degree, self.degree)

    def to_narration(self) -> str:
        """ナレーション用文字列。"""
        return (
            f"{self.roll.to_narration()} vs. 難易度{self.difficulty} "
            f"→ {self.degree_label} (マージン {self.margin:+d})"
        )

    def to_schema(self) -> "RollResultSchema":
        """core.schemas.RollResultSchema へ変換する。"""
        from core.schemas import RollResultSchema

        return RollResultSchema(
            notation=self.roll.notation,
            dice=self.roll.dice,
            modifier=self.roll.modifier,
            total=self.roll.total,
            difficulty=self.difficulty,
            success=self.success,
            degree=self.degree,
            margin=self.margin,
            narration=self.to_narration(),
        )


# ──────────────────────────────────────
# DiceRoller 本体
# ──────────────────────────────────────

# NdS、NdS+M、NdS-M 形式に対応
_DICE_PATTERN = re.compile(
    r"^(\d+)d(\d+)(?:([+-])(\d+))?$",
    re.IGNORECASE,
)


class DiceRoller:
    """ダイス記法を解析してロールするクラス。全メソッドがクラスメソッド。

    対応記法:
        - ``2d6``     — 2 個の 6 面ダイス
        - ``1d20+5``  — 1 個の 20 面ダイス + 修正値 +5
        - ``3d6-2``   — 3 個の 6 面ダイス + 修正値 -2
    """

    @classmethod
    def parse(cls, notation: str) -> tuple[int, int, int]:
        """記法を (count, sides, modifier) に分解する。

        Args:
            notation: ダイス記法文字列。

        Returns:
            (dice_count, dice_sides, modifier) のタプル。

        Raises:
            ValueError: 記法が不正な場合。
        """
        m = _DICE_PATTERN.match(notation.strip())
        if not m:
            raise ValueError(
                f"不正なダイス記法: {notation!r}。"
                f"有効な形式例: '2d6', '1d20+5', '3d6-2'"
            )
        count = int(m.group(1))
        sides = int(m.group(2))
        sign = m.group(3) or "+"
        raw_mod = int(m.group(4) or 0)
        modifier = raw_mod if sign == "+" else -raw_mod

        if count < 1:
            raise ValueError(f"ダイス個数は 1 以上が必要です: {count}")
        if sides < 2:
            raise ValueError(f"ダイス面数は 2 以上が必要です: {sides}")

        return count, sides, modifier

    @classmethod
    def roll(cls, notation: str, *, rng: random.Random | None = None) -> RollResult:
        """ダイスをロールして RollResult を返す。

        Args:
            notation: ダイス記法 ('2d6+3' など)。
            rng: 再現性テスト用の乱数生成器 (省略時は random モジュールを使用)。

        Returns:
            RollResult。
        """
        count, sides, modifier = cls.parse(notation)
        _random = rng or random
        dice = [_random.randint(1, sides) for _ in range(count)]
        return RollResult(
            notation=notation,
            dice=dice,
            modifier=modifier,
            dice_count=count,
            dice_sides=sides,
        )

    @classmethod
    def check(
        cls,
        notation: str,
        difficulty: int,
        *,
        rng: random.Random | None = None,
    ) -> CheckResult:
        """ダイスをロールして難易度と比較し CheckResult を返す。

        Args:
            notation: ダイス記法。
            difficulty: 成功に必要な最低合計値。
            rng: 再現性テスト用の乱数生成器。

        Returns:
            CheckResult。
        """
        roll = cls.roll(notation, rng=rng)
        return CheckResult(roll=roll, difficulty=difficulty)

    @classmethod
    def roll_to_schema(cls, notation: str) -> "RollResultSchema":
        """ロールして RollResultSchema を返す（スキーマ出力が必要な場合）。"""
        from core.schemas import RollResultSchema

        result = cls.roll(notation)
        return RollResultSchema(
            notation=result.notation,
            dice=result.dice,
            modifier=result.modifier,
            total=result.total,
            narration=result.to_narration(),
        )
