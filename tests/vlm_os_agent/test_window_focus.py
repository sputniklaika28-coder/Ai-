"""window_focus.py のユニットテスト（pygetwindow を mock）。"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_pygetwindow(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    m = MagicMock()
    fake = types.ModuleType("pygetwindow")
    fake.getAllTitles = m.getAllTitles  # type: ignore[attr-defined]
    fake.getWindowsWithTitle = m.getWindowsWithTitle  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pygetwindow", fake)
    return m


class TestListWindowTitles:
    def test_returns_titles(self, fake_pygetwindow) -> None:
        from addons.vlm_os_agent.window_focus import list_window_titles

        fake_pygetwindow.getAllTitles.return_value = ["Chrome", "", "ココフォリア"]
        assert list_window_titles() == ["Chrome", "ココフォリア"]

    def test_no_pygetwindow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from addons.vlm_os_agent.window_focus import list_window_titles

        monkeypatch.setitem(sys.modules, "pygetwindow", None)
        assert list_window_titles() == []

    def test_exception_yields_empty(self, fake_pygetwindow) -> None:
        from addons.vlm_os_agent.window_focus import list_window_titles

        fake_pygetwindow.getAllTitles.side_effect = RuntimeError("x11 error")
        assert list_window_titles() == []


class TestFindWindow:
    def _make_window(self, left=0, top=0, width=100, height=200) -> MagicMock:
        w = MagicMock()
        w.left = left
        w.top = top
        w.width = width
        w.height = height
        return w

    def test_regex_match(self, fake_pygetwindow) -> None:
        from addons.vlm_os_agent.window_focus import find_window

        win = self._make_window(10, 20, 100, 200)
        fake_pygetwindow.getAllTitles.return_value = ["Chrome - Gmail", "ココフォリア"]
        fake_pygetwindow.getWindowsWithTitle.side_effect = (
            lambda t: [win] if t == "ココフォリア" else []
        )
        info = find_window("ココ")
        assert info is not None
        assert info.title == "ココフォリア"
        assert info.bbox == (10, 20, 110, 220)

    def test_no_match(self, fake_pygetwindow) -> None:
        from addons.vlm_os_agent.window_focus import find_window

        fake_pygetwindow.getAllTitles.return_value = ["Notepad"]
        fake_pygetwindow.getWindowsWithTitle.return_value = []
        assert find_window("Chrome") is None

    def test_empty_pattern(self, fake_pygetwindow) -> None:
        from addons.vlm_os_agent.window_focus import find_window

        assert find_window("") is None

    def test_invalid_regex_falls_back_to_escape(self, fake_pygetwindow) -> None:
        from addons.vlm_os_agent.window_focus import find_window

        win = self._make_window()
        # "[invalid(" は unbalanced で re.compile が re.error を投げる → escape へ
        fake_pygetwindow.getAllTitles.return_value = ["[invalid("]
        fake_pygetwindow.getWindowsWithTitle.return_value = [win]
        info = find_window("[invalid(")
        assert info is not None


class TestFocus:
    def test_returns_false_for_none(self) -> None:
        from addons.vlm_os_agent.window_focus import focus

        assert focus(None) is False

    def test_activate_success(self) -> None:
        from addons.vlm_os_agent.window_focus import WindowInfo, focus

        w = MagicMock()
        info = WindowInfo(title="x", bbox=(0, 0, 10, 10), handle=w)
        assert focus(info) is True
        w.activate.assert_called()
