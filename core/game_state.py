"""game_state.py — TRPG セッションのゲーム状態管理。

戦闘参加者 (CombatantState) と全体状態 (GameState) を管理する。
HP 管理・状態異常・イニシアティブ・ターン進行を担当する。

依存なし（stdlib のみ）。CombatEngine から利用される。

使用例::
    gs = GameState()
    gs.add_combatant(CombatantState("アリス", hp=10, max_hp=10, initiative=8))
    gs.add_combatant(CombatantState("ゴブリンA", hp=5, max_hp=5, is_enemy=True, initiative=3))
    gs.start_combat()
    print(gs.current_actor)   # → "アリス" (イニシアティブ高い方が先)
    gs.advance_turn()
    print(gs.current_actor)   # → "ゴブリンA"
    print(gs.summary())
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

PhaseType = Literal["exploration", "combat", "dialogue", "rest"]


# ──────────────────────────────────────
# CombatantState
# ──────────────────────────────────────


@dataclass
class CombatantState:
    """戦闘参加者（PC / NPC / 敵）の状態。

    Args:
        name: キャラクター名。GameState 内で一意であること。
        hp: 現在 HP。
        max_hp: 最大 HP。
        sp: 現在 SP（精神力）。
        max_sp: 最大 SP。
        initiative: イニシアティブ値（高い順にターンが来る）。
        conditions: 状態異常リスト (例: ['出血', '気絶'])。
        is_enemy: 敵キャラクターか否か。
        body: 体格ステータス（物理攻撃の基礎値）。
        soul: 精神ステータス（霊的攻撃の基礎値）。
        skill: 技術ステータス（命中精度）。
        magic: 魔力ステータス。
        armor: 装甲値（物理ダメージ軽減）。
    """

    name: str
    hp: int
    max_hp: int
    sp: int = 0
    max_sp: int = 0
    initiative: int = 0
    conditions: list[str] = field(default_factory=list)
    is_enemy: bool = False
    # 戦闘判定に使うステータス
    body: int = 3
    soul: int = 3
    skill: int = 3
    magic: int = 2
    armor: int = 0

    # ── プロパティ ──

    @property
    def is_alive(self) -> bool:
        """HP > 0 なら True。"""
        return self.hp > 0

    @property
    def hp_ratio(self) -> float:
        """HP 比率 (0.0〜1.0)。max_hp が 0 の場合は 0.0 を返す。"""
        return self.hp / self.max_hp if self.max_hp > 0 else 0.0

    # ── HP 操作 ──

    def apply_damage(self, amount: int) -> int:
        """ダメージを適用する。

        Args:
            amount: ダメージ量（負の値は 0 として扱う）。

        Returns:
            実際に減った HP 量。
        """
        amount = max(0, amount)
        before = self.hp
        self.hp = max(0, self.hp - amount)
        return before - self.hp

    def heal(self, amount: int) -> int:
        """HP を回復する。

        Args:
            amount: 回復量（負の値は 0 として扱う）。

        Returns:
            実際に回復した HP 量。
        """
        amount = max(0, amount)
        before = self.hp
        self.hp = min(self.max_hp, self.hp + amount)
        return self.hp - before

    def apply_sp_cost(self, cost: int) -> bool:
        """SP を消費する。

        Args:
            cost: 消費 SP 量。

        Returns:
            消費できた場合 True、SP 不足の場合 False（SP は減らない）。
        """
        if self.sp < cost:
            return False
        self.sp = max(0, self.sp - cost)
        return True

    # ── 状態異常 ──

    def add_condition(self, condition: str) -> bool:
        """状態異常を追加する（重複なし）。

        Returns:
            新規追加した場合 True。
        """
        if condition not in self.conditions:
            self.conditions.append(condition)
            return True
        return False

    def remove_condition(self, condition: str) -> bool:
        """状態異常を解除する。

        Returns:
            解除した場合 True。
        """
        if condition in self.conditions:
            self.conditions.remove(condition)
            return True
        return False

    def has_condition(self, condition: str) -> bool:
        """指定した状態異常を持つか。"""
        return condition in self.conditions

    # ── シリアライズ ──

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "sp": self.sp,
            "max_sp": self.max_sp,
            "initiative": self.initiative,
            "conditions": list(self.conditions),
            "is_enemy": self.is_enemy,
            "body": self.body,
            "soul": self.soul,
            "skill": self.skill,
            "magic": self.magic,
            "armor": self.armor,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CombatantState":
        return cls(
            name=d["name"],
            hp=d["hp"],
            max_hp=d["max_hp"],
            sp=d.get("sp", 0),
            max_sp=d.get("max_sp", 0),
            initiative=d.get("initiative", 0),
            conditions=list(d.get("conditions", [])),
            is_enemy=d.get("is_enemy", False),
            body=d.get("body", 3),
            soul=d.get("soul", 3),
            skill=d.get("skill", 3),
            magic=d.get("magic", 2),
            armor=d.get("armor", 0),
        )

    @classmethod
    def from_character_json(cls, char_json: dict, is_enemy: bool = False) -> "CombatantState":
        """char_maker.py / PersonaBuilder が生成するキャラシート dict から生成する。"""
        return cls(
            name=char_json.get("name", "不明"),
            hp=char_json.get("hp", 5),
            max_hp=char_json.get("hp", 5),
            sp=char_json.get("sp", 3),
            max_sp=char_json.get("sp", 3),
            initiative=char_json.get("mobility", 3),
            is_enemy=is_enemy,
            body=char_json.get("body", 3),
            soul=char_json.get("soul", 3),
            skill=char_json.get("skill", 3),
            magic=char_json.get("magic", 2),
            armor=char_json.get("armor", 0),
        )

    def to_snapshot(self) -> "CombatantSnapshot":
        """core.schemas.CombatantSnapshot へ変換する。"""
        from core.schemas import CombatantSnapshot

        return CombatantSnapshot(
            name=self.name,
            hp=self.hp,
            max_hp=self.max_hp,
            sp=self.sp,
            max_sp=self.max_sp,
            conditions=list(self.conditions),
            is_enemy=self.is_enemy,
            initiative=self.initiative,
        )


# ──────────────────────────────────────
# GameState
# ──────────────────────────────────────


class GameState:
    """TRPG セッションのゲーム状態全体を管理するクラス。

    戦闘参加者の HP・状態異常・ターン順を一元管理する。
    セッション間の永続化のために save() / load() を提供する。

    Attributes:
        phase: 現在フェーズ ('exploration' / 'combat' / 'dialogue' / 'rest')。
        round_number: 現在のラウンド数（戦闘フェーズのみ有効）。
    """

    def __init__(self) -> None:
        self.phase: PhaseType = "exploration"
        self.round_number: int = 0
        self._combatants: dict[str, CombatantState] = {}
        self._turn_order: list[str] = []
        self._current_turn_index: int = 0

    # ── 参加者管理 ──────────────────────

    def add_combatant(self, combatant: CombatantState) -> None:
        """参加者を追加する（同名の場合は上書き）。"""
        self._combatants[combatant.name] = combatant
        logger.debug("GameState: 参加者追加 '%s'", combatant.name)

    def remove_combatant(self, name: str) -> bool:
        """参加者を削除する。

        Returns:
            削除した場合 True。
        """
        if name in self._combatants:
            del self._combatants[name]
            self._turn_order = [n for n in self._turn_order if n != name]
            # ターンインデックスが範囲外になった場合は調整
            if self._turn_order:
                self._current_turn_index = self._current_turn_index % len(self._turn_order)
            else:
                self._current_turn_index = 0
            logger.debug("GameState: 参加者削除 '%s'", name)
            return True
        return False

    def get_combatant(self, name: str) -> CombatantState | None:
        """名前で参加者を取得する（完全一致 → 部分一致の順で検索）。"""
        # 完全一致
        if name in self._combatants:
            return self._combatants[name]
        # 部分一致（大文字小文字無視）
        name_lower = name.lower()
        for k, v in self._combatants.items():
            if name_lower in k.lower():
                return v
        return None

    @property
    def combatants(self) -> dict[str, CombatantState]:
        """参加者辞書（読み取り専用ビュー）。"""
        return self._combatants

    @property
    def alive_combatants(self) -> list[CombatantState]:
        """生存中の参加者リスト。"""
        return [c for c in self._combatants.values() if c.is_alive]

    @property
    def enemies(self) -> list[CombatantState]:
        """敵キャラクターのリスト。"""
        return [c for c in self._combatants.values() if c.is_enemy]

    @property
    def players(self) -> list[CombatantState]:
        """プレイヤーキャラクターのリスト。"""
        return [c for c in self._combatants.values() if not c.is_enemy]

    # ── 戦闘フェーズ管理 ──────────────

    def start_combat(self) -> list[str]:
        """戦闘を開始する。イニシアティブ順にターン順を決定する。

        Returns:
            イニシアティブ降順のキャラクター名リスト。
        """
        self.phase = "combat"
        self.round_number = 1
        self._turn_order = sorted(
            self._combatants.keys(),
            key=lambda n: self._combatants[n].initiative,
            reverse=True,
        )
        self._current_turn_index = 0
        logger.info(
            "GameState: 戦闘開始 ラウンド1 / ターン順: %s",
            " → ".join(self._turn_order),
        )
        return list(self._turn_order)

    def end_combat(self) -> None:
        """戦闘を終了する。"""
        self.phase = "exploration"
        self.round_number = 0
        self._turn_order = []
        self._current_turn_index = 0
        logger.info("GameState: 戦闘終了")

    def set_phase(self, phase: PhaseType) -> None:
        """フェーズを手動で設定する。"""
        self.phase = phase

    @property
    def current_actor(self) -> str | None:
        """現在ターンを持つキャラクター名。ターン順未設定の場合は None。"""
        if not self._turn_order:
            return None
        return self._turn_order[self._current_turn_index % len(self._turn_order)]

    @property
    def turn_order(self) -> list[str]:
        """現在のターン順リスト。"""
        return list(self._turn_order)

    def advance_turn(self) -> tuple[str | None, bool]:
        """ターンを 1 つ進める。

        Returns:
            (次の行動者名, ラウンドが進んだか) のタプル。
        """
        if not self._turn_order:
            return None, False

        self._current_turn_index += 1
        new_round = False
        if self._current_turn_index >= len(self._turn_order):
            self._current_turn_index = 0
            self.round_number += 1
            new_round = True
            logger.info("GameState: ラウンド %d 開始", self.round_number)

        actor = self.current_actor
        logger.debug("GameState: ターン進行 → %s (ラウンド%s)", actor, self.round_number)
        return actor, new_round

    # ── 状態サマリー ──────────────────

    def summary(self) -> str:
        """GM プロンプトに挿入できる状態サマリー文字列を返す。"""
        lines: list[str] = []
        lines.append(f"【フェーズ: {self.phase} | ラウンド: {self.round_number}】")

        if self.current_actor and self.phase == "combat":
            lines.append(f"現在のターン: {self.current_actor}")

        if not self._combatants:
            lines.append("（参加者なし）")
            return "\n".join(lines)

        # HP バーの描画
        pc_lines: list[str] = []
        enemy_lines: list[str] = []
        for name, c in self._combatants.items():
            filled = round(c.hp_ratio * 5)
            bar = "█" * filled + "░" * (5 - filled)
            cond_str = " [" + ", ".join(c.conditions) + "]" if c.conditions else ""
            sp_str = f" SP:{c.sp}/{c.max_sp}" if c.max_sp > 0 else ""
            entry = f"  {name}: HP {c.hp}/{c.max_hp} [{bar}]{sp_str}{cond_str}"
            if c.is_enemy:
                enemy_lines.append(entry)
            else:
                pc_lines.append(entry)

        if pc_lines:
            lines.append("【PC】")
            lines.extend(pc_lines)
        if enemy_lines:
            lines.append("【敵】")
            lines.extend(enemy_lines)

        return "\n".join(lines)

    # ── シリアライズ ──────────────────

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "round_number": self.round_number,
            "combatants": {k: v.to_dict() for k, v in self._combatants.items()},
            "_turn_order": list(self._turn_order),
            "_current_turn_index": self._current_turn_index,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GameState":
        gs = cls()
        gs.phase = d.get("phase", "exploration")
        gs.round_number = d.get("round_number", 0)
        for k, v in d.get("combatants", {}).items():
            gs._combatants[k] = CombatantState.from_dict(v)
        gs._turn_order = list(d.get("_turn_order", []))
        gs._current_turn_index = d.get("_current_turn_index", 0)
        return gs

    def save(self, path: str | Path) -> None:
        """ゲーム状態をファイルに保存する。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("GameState: 保存 → %s", path)

    @classmethod
    def load(cls, path: str | Path) -> "GameState":
        """ファイルからゲーム状態を読み込む。"""
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        gs = cls.from_dict(data)
        logger.info("GameState: 読み込み ← %s", path)
        return gs

    def to_snapshot(self) -> "GameStateSnapshot":
        """core.schemas.GameStateSnapshot へ変換する。"""
        from core.schemas import GameStateSnapshot

        return GameStateSnapshot(
            phase=self.phase,
            round_number=self.round_number,
            current_actor=self.current_actor,
            combatants=[c.to_snapshot() for c in self._combatants.values()],
            summary=self.summary(),
        )
