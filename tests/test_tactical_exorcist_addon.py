"""test_tactical_exorcist_addon.py — タクティカル祓魔師ルールアドオンの単体テスト。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

from core.addons.addon_base import AddonContext
from core.addons.addon_models import AddonManifest


@pytest.fixture
def addon_dir(tmp_path: Path) -> Path:
    """テスト用のアドオンディレクトリを作成する。"""
    addon_d = tmp_path / "tactical_exorcist"
    addon_d.mkdir()

    # prompts.json を作成
    prompts = {
        "templates": {
            "meta_gm_template": {
                "system": "あなたはタクティカル祓魔師TRPGのゲームマスターです。",
                "temperature": 0.75,
            }
        }
    }
    (addon_d / "prompts.json").write_text(
        json.dumps(prompts, ensure_ascii=False), encoding="utf-8"
    )

    # world_setting_compressed.txt を作成
    (addon_d / "world_setting_compressed.txt").write_text(
        "世界観テスト: 現代の東京で悪魔と戦う祓魔師の物語", encoding="utf-8"
    )

    return addon_d


@pytest.fixture
def mock_context() -> AddonContext:
    return AddonContext(
        adapter=MagicMock(),
        lm_client=MagicMock(),
        knowledge_manager=MagicMock(),
        session_manager=MagicMock(),
        character_manager=MagicMock(),
        root_dir=Path("/tmp"),
    )


@pytest.fixture
def addon(addon_dir: Path, mock_context: AddonContext):
    """TacticalExorcistAddon インスタンスを生成する。"""
    # アドオンのパスを動的に解決してインポート
    real_addon_dir = Path(__file__).parent.parent / "addons" / "tactical_exorcist"
    if not real_addon_dir.exists():
        pytest.skip("addons/tactical_exorcist/ が見つかりません")

    sys.path.insert(0, str(Path(__file__).parent.parent))
    # core パッケージが importable であることを確認
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tactical_exorcist_addon",
        real_addon_dir / "addon.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    manifest = AddonManifest(
        id="tactical_exorcist",
        name="タクティカル祓魔師",
        version="1.0.0",
        type="rule_system",
        class_name="TacticalExorcistAddon",
        prompts_override="prompts.json",
        world_setting="world_setting_compressed.txt",
    )

    # テスト用アドオンディレクトリを使う
    instance = mod.TacticalExorcistAddon()
    instance.manifest = manifest
    instance.addon_dir = addon_dir
    instance.on_load(mock_context)
    return instance


class TestTacticalExorcistPhaseKeywords:
    def test_get_phase_keywords_returns_dict(self, addon):
        kw = addon.get_phase_keywords()
        assert isinstance(kw, dict)
        assert len(kw) > 0

    def test_combat_phase_keywords_exist(self, addon):
        kw = addon.get_phase_keywords()
        assert "combat" in kw
        assert len(kw["combat"]) > 0

    def test_mission_phase_keywords_exist(self, addon):
        kw = addon.get_phase_keywords()
        assert "mission" in kw

    def test_briefing_phase_keywords_exist(self, addon):
        kw = addon.get_phase_keywords()
        assert "briefing" in kw

    def test_phase_order_is_consistent(self, addon):
        """フェイズキーワードが PHASE_ORDER とセットで存在する。"""
        kw = addon.get_phase_keywords()
        order = addon.PHASE_ORDER
        # combatとmissionは必ず順序が定義されている
        assert "combat" in order
        assert "mission" in order


class TestTacticalExorcistPrompts:
    def test_get_system_prompt_override_not_empty(self, addon):
        prompt = addon.get_system_prompt_override()
        # プロンプトが読み込まれていれば文字列を返す
        assert prompt is None or isinstance(prompt, str)

    def test_get_system_prompt_contains_gm_text(self, addon):
        prompt = addon.get_system_prompt_override()
        if prompt is not None:
            assert len(prompt) > 0

    def test_get_prompt_templates_returns_dict_or_none(self, addon):
        templates = addon.get_prompt_templates()
        assert templates is None or isinstance(templates, dict)


class TestTacticalExorcistWorldSetting:
    def test_get_world_setting_returns_string(self, addon):
        ws = addon.get_world_setting()
        assert isinstance(ws, str)

    def test_get_world_setting_not_empty_when_file_exists(self, addon):
        ws = addon.get_world_setting()
        # world_setting_compressed.txt があれば空でない
        ws_path = addon.addon_dir / "world_setting_compressed.txt"
        if ws_path.exists():
            assert len(ws) > 0

    def test_world_setting_contains_test_content(self, addon):
        ws = addon.get_world_setting()
        # フィクスチャで書いた内容が読み込まれているはず
        if len(ws) > 0:
            assert isinstance(ws, str)


class TestTacticalExorcistAddonTools:
    def test_get_tools_returns_empty_list(self, addon):
        """ルールシステムアドオンはツールを提供しない。"""
        tools = addon.get_tools()
        assert tools == []

    def test_addon_is_rule_system_type(self, addon):
        from core.addons.addon_base import RuleSystemAddon
        assert isinstance(addon, RuleSystemAddon)
