"""tests/test_game_state.py — GameState と CombatantState のユニットテスト。"""

from __future__ import annotations

import json

import pytest

from core.game_state import CombatantState, GameState


# ──────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────


def _make_pc(name: str = "アリス", hp: int = 10, initiative: int = 5) -> CombatantState:
    return CombatantState(
        name=name, hp=hp, max_hp=hp,
        sp=3, max_sp=3,
        initiative=initiative,
        body=4, soul=3, skill=4, armor=1,
    )


def _make_enemy(name: str = "ゴブリン", hp: int = 5, initiative: int = 3) -> CombatantState:
    return CombatantState(
        name=name, hp=hp, max_hp=hp,
        initiative=initiative,
        is_enemy=True,
        body=2, armor=0,
    )


def _make_state_with_combatants() -> GameState:
    gs = GameState()
    gs.add_combatant(_make_pc("アリス", hp=10, initiative=8))
    gs.add_combatant(_make_pc("ボブ", hp=8, initiative=5))
    gs.add_combatant(_make_enemy("ゴブリンA", hp=5, initiative=3))
    gs.add_combatant(_make_enemy("ゴブリンB", hp=5, initiative=4))
    return gs


# ──────────────────────────────────────
# CombatantState — HP 操作
# ──────────────────────────────────────


class TestCombatantStateHP:
    def test_initial_hp(self):
        c = _make_pc(hp=10)
        assert c.hp == 10
        assert c.max_hp == 10

    def test_apply_damage_reduces_hp(self):
        c = _make_pc(hp=10)
        actual = c.apply_damage(3)
        assert c.hp == 7
        assert actual == 3

    def test_apply_damage_clamped_to_zero(self):
        c = _make_pc(hp=5)
        actual = c.apply_damage(100)
        assert c.hp == 0
        assert actual == 5

    def test_apply_damage_negative_treated_as_zero(self):
        c = _make_pc(hp=10)
        actual = c.apply_damage(-5)
        assert c.hp == 10
        assert actual == 0

    def test_heal_increases_hp(self):
        c = _make_pc(hp=10)
        c.apply_damage(4)
        actual = c.heal(2)
        assert c.hp == 8
        assert actual == 2

    def test_heal_clamped_to_max_hp(self):
        c = _make_pc(hp=10)
        actual = c.heal(5)
        assert c.hp == 10
        assert actual == 0  # すでに最大

    def test_heal_negative_treated_as_zero(self):
        c = _make_pc(hp=10)
        c.apply_damage(3)
        actual = c.heal(-1)
        assert c.hp == 7
        assert actual == 0

    def test_is_alive_when_hp_positive(self):
        c = _make_pc(hp=1)
        assert c.is_alive is True

    def test_is_dead_when_hp_zero(self):
        c = _make_pc(hp=5)
        c.apply_damage(5)
        assert c.is_alive is False

    def test_hp_ratio_full(self):
        c = _make_pc(hp=10)
        assert c.hp_ratio == pytest.approx(1.0)

    def test_hp_ratio_half(self):
        c = _make_pc(hp=10)
        c.apply_damage(5)
        assert c.hp_ratio == pytest.approx(0.5)

    def test_hp_ratio_zero_max_returns_zero(self):
        c = CombatantState(name="test", hp=0, max_hp=0)
        assert c.hp_ratio == 0.0


# ──────────────────────────────────────
# CombatantState — SP 操作
# ──────────────────────────────────────


class TestCombatantStateSP:
    def test_apply_sp_cost_success(self):
        c = _make_pc()
        result = c.apply_sp_cost(2)
        assert result is True
        assert c.sp == 1

    def test_apply_sp_cost_insufficient(self):
        c = _make_pc()
        result = c.apply_sp_cost(5)  # max_sp=3
        assert result is False
        assert c.sp == 3  # 変化なし

    def test_apply_sp_cost_exact(self):
        c = _make_pc()
        result = c.apply_sp_cost(3)
        assert result is True
        assert c.sp == 0


# ──────────────────────────────────────
# CombatantState — 状態異常
# ──────────────────────────────────────


