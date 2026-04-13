"""actuator.py — pyautogui + pyperclip の薄いラッパ。

すべての入力系 API の直前で KillSwitch.is_set() をチェックし、
発火済みなら `AgentCancelled` を送出する（協調停止）。

CJK（日本語）入力は `pyautogui.typewrite` が非対応のため、
クリップボード経由（`pyperclip.copy` → Ctrl+V）をデフォルトとする。
"""

from __future__ import annotations

import logging
from typing import Any

from .kill_switch import AgentCancelled, KillSwitch

logger = logging.getLogger(__name__)


class ActuatorError(RuntimeError):
    """アクチュエーター（pyautogui/pyperclip）の初期化失敗。"""


class Actuator:
    """pyautogui + pyperclip の入力操作ラッパ。"""

    def __init__(
        self,
        kill_switch: KillSwitch,
        *,
        failsafe: bool = True,
        pause: float = 0.05,
    ) -> None:
        self._ks = kill_switch
        self._pyautogui = self._import_pyautogui()
        self._pyperclip = self._import_pyperclip()
        self._pyautogui.FAILSAFE = bool(failsafe)
        self._pyautogui.PAUSE = float(pause)

    @staticmethod
    def _import_pyautogui() -> Any:
        try:
            import pyautogui  # type: ignore[import-not-found]
            return pyautogui
        except ImportError as e:
            raise ActuatorError(
                "pyautogui が未導入です。pip install -e .[vlm-agent] を実行してください。"
            ) from e

    @staticmethod
    def _import_pyperclip() -> Any:
        try:
            import pyperclip  # type: ignore[import-not-found]
            return pyperclip
        except ImportError as e:
            raise ActuatorError(
                "pyperclip が未導入です。pip install -e .[vlm-agent] を実行してください。"
            ) from e

    # ──────────────────────────────────────
    # ガード
    # ──────────────────────────────────────

    def _guard(self) -> None:
        if self._ks.is_set():
            raise AgentCancelled("actuator cancelled by kill switch")

    # ──────────────────────────────────────
    # マウス操作
    # ──────────────────────────────────────

    def move_to(self, x: int, y: int, duration: float = 0.1) -> None:
        self._guard()
        self._pyautogui.moveTo(int(x), int(y), duration=duration)

    def click(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        clicks: int = 1,
        interval: float = 0.05,
    ) -> None:
        self._guard()
        self._pyautogui.click(
            x=int(x),
            y=int(y),
            clicks=int(clicks),
            interval=float(interval),
            button=button,
        )

    def double_click(self, x: int, y: int, *, button: str = "left") -> None:
        self.click(x, y, button=button, clicks=2, interval=0.1)

    def right_click(self, x: int, y: int) -> None:
        self.click(x, y, button="right")

    def drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        duration: float = 0.3,
        button: str = "left",
    ) -> None:
        self._guard()
        self._pyautogui.moveTo(int(x1), int(y1))
        self._guard()
        self._pyautogui.dragTo(int(x2), int(y2), duration=duration, button=button)

    # ──────────────────────────────────────
    # キーボード操作
    # ──────────────────────────────────────

    def type_clipboard(self, text: str, *, submit: bool = False) -> None:
        """クリップボード経由で文字列を貼り付ける（CJK 安全経路）。"""
        self._guard()
        self._pyperclip.copy(text)
        self._guard()
        self._pyautogui.hotkey("ctrl", "v")
        if submit:
            self._guard()
            self._pyautogui.press("enter")

    def type_ascii(self, text: str, *, submit: bool = False, interval: float = 0.02) -> None:
        """ASCII 文字列をキー入力する（`pyautogui.typewrite`、CJK 非対応）。"""
        self._guard()
        self._pyautogui.typewrite(text, interval=interval)
        if submit:
            self._guard()
            self._pyautogui.press("enter")

    def hotkey(self, *keys: str) -> None:
        self._guard()
        self._pyautogui.hotkey(*keys)

    def press(self, key: str) -> None:
        self._guard()
        self._pyautogui.press(key)
