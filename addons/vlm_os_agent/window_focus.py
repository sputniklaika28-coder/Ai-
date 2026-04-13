"""window_focus.py — 対象ウィンドウを特定して前面化する。

優先順位:
  1. `pygetwindow`（クロスプラットフォーム、ただし Wayland は限定的）
  2. Windows のみ `pywinauto` によるフォールバック

いずれも未導入／失敗の場合は degrade（None / False を返してログ警告のみ）。
ユーザーが指定した env: `VLM_AGENT_TARGET_WINDOW` にマッチするウィンドウを
正規表現で探す。
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WindowInfo:
    """対象ウィンドウの情報。"""

    title: str
    bbox: tuple[int, int, int, int]  # (left, top, right, bottom)
    handle: Any = None               # pygetwindow の Window オブジェクト等


def _try_import_pygetwindow() -> Any | None:
    try:
        import pygetwindow  # type: ignore[import-not-found]
        return pygetwindow
    except ImportError:
        return None


def _try_import_pywinauto() -> Any | None:
    if sys.platform != "win32":
        return None
    try:
        from pywinauto import Application  # type: ignore[import-not-found]
        return Application
    except ImportError:
        return None


def list_window_titles() -> list[str]:
    """ウィンドウタイトル一覧を返す。取得失敗時は空リスト。"""
    gw = _try_import_pygetwindow()
    if gw is None:
        return []
    try:
        titles = gw.getAllTitles()
        return [t for t in titles if isinstance(t, str) and t.strip()]
    except Exception as e:
        logger.warning("ウィンドウタイトル取得失敗: %s", e)
        return []


def find_window(title_pattern: str) -> WindowInfo | None:
    """`title_pattern`（正規表現）にマッチする最初のウィンドウを返す。"""
    if not title_pattern:
        return None
    gw = _try_import_pygetwindow()
    if gw is None:
        logger.warning(
            "pygetwindow が未導入のため、ウィンドウ特定ができません "
            "（pip install -e .[vlm-agent] で有効化）。"
        )
        return None

    try:
        pattern = re.compile(title_pattern)
    except re.error:
        # 正規表現として不正なら部分一致として扱う
        pattern = re.compile(re.escape(title_pattern))

    try:
        titles = gw.getAllTitles()
    except Exception as e:
        logger.warning("ウィンドウ列挙失敗: %s", e)
        return None

    for title in titles:
        if not isinstance(title, str) or not title.strip():
            continue
        if not pattern.search(title):
            continue
        try:
            matches = gw.getWindowsWithTitle(title)
        except Exception:
            matches = []
        if not matches:
            continue
        win = matches[0]
        try:
            bbox = (
                int(win.left),
                int(win.top),
                int(win.left + win.width),
                int(win.top + win.height),
            )
        except Exception:
            continue
        return WindowInfo(title=title, bbox=bbox, handle=win)

    return None


def focus(info: WindowInfo | None) -> bool:
    """ウィンドウを前面化する。成功時 True。

    失敗した場合は Windows では pywinauto にフォールバック。
    """
    if info is None or info.handle is None:
        return False
    # pygetwindow の activate を試す
    try:
        info.handle.activate()
        return True
    except Exception as e:
        logger.debug("pygetwindow.activate 失敗: %s", e)

    # Windows 限定 pywinauto フォールバック
    Application = _try_import_pywinauto()
    if Application is None:
        logger.warning(
            "ウィンドウ前面化に失敗しました。"
            "Windows では pywinauto を導入すると改善する場合があります。"
        )
        return False

    try:
        app = Application().connect(title_re=re.escape(info.title))
        win = app.top_window()
        win.set_focus()
        return True
    except Exception as e:
        logger.warning("pywinauto によるフォーカス設定失敗: %s", e)
        return False


def get_bbox(info: WindowInfo | None) -> tuple[int, int, int, int] | None:
    """ウィンドウの現在の bbox を返す。"""
    if info is None or info.handle is None:
        return info.bbox if info else None
    try:
        w = info.handle
        return (int(w.left), int(w.top), int(w.left + w.width), int(w.top + w.height))
    except Exception:
        return info.bbox
