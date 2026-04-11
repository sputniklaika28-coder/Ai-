"""char_maker.py — [システム名]専用キャラクタージェネレーター テンプレート。

このファイルは addon の付属 GUI アプリです。
タクティカル祓魔師の char_maker.py を参考に、
このシステムのキャラクターステータスに合わせてカスタマイズしてください。

スタンドアロン起動:
  python addons/<your_system_id>/char_maker.py
"""

from __future__ import annotations

import json
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

_ADDON_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _ADDON_DIR.parent.parent

SAVED_PCS_DIR = _PROJECT_ROOT / "configs" / "saved_pcs"
SAVED_PCS_DIR.mkdir(parents=True, exist_ok=True)

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from core.lm_client import LMClient
except ImportError:
    print("❌ エラー: core/lm_client.py が見つかりません。")
    sys.exit(1)


class YourSystemCharMaker(tk.Tk):
    """[システム名]専用キャラクタージェネレーター。

    TODO: このシステムのキャラクターフィールドに合わせて実装してください。
    タクティカル祓魔師の char_maker.py を参考にしてください。
    """

    def __init__(self) -> None:
        super().__init__()
        self.title("[システム名] - キャラクタージェネレーター")
        self.geometry("800x600")

        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        self.lm_client = LMClient()
        self._build_ui()

    def _build_ui(self) -> None:
        label = ttk.Label(
            self,
            text="TODO: このシステムのキャラクター作成画面を実装してください",
            font=("", 12),
        )
        label.pack(expand=True)


if __name__ == "__main__":
    app = YourSystemCharMaker()
    app.mainloop()