class TestCombatantStateConditions:
    def test_add_condition(self):
        c = _make_pc()
        added = c.add_condition("出血")
        assert added is True
        assert "出血" in c.conditions

    def test_add_condition_no_duplicate(self):
        c = _make_pc()
        c.add_condition("出血")
        added = c.add_condition("出血")
        assert added is False
        assert c.conditions.count("出血") == 1

    def test_remove_condition(self):
        c = _make_pc()
        c.add_condition("出血")
        removed = c.remove_condition("出血")
        assert removed is True
        assert "出血" not in c.conditions

    def test_remove_nonexistent_condition(self):
        c = _make_pc()
        removed = c.remove_condition("気絶")
        assert removed is False

    def test_has_condition_true(self):
        c = _make_pc()
        c.add_condition("朱印")
        assert c.has_condition("朱印") is True

    def test_has_condition_false(self):
        c = _make_pc()
        assert c.has_condition("朱印") is False

    def test_multiple_conditions(self):
        c = _make_pc()
        c.add_condition("出血")
        c.add_condition("気絶")
        assert len(c.conditions) == 2


# ──────────────────────────────────────
# CombatantState — シリアライズ
# ──────────────────────────────────────


class TestCombatantStateSerialization:
    def test_to_dict_from_dict_roundtrip(self):
        c = _make_pc("テスト", hp=8, initiative=6)
        c.add_condition("出血")
        d = c.to_dict()
        restored = CombatantState.from_dict(d)

        assert restored.name == c.name
        assert restored.hp == c.hp
        assert restored.max_hp == c.max_hp
        assert restored.conditions == c.conditions
        assert restored.initiative == c.initiative

    def test_from_character_json(self):
        char_json = {
            "name": "鈴木アリス",
            "hp": 12,
            "sp": 4,
            "body": 5,
            "soul": 3,
            "skill": 4,
            "magic": 2,
            "armor": 1,
            "mobility": 5,
        }
        c = CombatantState.from_character_json(char_json, is_enemy=False)
        assert c.name == "鈴木アリス"
        assert c.hp == 12
        assert c.max_hp == 12
        assert c.sp == 4
        assert c.body == 5
        assert c.initiative == 5
        assert c.is_enemy is False

    def test_to_snapshot(self):
        from core.schemas import CombatantSnapshot

        c = _make_pc("アリス")
        c.add_condition("朱印")
        snap = c.to_snapshot()
        assert isinstance(snap, CombatantSnapshot)
        assert snap.name == "アリス"
        assert "朱印" in snap.conditions


# ──────────────────────────────────────
# GameState — 参加者管理
# ──────────────────────────────────────


class TestGameStateCombatants:
    def test_add_and_get_combatant(self):
        gs = GameState()
        gs.add_combatant(_make_pc("アリス"))
        result = gs.get_combatant("アリス")
        assert result is not None
        assert result.name == "アリス"

    def test_get_combatant_partial_match(self):
        gs = GameState()
        gs.add_combatant(_make_enemy("ゴブリンA"))
        result = gs.get_combatant("ゴブリン")
        assert result is not None
        assert "ゴブリン" in result.name

    def test_get_combatant_not_found(self):
        gs = GameState()
        result = gs.get_combatant("存在しない")
        assert result is None

    def test_remove_combatant(self):
        gs = GameState()
        gs.add_combatant(_make_pc("アリス"))
        removed = gs.remove_combatant("アリス")
        assert removed is True
        assert gs.get_combatant("アリス") is None

    def test_remove_nonexistent_combatant(self):
        gs = GameState()
        removed = gs.remove_combatant("存在しない")
        assert removed is False

    def test_players_property(self):
        gs = _make_state_with_combatants()
        players = gs.players
        assert len(players) == 2
        assert all(not p.is_enemy for p in players)

    def test_enemies_property(self):
        gs = _make_state_with_combatants()
        enemies = gs.enemies
        assert len(enemies) == 2
        assert all(e.is_enemy for e in enemies)

    def test_alive_combatants(self):
        gs = _make_state_with_combatants()
        gs.get_combatant("ゴブリンA").apply_damage(999)  # 撃破
        alive = gs.alive_combatants
        assert len(alive) == 3


# ──────────────────────────────────────
# GameState — 戦闘フェーズ管理
# ──────────────────────────────────────


