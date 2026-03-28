"""
test_browser_use_agent.py — Browser Use エージェントラッパーのユニットテスト

BrowserUseAgentWrapper のタスクテンプレート読み込みと
AgentTaskResult データクラスをテストする。
Browser Use ライブラリが未インストールの環境でも動作する。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.browser_use_agent import AgentTaskResult, _load_task_templates

# ──────────────────────────────────────────
# AgentTaskResult テスト
# ──────────────────────────────────────────


class TestAgentTaskResult:
    def test_default_values(self):
        r = AgentTaskResult(success=True)
        assert r.success is True
        assert r.output == ""
        assert r.error == ""
        assert r.steps == 0
        assert r.extra == {}

    def test_custom_values(self):
        r = AgentTaskResult(
            success=False,
            output="done",
            error="timeout",
            steps=5,
            extra={"url": "https://example.com"},
        )
        assert r.success is False
        assert r.output == "done"
        assert r.error == "timeout"
        assert r.steps == 5
        assert r.extra == {"url": "https://example.com"}


# ──────────────────────────────────────────
# タスクテンプレート読み込みテスト
# ──────────────────────────────────────────


class TestLoadTaskTemplates:
    def test_loads_existing_templates(self):
        templates = _load_task_templates()
        assert isinstance(templates, dict)
        assert "send_chat" in templates
        assert "create_room" in templates

    def test_template_has_placeholders(self):
        templates = _load_task_templates()
        assert "{character_name}" in templates["send_chat"]
        assert "{text}" in templates["send_chat"]

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        with patch("core.browser_use_agent._TASKS_PATH", tmp_path / "nonexistent.json"):
            result = _load_task_templates()
            assert result == {}

    def test_format_template(self):
        templates = _load_task_templates()
        formatted = templates["send_chat"].format(
            character_name="GM", text="Hello"
        )
        assert "GM" in formatted
        assert "Hello" in formatted


# ──────────────────────────────────────────
# BrowserUseAgentWrapper テスト（browser-use未インストール時）
# ──────────────────────────────────────────


class TestBrowserUseAgentWrapperImportGuard:
    def test_raises_when_browser_use_not_installed(self):
        """browser-use が未インストールの場合 ModuleNotFoundError を投げる"""
        with patch("core.browser_use_agent._HAS_BROWSER_USE", False):
            from core.browser_use_agent import BrowserUseAgentWrapper

            with pytest.raises(ModuleNotFoundError, match="browser-use"):
                BrowserUseAgentWrapper()
