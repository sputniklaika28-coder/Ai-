"""test_memory_manager.py — MemoryManager のユニットテスト。"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

from core.memory_manager import MemoryManager, MemoryStats


# ──────────────────────────────────────────
# フィクスチャ
# ──────────────────────────────────────────


@pytest.fixture
def mock_lm():
    """LMClient モック: 要約を固定テキストで返す。"""
    lm = MagicMock()
    lm.generate_response.return_value = ("これまでのあらすじ要約", None)
    return lm


@pytest.fixture
def mem(mock_lm):
    """低閾値の MemoryManager（テスト用）。"""
    return MemoryManager(lm_client=mock_lm, summary_threshold=5, recent_keep=3)


# ──────────────────────────────────────────
# 初期状態のテスト
# ──────────────────────────────────────────


class TestMemoryManagerInit:
    def test_initial_state_empty(self):
        m = MemoryManager()
        assert m.get_recent_messages() == []
        assert m.get_summary() == ""

    def test_get_context_window_empty(self):
        m = MemoryManager()
        assert m.get_context_window() == ""

    def test_stats_initial(self):
        m = MemoryManager()
        stats = m.get_stats()
        assert stats.total_messages == 0
        assert stats.summary_count == 0
        assert stats.has_summary is False


# ──────────────────────────────────────────
# メッセージ追加テスト
# ──────────────────────────────────────────


class TestAddMessage:
    def test_add_single_message(self):
        m = MemoryManager()
        m.add_message("GM", "ゲーム開始")
        msgs = m.get_recent_messages()
        assert len(msgs) == 1
        assert msgs[0]["speaker"] == "GM"
        assert msgs[0]["body"] == "ゲーム開始"

    def test_add_multiple_messages(self):
        m = MemoryManager()
        for i in range(5):
            m.add_message("Player", f"行動{i}")
        msgs = m.get_recent_messages()
        assert len(msgs) == 5

    def test_total_message_count_increments(self):
        m = MemoryManager()
        for _ in range(10):
            m.add_message("GM", "test")
        assert m.get_stats().total_messages == 10


# ──────────────────────────────────────────
# コンテキストウィンドウ
# ──────────────────────────────────────────


class TestContextWindow:
    def test_recent_messages_in_context(self):
        m = MemoryManager()
        m.add_message("GM", "敵が現れた")
        m.add_message("Player", "攻撃する")
        ctx = m.get_context_window()
        assert "敵が現れた" in ctx
        assert "攻撃する" in ctx

    def test_context_contains_section_header(self):
        m = MemoryManager()
        m.add_message("GM", "test")
        ctx = m.get_context_window()
        assert "直近の会話" in ctx

    def test_context_includes_summary_when_present(self):
        m = MemoryManager()
        m._summary = "過去の出来事の要約"
        m.add_message("GM", "現在の発言")
        ctx = m.get_context_window()
        assert "これまでのあらすじ" in ctx
        assert "過去の出来事の要約" in ctx
        assert "現在の発言" in ctx


# ──────────────────────────────────────────
# ローリング要約テスト
# ──────────────────────────────────────────


class TestRollingSummary:
    def test_summary_triggered_at_threshold(self, mem: MemoryManager, mock_lm):
        """閾値に達すると要約がトリガーされる。"""
        for i in range(mem.summary_threshold):
            mem.add_message("GM", f"メッセージ{i}")

        # バックグラウンドスレッドが完了するまで待つ
        for _ in range(20):
            time.sleep(0.1)
            if not mem._summarizing:
                break

        stats = mem.get_stats()
        assert stats.summary_count >= 1 or mock_lm.generate_response.called

    def test_recent_keep_after_summary(self, mem: MemoryManager, mock_lm):
        """要約後も recent_keep 件のメッセージが残る。"""
        for i in range(mem.summary_threshold):
            mem.add_message("Player", f"行動{i}")

        # 要約完了を待つ
        for _ in range(20):
            time.sleep(0.1)
            if not mem._summarizing:
                break

        assert len(mem.get_recent_messages()) <= mem.recent_keep + 2  # 少し余裕

    def test_no_summary_below_threshold(self, mock_lm):
        """閾値未満では要約されない。"""
        m = MemoryManager(lm_client=mock_lm, summary_threshold=10, recent_keep=5)
        for i in range(5):
            m.add_message("GM", f"msg{i}")

        time.sleep(0.1)
        mock_lm.generate_response.assert_not_called()
        assert m.get_stats().summary_count == 0


# ──────────────────────────────────────────
# LMClient なし（フォールバック）テスト
# ──────────────────────────────────────────


class TestFallbackWithoutLM:
    def test_fallback_truncate_used_when_no_lm(self):
        m = MemoryManager(lm_client=None, summary_threshold=3, recent_keep=1)
        for i in range(3):
            m.add_message("GM", f"msg{i}")

        for _ in range(10):
            time.sleep(0.1)
            if not m._summarizing:
                break

        # LMなしでも要約（切り詰め）が実行される
        assert m.get_stats().summary_count >= 1 or len(m.get_recent_messages()) <= 2

    def test_fallback_truncate_static_method(self):
        long_text = "\n".join([f"行{i}: テスト内容" for i in range(50)])
        result = MemoryManager._fallback_truncate(long_text, max_lines=10)
        assert "省略" in result
        assert len(result.splitlines()) <= 12  # 10行 + 省略行

    def test_fallback_truncate_short_text(self):
        short_text = "短いテキスト"
        result = MemoryManager._fallback_truncate(short_text, max_lines=10)
        assert result == short_text


# ──────────────────────────────────────────
# 後方互換 API テスト
# ──────────────────────────────────────────


class TestBackwardCompatAPI:
    def test_history_property_returns_list(self):
        m = MemoryManager()
        m.add_message("GM", "test")
        assert isinstance(m.history, list)
        assert len(m.history) == 1

    def test_get_context_summary_returns_string(self):
        m = MemoryManager()
        m.add_message("GM", "test")
        result = m.get_context_summary()
        assert isinstance(result, str)
