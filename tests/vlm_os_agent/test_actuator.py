"""actuator.py のユニットテスト（pyautogui / pyperclip を mock）。"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest
from addons.vlm_os_agent.kill_switch import AgentCancelled, KillSwitch


@pytest.fixture
def fake_pyautogui(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    m = MagicMock()
    m.FAILSAFE = False
    m.PAUSE = 0.0
    fake = types.ModuleType("pyautogui")
    for attr in (
        "moveTo", "click", "dragTo", "typewrite",
        "hotkey", "press",
    ):
        setattr(fake, attr, getattr(m, attr))
    fake.FAILSAFE = True  # type: ignore[attr-defined]
    fake.PAUSE = 0.05  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    # MagicMock でアクセス履歴を残すため、fake の属性を m にリダイレクトする
    return m


@pytest.fixture
def fake_pyperclip(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    m = MagicMock()
    fake = types.ModuleType("pyperclip")
    fake.copy = m.copy  # type: ignore[attr-defined]
    fake.paste = m.paste  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyperclip", fake)
    return m


@pytest.fixture
def actuator(fake_pyautogui, fake_pyperclip):
    from addons.vlm_os_agent.actuator import Actuator

    ks = KillSwitch()
    return Actuator(ks, failsafe=True, pause=0.0), ks


class TestMouse:
    def test_move_to(self, actuator, fake_pyautogui) -> None:
        a, _ = actuator
        a.move_to(100, 200)
        fake_pyautogui.moveTo.assert_called_once()
        args, kwargs = fake_pyautogui.moveTo.call_args
        assert args[0] == 100
        assert args[1] == 200

    def test_click(self, actuator, fake_pyautogui) -> None:
        a, _ = actuator
        a.click(50, 60, button="left")
        fake_pyautogui.click.assert_called_once()
        _, kwargs = fake_pyautogui.click.call_args
        assert kwargs["x"] == 50
        assert kwargs["y"] == 60
        assert kwargs["button"] == "left"
        assert kwargs["clicks"] == 1

    def test_double_click(self, actuator, fake_pyautogui) -> None:
        a, _ = actuator
        a.double_click(10, 10)
        _, kwargs = fake_pyautogui.click.call_args
        assert kwargs["clicks"] == 2

    def test_right_click(self, actuator, fake_pyautogui) -> None:
        a, _ = actuator
        a.right_click(10, 10)
        _, kwargs = fake_pyautogui.click.call_args
        assert kwargs["button"] == "right"

    def test_drag(self, actuator, fake_pyautogui) -> None:
        a, _ = actuator
        a.drag(1, 2, 3, 4)
        fake_pyautogui.moveTo.assert_called()
        fake_pyautogui.dragTo.assert_called_once()


class TestKeyboard:
    def test_type_clipboard(self, actuator, fake_pyautogui, fake_pyperclip) -> None:
        a, _ = actuator
        a.type_clipboard("こんにちは", submit=True)
        fake_pyperclip.copy.assert_called_once_with("こんにちは")
        # Ctrl+V と enter が呼ばれる
        hotkey_calls = fake_pyautogui.hotkey.call_args_list
        assert any(c.args == ("ctrl", "v") for c in hotkey_calls)
        fake_pyautogui.press.assert_called_with("enter")

    def test_type_ascii(self, actuator, fake_pyautogui) -> None:
        a, _ = actuator
        a.type_ascii("hello", submit=False)
        fake_pyautogui.typewrite.assert_called_once()

    def test_hotkey(self, actuator, fake_pyautogui) -> None:
        a, _ = actuator
        a.hotkey("ctrl", "c")
        fake_pyautogui.hotkey.assert_called_with("ctrl", "c")

    def test_press(self, actuator, fake_pyautogui) -> None:
        a, _ = actuator
        a.press("escape")
        fake_pyautogui.press.assert_called_with("escape")


class TestKillSwitchIntegration:
    def test_click_raises_when_fired(self, actuator) -> None:
        a, ks = actuator
        ks.set("test")
        with pytest.raises(AgentCancelled):
            a.click(1, 2)

    def test_type_clipboard_raises(self, actuator) -> None:
        a, ks = actuator
        ks.set("test")
        with pytest.raises(AgentCancelled):
            a.type_clipboard("hi")

    def test_move_to_raises(self, actuator) -> None:
        a, ks = actuator
        ks.set("test")
        with pytest.raises(AgentCancelled):
            a.move_to(0, 0)


class TestMissingDependencies:
    def test_no_pyautogui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from addons.vlm_os_agent.actuator import Actuator, ActuatorError

        monkeypatch.setitem(sys.modules, "pyautogui", None)
        with pytest.raises(ActuatorError, match="pyautogui"):
            Actuator(KillSwitch())

    def test_no_pyperclip(
        self, monkeypatch: pytest.MonkeyPatch, fake_pyautogui
    ) -> None:
        from addons.vlm_os_agent.actuator import Actuator, ActuatorError

        monkeypatch.setitem(sys.modules, "pyperclip", None)
        with pytest.raises(ActuatorError, match="pyperclip"):
            Actuator(KillSwitch())
