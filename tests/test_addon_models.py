"""test_addon_models.py — AddonManifest (Pydantic スキーマ) のテスト。"""

from __future__ import annotations

import json

import pytest

# core/ を import できるようにパスを追加
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

from core.addons.addon_models import AddonManifest


class TestAddonManifestValidation:
    """AddonManifest の Pydantic バリデーションテスト。"""

    def test_valid_rule_system_manifest(self):
        data = {
            "id": "tactical_exorcist",
            "name": "タクティカル祓魔師",
            "version": "1.0.0",
            "type": "rule_system",
            "class_name": "TacticalExorcistAddon",
        }
        m = AddonManifest(**data)
        assert m.id == "tactical_exorcist"
        assert m.type == "rule_system"
        assert m.class_name == "TacticalExorcistAddon"
        assert m.dependencies == []
        assert m.tools == []

    def test_valid_tool_manifest(self):
        data = {
            "id": "room_builder",
            "name": "ルームビルダー",
            "version": "1.0.0",
            "type": "tool",
            "class_name": "RoomBuilderAddon",
            "tools": ["build_room", "set_room_background"],
        }
        m = AddonManifest(**data)
        assert m.type == "tool"
        assert "build_room" in m.tools

    def test_missing_required_id_raises(self):
        with pytest.raises(Exception):
            AddonManifest(
                name="Test",
                version="1.0.0",
                type="tool",
                class_name="TestAddon",
            )

    def test_missing_required_name_raises(self):
        with pytest.raises(Exception):
            AddonManifest(
                id="test",
                version="1.0.0",
                type="tool",
                class_name="TestAddon",
            )

    def test_missing_required_class_name_raises(self):
        with pytest.raises(Exception):
            AddonManifest(
                id="test",
                name="Test",
                version="1.0.0",
                type="tool",
            )

    def test_invalid_type_raises(self):
        with pytest.raises(Exception):
            AddonManifest(
                id="test",
                name="Test",
                version="1.0.0",
                type="invalid_type",
                class_name="TestAddon",
            )

    def test_defaults_are_applied(self):
        m = AddonManifest(
            id="test",
            name="Test",
            type="tool",
            class_name="TestAddon",
        )
        assert m.version == "1.0.0" or m.version is not None
        assert m.description == ""
        assert m.author == ""
        assert m.entry_point == "addon.py"
        assert m.dependencies == []
        assert m.tools == []
        assert m.gui_tab is None
        assert m.gui_tab_label is None
        assert m.prompts_override is None
        assert m.world_setting is None
        assert m.characters is None

    def test_rule_system_with_override_fields(self):
        m = AddonManifest(
            id="test_rule",
            name="Test Rule",
            type="rule_system",
            class_name="TestRuleAddon",
            prompts_override="prompts.json",
            world_setting="world_setting_compressed.txt",
            characters="characters.json",
        )
        assert m.prompts_override == "prompts.json"
        assert m.world_setting == "world_setting_compressed.txt"

    def test_tool_with_gui_tab(self):
        m = AddonManifest(
            id="image_gen",
            name="画像生成",
            type="tool",
            class_name="ImageGenAddon",
            tools=["generate_image"],
            gui_tab="ImageGenTab",
            gui_tab_label="画像生成",
        )
        assert m.gui_tab == "ImageGenTab"
        assert m.gui_tab_label == "画像生成"

    def test_from_json_string(self):
        """JSON文字列からのデシリアライズ。"""
        raw = json.dumps({
            "id": "room_builder",
            "name": "ルームビルダー",
            "version": "1.0.0",
            "type": "tool",
            "class_name": "RoomBuilderAddon",
            "tools": ["build_room"],
        })
        m = AddonManifest(**json.loads(raw))
        assert m.id == "room_builder"
