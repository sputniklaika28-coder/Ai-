"""kill_switch.py のユニットテスト。"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest
from addons.vlm_os_agent.kill_switch import AgentCancelled, KillSwitch


class TestBasic:
    def test_default_not_set(self) -> None:
        ks = KillSwitch()
        assert ks.is_set() is False

    def test_set_and_reset(self) -> None:
        ks = KillSwitch()
        ks.set("test")
        assert ks.is_set() is True
        ks.reset()
        assert ks.is_set() is False

    def test_raise_if_set_when_clear(self) -> None:
        ks = KillSwitch()
        # 例外が出ないこと
        ks.raise_if_set()

    def test_raise_if_set_when_fired(self) -> None:
        ks = KillSwitch()
        ks.set("x")
        with pytest.raises(AgentCancelled):
            ks.raise_if_set()


class TestListenerLifecycle:
    def test_start_without_pynput(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pynput 未導入時は False を返し、例外にならない。"""
        # pynput のトップレベルが ImportError になるよう偽装
        monkeypatch.setitem(sys.modules, "pynput", None)
        ks = KillSwitch()
        # import pynput が ImportError になるので False
        assert ks.start() is False

    def test_start_with_mock_pynput(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pynput を mock して ESC 押下で set() されることを確認。"""
        captured: dict[str, object] = {}

        class FakeKey:
            esc = object()

        fake_listener_inst = MagicMock()

        class FakeListener:
            def __init__(self, on_press=None, **kwargs):  # noqa: ANN001
                captured["on_press"] = on_press
                captured["kwargs"] = kwargs
                self._inst = fake_listener_inst

            def start(self) -> None:
                fake_listener_inst.start()

            def stop(self) -> None:
                fake_listener_inst.stop()

        fake_keyboard = types.SimpleNamespace(Key=FakeKey, Listener=FakeListener)
        fake_pynput = types.ModuleType("pynput")
        fake_pynput.keyboard = fake_keyboard  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pynput", fake_pynput)
        monkeypatch.setitem(sys.modules, "pynput.keyboard", fake_keyboard)  # type: ignore[arg-type]

        ks = KillSwitch()
        assert ks.start() is True

        # on_press コールバックを取り出して ESC キーを渡す
        on_press = captured["on_press"]
        assert callable(on_press)
        on_press(FakeKey.esc)  # type: ignore[operator]
        assert ks.is_set() is True

        ks.stop()
        fake_listener_inst.stop.assert_called()

    def test_double_start_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeKey:
            esc = object()

        class FakeListener:
            def __init__(self, on_press=None, **kwargs):  # noqa: ANN001
                pass

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

        fake_keyboard = types.SimpleNamespace(Key=FakeKey, Listener=FakeListener)
        fake_pynput = types.ModuleType("pynput")
        fake_pynput.keyboard = fake_keyboard  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pynput", fake_pynput)
        monkeypatch.setitem(sys.modules, "pynput.keyboard", fake_keyboard)  # type: ignore[arg-type]

        ks = KillSwitch()
        assert ks.start() is True
        assert ks.start() is True  # 2 回目は noop
