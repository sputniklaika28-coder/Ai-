"""kill_switch.py — グローバル ESC ホットキーによる緊急停止スイッチ。

`threading.Event` をラップし、`pynput` のキーボードリスナーを
デーモンスレッドで起動する。`Key.esc` が押下されると Event が set され、
`AgentRuntime` が各 yield ポイントで検出して `AgentCancelled` を raise する。

pynput が未インストール（= [vlm-agent] extras 未導入）の環境でも
アドオンのロード自体が落ちないよう、import は遅延化している。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


class AgentCancelled(RuntimeError):
    """キルスイッチ発火時に送出される例外。"""


class KillSwitch:
    """ESC キー押下で発火する緊急停止スイッチ。

    使い方:
        ks = KillSwitch()
        ks.start()              # ESC リスナーを起動
        ...
        if ks.is_set():
            raise AgentCancelled("esc")
        ks.stop()               # シャットダウン時に停止
        ks.reset()              # 次タスクに向けてフラグを戻す
    """

    def __init__(self, hotkey: str = "esc") -> None:
        self._event = threading.Event()
        self._listener: Any | None = None
        self._hotkey = hotkey.lower()
        self._lock = threading.Lock()

    # ──────────────────────────────────────
    # 状態クエリ
    # ──────────────────────────────────────

    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self, reason: str = "manual") -> None:
        """手動でキルスイッチを発火させる（GUI の Stop ボタン等から）。"""
        logger.info("KillSwitch 発火: %s", reason)
        self._event.set()

    def reset(self) -> None:
        """新しいタスク開始前にフラグをクリアする。"""
        self._event.clear()

    # ──────────────────────────────────────
    # ESC リスナーのライフサイクル
    # ──────────────────────────────────────

    def start(self) -> bool:
        """pynput のキーボードリスナーをデーモンスレッドで起動する。

        pynput が無い環境でも False を返して degrade（GUI の Stop ボタンのみで停止）。
        """
        with self._lock:
            if self._listener is not None:
                return True
            try:
                from pynput import keyboard  # type: ignore[import-not-found]
            except ImportError:
                logger.warning(
                    "pynput が未導入のため、ESC ホットキーは無効です "
                    "（pip install -e .[vlm-agent] で有効化）。"
                )
                return False

            def on_press(key: Any) -> None:
                try:
                    is_esc = key == keyboard.Key.esc
                except AttributeError:
                    is_esc = False
                if is_esc:
                    self.set(reason="esc")

            listener = keyboard.Listener(on_press=on_press, daemon=True)
            listener.start()
            self._listener = listener
            logger.info("KillSwitch ESC リスナーを起動しました")
            return True

    def stop(self) -> None:
        """リスナーを停止する。"""
        with self._lock:
            if self._listener is not None:
                try:
                    self._listener.stop()
                except Exception as e:
                    logger.warning("KillSwitch リスナー停止時エラー: %s", e)
                self._listener = None

    # ──────────────────────────────────────
    # 協調停止用ヘルパー
    # ──────────────────────────────────────

    def raise_if_set(self) -> None:
        """発火済みなら `AgentCancelled` を送出する。"""
        if self._event.is_set():
            raise AgentCancelled("kill_switch fired")
