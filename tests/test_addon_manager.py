"""test_addon_manager.py — AddonManager の探索・ロード・集約テスト。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

from core.addons.addon_base import AddonContext, RuleSystemAddon, ToolAddon, ToolExecutionContext
from core.addons.addon_manager import AddonManager
from core.addons.addon_models import AddonManifest


# ──────────────────────────────────────────
# テスト用アドオンクラス定義（インメモリ）
# ──────────────────────────────────────────


SAMPLE_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "sample_tool",
        "description": "サンプルツール",
        "parameters": {"type": "object", "properties": {}},
    },
}

SAMPLE_TOOL_DEF_2 = {
    "type": "function",
    "function": {
        "name": "another_tool",
        "description": "別のツール",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _write_addon(addon_dir: Path, manifest_data: dict, addon_code: str) -> None:
    """テスト用アドオンファイルをtmp_pathに書き出す。"""
    addon_dir.mkdir(parents=True, exist_ok=True)
    (addon_dir / "addon.json").write_text(
        json.dumps(manifest_data, ensure_ascii=False), encoding="utf-8"
    )
    entry = manifest_data.get("entry_point", "addon.py")
    (addon_dir / entry).write_text(addon_code, encoding="utf-8")


@pytest.fixture
def addons_dir(tmp_path: Path) -> Path:
    return tmp_path / "addons"


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


# ──────────────────────────────────────────
# discover() テスト
# ──────────────────────────────────────────


class TestAddonManagerDiscover:
    def test_discover_finds_addon_json(self, addons_dir: Path):
        _write_addon(
            addons_dir / "test_addon",
            {
                "id": "test_addon",
                "name": "Test",
                "type": "tool",
                "class_name": "TestAddon",
            },
            "class TestAddon: pass",
        )
        mgr = AddonManager(addons_dir)
        manifests = mgr.discover()
        assert len(manifests) == 1
        assert manifests[0].id == "test_addon"

    def test_discover_empty_dir(self, addons_dir: Path):
        addons_dir.mkdir()
        mgr = AddonManager(addons_dir)
        manifests = mgr.discover()
        assert manifests == []

    def test_discover_nonexistent_dir(self, tmp_path: Path):
        mgr = AddonManager(tmp_path / "nonexistent")
        manifests = mgr.discover()
        assert manifests == []

    def test_discover_skips_invalid_manifest(self, addons_dir: Path):
        bad_dir = addons_dir / "bad_addon"
        bad_dir.mkdir(parents=True)
        (bad_dir / "addon.json").write_text('{"invalid": "no required fields"}', encoding="utf-8")

        good_dir = addons_dir / "good_addon"
        _write_addon(
            good_dir,
            {"id": "good_addon", "name": "Good", "type": "tool", "class_name": "GoodAddon"},
            "class GoodAddon: pass",
        )
        mgr = AddonManager(addons_dir)
        manifests = mgr.discover()
        assert len(manifests) == 1
        assert manifests[0].id == "good_addon"

    def test_discover_multiple_addons(self, addons_dir: Path):
        for i in range(3):
            _write_addon(
                addons_dir / f"addon_{i}",
                {"id": f"addon_{i}", "name": f"Addon {i}", "type": "tool", "class_name": f"Addon{i}"},
                f"class Addon{i}: pass",
            )
        mgr = AddonManager(addons_dir)
        manifests = mgr.discover()
        assert len(manifests) == 3


# ──────────────────────────────────────────
# load_addon() / load_all() テスト
# ──────────────────────────────────────────

TOOL_ADDON_CODE = '''
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))
from core.addons.addon_base import ToolAddon, AddonContext

class SampleToolAddon(ToolAddon):
    def on_load(self, context):
        self._loaded = True
    def get_tools(self):
        return [{"type": "function", "function": {"name": "sample_tool", "description": "test", "parameters": {"type": "object", "properties": {}}}}]
    def execute_tool(self, tool_name, tool_args, context):
        return False, '{"result": "ok"}'
'''

RULE_ADDON_CODE = '''
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))
from core.addons.addon_base import RuleSystemAddon, AddonContext

class SampleRuleAddon(RuleSystemAddon):
    def on_load(self, context):
        self._loaded = True
    def get_phase_keywords(self):
        return {"combat": ["戦闘開始"], "rest": ["休憩"]}
    def get_system_prompt_override(self):
        return "テストプロンプト"
    def get_world_setting(self):
        return "テスト世界観"
'''


class TestAddonManagerLoad:
    def test_load_tool_addon_success(self, addons_dir: Path, mock_context: AddonContext):
        _write_addon(
            addons_dir / "sample_tool",
            {"id": "sample_tool", "name": "Sample", "type": "tool", "class_name": "SampleToolAddon"},
            TOOL_ADDON_CODE,
        )
        mgr = AddonManager(addons_dir)
        mgr.discover()
        mgr.load_addon("sample_tool", mock_context)
        assert "sample_tool" in mgr.loaded_addons

    def test_load_nonexistent_addon_raises(self, addons_dir: Path, mock_context: AddonContext):
        addons_dir.mkdir()
        mgr = AddonManager(addons_dir)
        mgr.discover()
        with pytest.raises(ValueError):
            mgr.load_addon("nonexistent", mock_context)

    def test_load_all_loads_discovered(self, addons_dir: Path, mock_context: AddonContext):
        _write_addon(
            addons_dir / "sample_tool",
            {"id": "sample_tool", "name": "Sample", "type": "tool", "class_name": "SampleToolAddon"},
            TOOL_ADDON_CODE,
        )
        mgr = AddonManager(addons_dir)
        mgr.load_all(mock_context)
        assert "sample_tool" in mgr.loaded_addons

    def test_load_addon_twice_warns_and_skips(
        self, addons_dir: Path, mock_context: AddonContext, caplog
    ):
        _write_addon(
            addons_dir / "sample_tool",
            {"id": "sample_tool", "name": "Sample", "type": "tool", "class_name": "SampleToolAddon"},
            TOOL_ADDON_CODE,
        )
        mgr = AddonManager(addons_dir)
        mgr.discover()
        mgr.load_addon("sample_tool", mock_context)
        mgr.load_addon("sample_tool", mock_context)  # 2回目
        assert len(mgr.loaded_addons) == 1

    def test_only_one_rule_system_active(self, addons_dir: Path, mock_context: AddonContext):
        for name in ["rule1", "rule2"]:
            _write_addon(
                addons_dir / name,
                {"id": name, "name": name, "type": "rule_system", "class_name": "SampleRuleAddon"},
                RULE_ADDON_CODE,
            )
        mgr = AddonManager(addons_dir)
        mgr.discover()
        mgr.load_addon("rule1", mock_context)
        mgr.load_addon("rule2", mock_context)  # 2つ目はスキップされる
        rule_addons = [a for a in mgr.loaded_addons.values() if isinstance(a, RuleSystemAddon)]
        assert len(rule_addons) == 1


# ──────────────────────────────────────────
# get_all_tools() テスト
# ──────────────────────────────────────────


class TestAddonManagerTools:
    def test_get_all_tools_aggregates(self, addons_dir: Path, mock_context: AddonContext):
        _write_addon(
            addons_dir / "sample_tool",
            {"id": "sample_tool", "name": "Sample", "type": "tool", "class_name": "SampleToolAddon"},
            TOOL_ADDON_CODE,
        )
        mgr = AddonManager(addons_dir)
        mgr.load_all(mock_context)
        tools = mgr.get_all_tools()
        tool_names = [t["function"]["name"] for t in tools]
        assert "sample_tool" in tool_names

    def test_get_all_tools_empty_when_no_addons(self, addons_dir: Path):
        addons_dir.mkdir()
        mgr = AddonManager(addons_dir)
        assert mgr.get_all_tools() == []


# ──────────────────────────────────────────
# execute_tool() テスト
# ──────────────────────────────────────────


class TestAddonManagerDispatch:
    def test_execute_known_tool(self, addons_dir: Path, mock_context: AddonContext):
        _write_addon(
            addons_dir / "sample_tool",
            {"id": "sample_tool", "name": "Sample", "type": "tool", "class_name": "SampleToolAddon"},
            TOOL_ADDON_CODE,
        )
        mgr = AddonManager(addons_dir)
        mgr.load_all(mock_context)
        ctx = ToolExecutionContext(
            char_name="GM",
            tool_call_id="tc_1",
            adapter=MagicMock(),
            connector=MagicMock(),
        )
        finished, result = mgr.execute_tool("sample_tool", {}, ctx)
        assert finished is False
        assert result is not None

    def test_execute_unknown_tool_returns_error(self, addons_dir: Path):
        addons_dir.mkdir()
        mgr = AddonManager(addons_dir)
        ctx = ToolExecutionContext(
            char_name="GM",
            tool_call_id="tc_1",
            adapter=MagicMock(),
            connector=MagicMock(),
        )
        finished, result = mgr.execute_tool("nonexistent_tool", {}, ctx)
        assert finished is False
        assert "error" in result


# ──────────────────────────────────────────
# unload_addon() テスト
# ──────────────────────────────────────────


class TestAddonManagerUnload:
    def test_unload_removes_addon_and_tools(self, addons_dir: Path, mock_context: AddonContext):
        _write_addon(
            addons_dir / "sample_tool",
            {"id": "sample_tool", "name": "Sample", "type": "tool", "class_name": "SampleToolAddon"},
            TOOL_ADDON_CODE,
        )
        mgr = AddonManager(addons_dir)
        mgr.load_all(mock_context)
        assert "sample_tool" in mgr.loaded_addons

        mgr.unload_addon("sample_tool")
        assert "sample_tool" not in mgr.loaded_addons

        tools = mgr.get_all_tools()
        tool_names = [t["function"]["name"] for t in tools]
        assert "sample_tool" not in tool_names


# ──────────────────────────────────────────
# 依存関係解決テスト
# ──────────────────────────────────────────


class TestDependencyResolution:
    def test_dependency_loaded_first(self, addons_dir: Path, mock_context: AddonContext):
        """addon_b は addon_a に依存 → addon_a が先にロードされる。"""
        for name in ["addon_a", "addon_b"]:
            deps = [] if name == "addon_a" else [name.replace("b", "a")]
            _write_addon(
                addons_dir / name,
                {
                    "id": name,
                    "name": name,
                    "type": "tool",
                    "class_name": "SampleToolAddon",
                    "dependencies": deps,
                },
                TOOL_ADDON_CODE,
            )
        mgr = AddonManager(addons_dir)
        mgr.load_all(mock_context)
        order = mgr._load_order
        assert order.index("addon_a") < order.index("addon_b")
