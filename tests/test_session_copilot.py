"""
test_session_copilot.py — SessionCoPilot のユニットテスト

シーン管理、イベントルール、アクション実行、モード切替をテストする。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from core.session_copilot import (
    ActionResult,
    EventRule,
    SceneDefinition,
    SessionCoPilot,
)

# ──────────────────────────────────────────
# フィクスチャ
# ──────────────────────────────────────────


@pytest.fixture()
def mock_adapter():
    adapter = MagicMock()
    result_ok = MagicMock()
    result_ok.success = True
    result_ok.error = ""
    adapter.create_room.return_value = result_ok
    adapter.set_background.return_value = result_ok
    adapter.switch_bgm.return_value = result_ok
    adapter.upload_asset.return_value = "https://example.com/asset"
    adapter.spawn_piece.return_value = True
    adapter.send_chat.return_value = True
    adapter.get_vision_controller.return_value = MagicMock()
    return adapter


@pytest.fixture()
def copilot(mock_adapter):
    return SessionCoPilot(adapter=mock_adapter, mode="auto")


@pytest.fixture()
def sample_scene():
    return SceneDefinition(
        name="廃神社",
        description="崩れた鳥居が見える",
        background_image="",
        bgm=[],
        characters=[],
    )


@pytest.fixture()
def sample_rule():
    return EventRule(
        name="combat_start",
        pattern="戦闘開始",
        action="bgm",
        params={"bgm_name": "battle_theme"},
    )


# ──────────────────────────────────────────
# SceneDefinition
# ──────────────────────────────────────────


class TestSceneDefinition:
    def test_from_dict(self):
        data = {
            "name": "ダンジョン",
            "description": "暗い洞窟",
            "background_image": "/path/bg.png",
            "bgm": [{"file_path": "/path/music.mp3", "name": "cave"}],
            "characters": [{"name": "戦士", "position": "入口"}],
            "metadata": {"difficulty": "hard"},
        }
        scene = SceneDefinition.from_dict(data)
        assert scene.name == "ダンジョン"
        assert scene.description == "暗い洞窟"
        assert len(scene.bgm) == 1
        assert len(scene.characters) == 1
        assert scene.metadata["difficulty"] == "hard"

    def test_from_dict_minimal(self):
        scene = SceneDefinition.from_dict({"name": "空シーン"})
        assert scene.name == "空シーン"
        assert scene.bgm == []
        assert scene.characters == []

    def test_to_room_definition_dict(self):
        scene = SceneDefinition(
            name="test",
            description="desc",
            background_image="/bg.png",
            bgm=[{"file_path": "/m.mp3"}],
            characters=[{"name": "A"}],
        )
        d = scene.to_room_definition_dict()
        assert d["name"] == "test"
        assert d["background_image"] == "/bg.png"
        assert len(d["bgm"]) == 1
        assert len(d["characters"]) == 1


# ──────────────────────────────────────────
# EventRule
# ──────────────────────────────────────────


class TestEventRule:
    def test_matches_simple(self, sample_rule):
        assert sample_rule.matches("戦闘開始！") is not None

    def test_no_match(self, sample_rule):
        assert sample_rule.matches("平和な日常") is None

    def test_disabled_rule(self):
        rule = EventRule(name="x", pattern="test", action="bgm", enabled=False)
        assert rule.matches("this is a test") is None

    def test_case_insensitive(self):
        rule = EventRule(name="x", pattern="battle", action="bgm")
        assert rule.matches("BATTLE start") is not None

    def test_regex_pattern(self):
        rule = EventRule(name="x", pattern=r"HP\s*[:：]\s*\d+", action="narration")
        assert rule.matches("HP: 10") is not None
        assert rule.matches("HPは十分です") is None

    def test_invalid_regex(self):
        rule = EventRule(name="bad", pattern="[invalid", action="bgm")
        assert rule._compiled is None
        assert rule.matches("anything") is None

    def test_from_dict(self):
        data = {
            "name": "test",
            "pattern": "トリガー",
            "action": "transition",
            "params": {"scene": "next"},
            "enabled": True,
        }
        rule = EventRule.from_dict(data)
        assert rule.name == "test"
        assert rule.action == "transition"
        assert rule.params["scene"] == "next"


# ──────────────────────────────────────────
# ActionResult
# ──────────────────────────────────────────


class TestActionResult:
    def test_defaults(self):
        r = ActionResult(rule_name="r", action="bgm", success=True)
        assert r.detail == ""
        assert r.error == ""

    def test_with_error(self):
        r = ActionResult(rule_name="r", action="bgm", success=False, error="失敗")
        assert r.error == "失敗"


# ──────────────────────────────────────────
# シーン管理
# ──────────────────────────────────────────


class TestSceneManagement:
    def test_register_scene(self, copilot, sample_scene):
        copilot.register_scene(sample_scene)
        assert "廃神社" in copilot.list_scenes()
        assert copilot.get_scene("廃神社") is sample_scene

    def test_register_scenes(self, copilot):
        scenes = [
            SceneDefinition(name="A"),
            SceneDefinition(name="B"),
        ]
        copilot.register_scenes(scenes)
        assert len(copilot.list_scenes()) == 2

    def test_get_scene_not_found(self, copilot):
        assert copilot.get_scene("nonexistent") is None

    def test_transition_to(self, copilot, sample_scene):
        copilot.register_scene(sample_scene)
        results = copilot.transition_to("廃神社")
        assert copilot.current_scene == "廃神社"
        assert "廃神社" in copilot.scene_history
        assert len(results) >= 1

    def test_transition_unknown_scene(self, copilot):
        results = copilot.transition_to("存在しないシーン")
        assert any(not r["success"] for r in results)

    def test_transition_no_adapter(self):
        copilot = SessionCoPilot(adapter=None)
        copilot.register_scene(SceneDefinition(name="X"))
        results = copilot.transition_to("X")
        assert any(not r["success"] for r in results)

    def test_scene_history_tracks_transitions(self, copilot):
        copilot.register_scene(SceneDefinition(name="A"))
        copilot.register_scene(SceneDefinition(name="B"))
        copilot.transition_to("A")
        copilot.transition_to("B")
        assert copilot.scene_history == ["A", "B"]
        assert copilot.current_scene == "B"

    def test_transition_with_assets(self, copilot, mock_adapter, tmp_path):
        bg = tmp_path / "bg.png"
        bg.write_bytes(b"\x89PNG")
        scene = SceneDefinition(
            name="battle",
            background_image=str(bg),
            bgm=[{"file_path": str(tmp_path / "x.mp3"), "name": "battle"}],
            characters=[{"name": "goblin", "ccfolia_data": {"name": "goblin"}}],
        )
        copilot.register_scene(scene)
        results = copilot.transition_to("battle")
        # set_background + add_bgm + place_character = 3 steps
        assert len(results) == 3

    def test_load_scenes_from_file(self, copilot, tmp_path):
        scenes_file = tmp_path / "scenes.json"
        scenes_data = {
            "scenes": [
                {"name": "scene1", "description": "first"},
                {"name": "scene2", "description": "second"},
            ]
        }
        scenes_file.write_text(json.dumps(scenes_data), encoding="utf-8")
        count = copilot.load_scenes_from_file(str(scenes_file))
        assert count == 2
        assert "scene1" in copilot.list_scenes()

    def test_load_scenes_missing_file(self, copilot):
        count = copilot.load_scenes_from_file("/nonexistent.json")
        assert count == 0

    def test_load_scenes_list_format(self, copilot, tmp_path):
        """scenes が配列の場合もサポート。"""
        scenes_file = tmp_path / "scenes.json"
        scenes_data = [{"name": "A"}, {"name": "B"}]
        scenes_file.write_text(json.dumps(scenes_data), encoding="utf-8")
        count = copilot.load_scenes_from_file(str(scenes_file))
        assert count == 2


# ──────────────────────────────────────────
# イベントルール
# ──────────────────────────────────────────


class TestEventRuleManagement:
    def test_add_rule(self, copilot, sample_rule):
        copilot.add_rule(sample_rule)
        assert len(copilot.event_rules) == 1

    def test_add_rules(self, copilot):
        rules = [
            EventRule(name="a", pattern="test1", action="bgm"),
            EventRule(name="b", pattern="test2", action="narration"),
        ]
        copilot.add_rules(rules)
        assert len(copilot.event_rules) == 2

    def test_remove_rule(self, copilot, sample_rule):
        copilot.add_rule(sample_rule)
        assert copilot.remove_rule("combat_start") is True
        assert len(copilot.event_rules) == 0

    def test_remove_nonexistent_rule(self, copilot):
        assert copilot.remove_rule("nonexistent") is False


# ──────────────────────────────────────────
# メッセージ処理 (auto モード)
# ──────────────────────────────────────────


class TestProcessMessageAuto:
    def test_bgm_action(self, copilot, mock_adapter):
        copilot.add_rule(EventRule(
            name="battle_bgm", pattern="戦闘開始", action="bgm",
            params={"bgm_name": "battle_theme"},
        ))
        results = copilot.process_message("GM", "戦闘開始！")
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].action == "bgm"
        mock_adapter.switch_bgm.assert_called_once_with("battle_theme")

    def test_narration_action(self, copilot, mock_adapter):
        copilot.add_rule(EventRule(
            name="entrance", pattern="入場", action="narration",
            params={"text": "暗い洞窟に入った…", "character": "GM"},
        ))
        results = copilot.process_message("Player", "入場します")
        assert len(results) == 1
        assert results[0].success is True
        mock_adapter.send_chat.assert_called_once_with("GM", "暗い洞窟に入った…")

    def test_spawn_action(self, copilot, mock_adapter):
        copilot.add_rule(EventRule(
            name="enemy", pattern="敵出現", action="spawn",
            params={"ccfolia_data": {"name": "ゴブリン"}},
        ))
        results = copilot.process_message("GM", "敵出現！")
        assert len(results) == 1
        assert results[0].success is True
        mock_adapter.spawn_piece.assert_called_once_with({"name": "ゴブリン"})

    def test_transition_action(self, copilot, mock_adapter):
        copilot.register_scene(SceneDefinition(name="dungeon"))
        copilot.add_rule(EventRule(
            name="enter_dungeon", pattern="ダンジョンに入る", action="transition",
            params={"scene": "dungeon"},
        ))
        results = copilot.process_message("Player", "ダンジョンに入る")
        assert len(results) == 1
        assert copilot.current_scene == "dungeon"

    def test_no_match(self, copilot):
        copilot.add_rule(EventRule(name="x", pattern="特殊", action="bgm"))
        results = copilot.process_message("Player", "普通の発言")
        assert len(results) == 0

    def test_multiple_rules_match(self, copilot, mock_adapter):
        copilot.add_rule(EventRule(
            name="bgm", pattern="戦闘", action="bgm",
            params={"bgm_name": "battle"},
        ))
        copilot.add_rule(EventRule(
            name="narr", pattern="戦闘", action="narration",
            params={"text": "戦いだ！", "character": "GM"},
        ))
        results = copilot.process_message("GM", "戦闘開始")
        assert len(results) == 2

    def test_action_log(self, copilot, mock_adapter):
        copilot.add_rule(EventRule(
            name="test", pattern="テスト", action="bgm",
            params={"bgm_name": "test_bgm"},
        ))
        copilot.process_message("P", "テスト")
        assert len(copilot.action_log) == 1

    def test_bgm_missing_param(self, copilot):
        copilot.add_rule(EventRule(
            name="bad_bgm", pattern="bgm", action="bgm", params={},
        ))
        results = copilot.process_message("P", "bgm")
        assert results[0].success is False

    def test_narration_missing_text(self, copilot):
        copilot.add_rule(EventRule(
            name="bad", pattern="test", action="narration", params={},
        ))
        results = copilot.process_message("P", "test")
        assert results[0].success is False

    def test_spawn_missing_data(self, copilot):
        copilot.add_rule(EventRule(
            name="bad", pattern="test", action="spawn", params={},
        ))
        results = copilot.process_message("P", "test")
        assert results[0].success is False

    def test_unknown_action(self, copilot):
        copilot.add_rule(EventRule(
            name="x", pattern="test", action="unknown_action",
        ))
        results = copilot.process_message("P", "test")
        assert results[0].success is False


# ──────────────────────────────────────────
# assist モード
# ──────────────────────────────────────────


class TestAssistMode:
    def test_assist_does_not_execute(self, mock_adapter):
        copilot = SessionCoPilot(adapter=mock_adapter, mode="assist")
        copilot.add_rule(EventRule(
            name="test", pattern="trigger", action="bgm",
            params={"bgm_name": "test"},
        ))
        results = copilot.process_message("P", "trigger word")
        assert len(results) == 1
        assert results[0].success is True
        assert "[提案]" in results[0].detail
        mock_adapter.switch_bgm.assert_not_called()


# ──────────────────────────────────────────
# モード切替
# ──────────────────────────────────────────


class TestMode:
    def test_default_mode(self, copilot):
        assert copilot.mode == "auto"

    def test_set_mode(self, copilot):
        copilot.mode = "assist"
        assert copilot.mode == "assist"

    def test_invalid_mode_ignored(self, copilot):
        copilot.mode = "invalid"
        assert copilot.mode == "auto"  # unchanged


# ──────────────────────────────────────────
# プロパティ
# ──────────────────────────────────────────


class TestProperties:
    def test_scenes_is_copy(self, copilot, sample_scene):
        copilot.register_scene(sample_scene)
        scenes = copilot.scenes
        scenes.clear()
        assert len(copilot.scenes) == 1

    def test_event_rules_is_copy(self, copilot, sample_rule):
        copilot.add_rule(sample_rule)
        rules = copilot.event_rules
        rules.clear()
        assert len(copilot.event_rules) == 1

    def test_action_log_is_copy(self, copilot, mock_adapter):
        copilot.add_rule(EventRule(
            name="t", pattern="t", action="bgm", params={"bgm_name": "x"},
        ))
        copilot.process_message("P", "t")
        log = copilot.action_log
        log.clear()
        assert len(copilot.action_log) == 1

    def test_scene_history_is_copy(self, copilot):
        copilot.register_scene(SceneDefinition(name="A"))
        copilot.transition_to("A")
        history = copilot.scene_history
        history.clear()
        assert len(copilot.scene_history) == 1
