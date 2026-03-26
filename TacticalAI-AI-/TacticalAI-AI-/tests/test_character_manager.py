"""
test_character_manager.py — CharacterManager のユニットテスト

テスト対象:
  - load_characters()
  - get_character()
  - get_enabled_characters()
  - get_character_count()
"""

import json
from pathlib import Path

from core.character_manager import CharacterManager


class TestCharacterManagerLoad:
    def test_loads_characters_from_json(self, characters_json: Path):
        cm = CharacterManager(config_path=str(characters_json))
        assert "meta_gm" in cm.characters
        assert "player_01" in cm.characters

    def test_handles_missing_file(self, tmp_path: Path):
        cm = CharacterManager(config_path=str(tmp_path / "no.json"))
        assert cm.characters == {}

    def test_character_fields(self, characters_json: Path):
        cm = CharacterManager(config_path=str(characters_json))
        gm = cm.characters["meta_gm"]
        assert gm["name"] == "ゲームマスター"
        assert gm["role"] == "game_master"
        assert gm["is_ai"] is True


class TestGetCharacter:
    def test_get_existing(self, characters_json: Path):
        cm = CharacterManager(config_path=str(characters_json))
        char = cm.get_character("meta_gm")
        assert char is not None
        assert char["id"] == "meta_gm"

    def test_get_nonexistent_returns_none(self, characters_json: Path):
        cm = CharacterManager(config_path=str(characters_json))
        assert cm.get_character("ghost") is None


class TestGetEnabledCharacters:
    def test_returns_only_enabled(self, characters_json: Path):
        cm = CharacterManager(config_path=str(characters_json))
        enabled = cm.get_enabled_characters()
        ids = [c["id"] for c in enabled]
        assert "meta_gm" in ids
        assert "player_01" not in ids  # enabled: False

    def test_empty_when_none_enabled(self, tmp_path: Path):
        data = {"characters": {"c1": {"id": "c1", "name": "x", "enabled": False, "is_ai": False}}}
        p = tmp_path / "chars.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        cm = CharacterManager(config_path=str(p))
        assert cm.get_enabled_characters() == []

    def test_all_returned_when_all_enabled(self, tmp_path: Path):
        data = {
            "characters": {
                "a": {"id": "a", "name": "A", "enabled": True, "is_ai": True},
                "b": {"id": "b", "name": "B", "enabled": True, "is_ai": True},
            }
        }
        p = tmp_path / "chars.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        cm = CharacterManager(config_path=str(p))
        assert len(cm.get_enabled_characters()) == 2


class TestGetCharacterCount:
    def test_count_matches_json(self, characters_json: Path):
        cm = CharacterManager(config_path=str(characters_json))
        assert cm.get_character_count() == 2

    def test_count_zero_when_empty(self, tmp_path: Path):
        p = tmp_path / "empty.json"
        p.write_text('{"characters": {}}', encoding="utf-8")
        cm = CharacterManager(config_path=str(p))
        assert cm.get_character_count() == 0