class TestGameStateCombat:
    def test_initial_phase_is_exploration(self):
        gs = GameState()
        assert gs.phase == "exploration"

    def test_start_combat_sets_phase(self):
        gs = _make_state_with_combatants()
        gs.start_combat()
        assert gs.phase == "combat"

    def test_start_combat_sets_round_1(self):
        gs = _make_state_with_combatants()
        gs.start_combat()
        assert gs.round_number == 1

    def test_start_combat_returns_turn_order(self):
        gs = _make_state_with_combatants()
        order = gs.start_combat()
        # イニシアティブ降順
        assert order[0] == "アリス"   # initiative=8
        assert order[1] == "ボブ"     # initiative=5
        assert order[2] == "ゴブリンB"  # initiative=4
        assert order[3] == "ゴブリンA"  # initiative=3

    def test_start_combat_current_actor_is_highest_initiative(self):
        gs = _make_state_with_combatants()
        gs.start_combat()
        assert gs.current_actor == "アリス"

    def test_advance_turn_cycles(self):
        gs = _make_state_with_combatants()
        gs.start_combat()

        actor1, new_round = gs.advance_turn()
        assert actor1 == "ボブ"
        assert new_round is False

        actor2, new_round = gs.advance_turn()
        assert actor2 == "ゴブリンB"
        assert new_round is False

    def test_advance_turn_increments_round(self):
        gs = GameState()
        gs.add_combatant(_make_pc("アリス", initiative=5))
        gs.add_combatant(_make_enemy("敵", initiative=3))
        gs.start_combat()

        gs.advance_turn()  # ボブへ
        _, new_round = gs.advance_turn()  # ラウンド終了 → ラウンド2へ
        assert new_round is True
        assert gs.round_number == 2

    def test_end_combat_resets_phase(self):
        gs = _make_state_with_combatants()
        gs.start_combat()
        gs.end_combat()
        assert gs.phase == "exploration"
        assert gs.round_number == 0

    def test_end_combat_clears_turn_order(self):
        gs = _make_state_with_combatants()
        gs.start_combat()
        gs.end_combat()
        assert gs.turn_order == []
        assert gs.current_actor is None

    def test_current_actor_none_when_no_turn_order(self):
        gs = GameState()
        assert gs.current_actor is None

    def test_remove_combatant_adjusts_turn_order(self):
        gs = GameState()
        gs.add_combatant(_make_pc("アリス", initiative=8))
        gs.add_combatant(_make_enemy("敵", initiative=3))
        gs.start_combat()
        gs.remove_combatant("敵")
        assert "敵" not in gs.turn_order


# ──────────────────────────────────────
# GameState — summary()
# ──────────────────────────────────────


class TestGameStateSummary:
    def test_summary_contains_phase(self):
        gs = GameState()
        summary = gs.summary()
        assert "exploration" in summary

    def test_summary_contains_combatant_names(self):
        gs = _make_state_with_combatants()
        summary = gs.summary()
        assert "アリス" in summary
        assert "ゴブリンA" in summary

    def test_summary_contains_hp(self):
        gs = _make_state_with_combatants()
        summary = gs.summary()
        assert "10" in summary  # アリスの HP

    def test_summary_contains_conditions(self):
        gs = _make_state_with_combatants()
        gs.get_combatant("アリス").add_condition("出血")
        summary = gs.summary()
        assert "出血" in summary

    def test_summary_combat_shows_current_actor(self):
        gs = _make_state_with_combatants()
        gs.start_combat()
        summary = gs.summary()
        assert "アリス" in summary


# ──────────────────────────────────────
# GameState — シリアライズ・永続化
# ──────────────────────────────────────


class TestGameStateSerialization:
    def test_to_dict_from_dict_roundtrip(self):
        gs = _make_state_with_combatants()
        gs.start_combat()
        gs.advance_turn()

        d = gs.to_dict()
        restored = GameState.from_dict(d)

        assert restored.phase == gs.phase
        assert restored.round_number == gs.round_number
        assert list(restored.combatants.keys()) == list(gs.combatants.keys())

    def test_save_and_load(self, tmp_path):
        gs = _make_state_with_combatants()
        gs.get_combatant("アリス").add_condition("朱印")
        path = tmp_path / "game_state.json"

        gs.save(path)
        restored = GameState.load(path)

        assert restored.get_combatant("アリス").has_condition("朱印")
        assert len(restored.combatants) == len(gs.combatants)

    def test_save_creates_parent_directory(self, tmp_path):
        gs = GameState()
        gs.add_combatant(_make_pc())
        nested_path = tmp_path / "sessions" / "session1" / "game_state.json"
        gs.save(nested_path)
        assert nested_path.exists()

    def test_to_snapshot(self):
        from core.schemas import GameStateSnapshot

        gs = _make_state_with_combatants()
        snap = gs.to_snapshot()
        assert isinstance(snap, GameStateSnapshot)
        assert len(snap.combatants) == 4
        assert snap.phase == "exploration"
        assert snap.summary != ""
