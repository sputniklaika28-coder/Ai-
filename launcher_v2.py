# ================================
# ファイル: core/launcher_v2.py
# タクティカル祓魔師TRPG AIシステム — 統合ランチャー v2
# CustomTkinter ベース・サイドバーナビゲーション対応
# ================================

from __future__ import annotations

import importlib.util
import json
import os
import queue as _queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

import customtkinter as ctk
import requests

# ─────────────────────────────────────────────────────────────────
# パス設定
# ─────────────────────────────────────────────────────────────────
_THIS = Path(__file__).resolve()
if _THIS.parent.name == "core":
    BASE_DIR = _THIS.parent.parent
else:
    BASE_DIR = _THIS.parent

CONFIGS_DIR     = BASE_DIR / "configs"
CHARACTERS_JSON = CONFIGS_DIR / "characters.json"
PROMPTS_JSON    = CONFIGS_DIR / "prompts.json"
SESSION_JSON    = CONFIGS_DIR / "session_config.json"
WORLD_SETTING_JSON = CONFIGS_DIR / "world_setting.json"
SESSIONS_DIR    = BASE_DIR / "sessions"
SAVED_PCS_DIR   = CONFIGS_DIR / "saved_pcs"
CORE_DIR        = BASE_DIR / "core"
ADDON_STATE_JSON = CONFIGS_DIR / "addon_state.json"
ADDONS_DIR      = BASE_DIR / "addons"

SAVED_PCS_DIR.mkdir(parents=True, exist_ok=True)
PYTHON = sys.executable

sys.path.insert(0, str(CORE_DIR))

# ─────────────────────────────────────────────────────────────────
# ユーティリティ
# ─────────────────────────────────────────────────────────────────

def compress_tokens_safe(text: str) -> str:
    compressed = re.sub(r"\n+", "\n", text)
    compressed = re.sub(r"[ \t　]+", " ", compressed)
    return compressed


def parse_llm_json_robust(text: str) -> dict:
    clean_text = re.sub(r"```json\n?|```\n?", "", text).strip()
    start = clean_text.find("{")
    end   = clean_text.rfind("}")
    parsed_data: dict = {}
    if start != -1 and end != -1:
        try:
            parsed_data = json.loads(clean_text[start : end + 1])
            return parsed_data
        except json.JSONDecodeError:
            pass
    pattern_str = r'"([^"]+)"\s*:\s*(?:"([^"]*)"|(\d+))'
    matches = re.findall(pattern_str, text)
    for key, val_str, val_num in matches:
        if val_str:
            parsed_data[key] = val_str.replace("\\n", "\n")
        elif val_num:
            parsed_data[key] = int(val_num)
    for list_key in ["skills", "inventory", "accessories"]:
        if list_key not in parsed_data:
            parsed_data[list_key] = []
    return parsed_data


def load_json(path: Path) -> dict:
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read().strip()
                return json.loads(content) if content else {}
        except json.JSONDecodeError:
            return {}
    return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_template_ids() -> list[str]:
    return list(load_json(PROMPTS_JSON).get("templates", {}).keys())


def get_session_folders() -> list[Path]:
    if not SESSIONS_DIR.exists():
        return []
    return sorted([d for d in SESSIONS_DIR.iterdir() if d.is_dir()], reverse=True)


def check_lm_studio() -> bool:
    try:
        r = requests.get("http://localhost:1234/v1/models", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────
# デザインシステム
# ─────────────────────────────────────────────────────────────────

class AppTheme:
    """カラースキームとフォント定数"""
    # 背景系
    BG        = "#1E1E1E"   # メイン背景
    SURFACE   = "#252526"   # パネル・カード背景
    SIDEBAR   = "#181818"   # サイドバー背景
    SIDEBAR_HOVER = "#2A2D2E"

    # アクセント
    ACCENT    = "#007ACC"   # アクティブ・ボタン強調
    LAUNCH    = "#4CAF50"   # 起動ボタン（緑）
    LAUNCH_HV = "#3d9140"   # 起動ボタン ホバー

    # テキスト
    TEXT      = "#D4D4D4"   # メインテキスト
    TEXT_DIM  = "#808080"   # 薄いテキスト
    TEXT_HEAD = "#FFFFFF"   # 見出し

    # ステータス
    OK        = "#4CAF50"   # 接続OK（緑）
    ERROR     = "#F44747"   # エラー（赤）
    WARN      = "#CCA700"   # 警告（黄）
    INFO      = "#9CDCFE"   # 情報（水色）

    # ログタグ（tk.Text 用）
    LOG_OK    = "#4ec9b0"
    LOG_ERR   = "#f44747"
    LOG_WARN  = "#dcdcaa"
    LOG_INFO  = "#9cdcfe"
    LOG_PLAIN = "#d4d4d4"

    # フォント
    FONT_NORMAL  = ("Yu Gothic UI", 11)
    FONT_SMALL   = ("Yu Gothic UI", 9)
    FONT_BOLD    = ("Yu Gothic UI", 11, "bold")
    FONT_HEAD    = ("Yu Gothic UI", 13, "bold")
    FONT_MONO    = ("Courier New", 9)


def _setup_ctk_appearance() -> None:
    """CustomTkinter のグローバル外観設定"""
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")


# ─────────────────────────────────────────────────────────────────
# スレッドセーフ UI スケジューラー
# Python 3.14 以降、バックグラウンドスレッドから widget.after() を
# 直接呼ぶと RuntimeError になるため queue.Queue 経由で安全にポスト。
# TacticalAILauncherV2 が 40ms ごとにキューをドレインする。
# ─────────────────────────────────────────────────────────────────

_UI_QUEUE: _queue.SimpleQueue = _queue.SimpleQueue()


def _post_to_main(fn) -> None:
    """バックグラウンドスレッドから UI コールバックをメインスレッドへ安全に転送する"""
    _UI_QUEUE.put(fn)


# ─────────────────────────────────────────────────────────────────
# サイドバー
# ─────────────────────────────────────────────────────────────────

_NAV_ITEMS: list[tuple[str, str]] = [
    ("home",   "🚀  ホーム"),
    ("actors", "👥  アクター"),
    ("world",  "🌍  世界観"),
    ("ai",     "📝  AI設定"),
    ("system", "⚙️   システム"),
]


class Sidebar(ctk.CTkFrame):
    """左サイドバー — ナビゲーションボタン群"""

    def __init__(self, parent: ctk.CTk, on_nav_click):
        super().__init__(
            parent,
            width=190,
            corner_radius=0,
            fg_color=AppTheme.SIDEBAR,
        )
        self.pack_propagate(False)
        self._on_nav_click = on_nav_click
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._build()

    def _build(self) -> None:
        # アプリタイトル
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.pack(fill="x", padx=12, pady=(16, 4))
        ctk.CTkLabel(
            title_frame,
            text="祓魔師AI",
            font=ctk.CTkFont(family="Yu Gothic UI", size=15, weight="bold"),
            text_color=AppTheme.TEXT_HEAD,
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_frame,
            text="TRPG システム",
            font=ctk.CTkFont(family="Yu Gothic UI", size=9),
            text_color=AppTheme.TEXT_DIM,
        ).pack(anchor="w")

        # 区切り線
        ctk.CTkFrame(self, height=1, fg_color="#333333").pack(fill="x", padx=8, pady=(8, 12))

        # ナビゲーションボタン
        for key, label in _NAV_ITEMS:
            btn = ctk.CTkButton(
                self,
                text=label,
                anchor="w",
                fg_color="transparent",
                hover_color=AppTheme.SIDEBAR_HOVER,
                text_color=AppTheme.TEXT,
                font=ctk.CTkFont(family="Yu Gothic UI", size=12),
                height=40,
                corner_radius=6,
                command=lambda k=key: self._on_nav_click(k),
            )
            btn.pack(fill="x", padx=8, pady=2)
            self._buttons[key] = btn

        # アドオン用スペーサー + 区切り
        self._addon_sep = ctk.CTkFrame(self, height=1, fg_color="#333333")
        self._addon_sep.pack(fill="x", padx=8, pady=(12, 8))
        self._addon_sep.pack_forget()  # アドオンがあれば表示する

        self._addon_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._addon_frame.pack(fill="x")

    def set_active(self, view_key: str) -> None:
        """アクティブなナビゲーションボタンをハイライト"""
        for key, btn in self._buttons.items():
            if key == view_key:
                btn.configure(fg_color=AppTheme.ACCENT, text_color=AppTheme.TEXT_HEAD)
            else:
                btn.configure(fg_color="transparent", text_color=AppTheme.TEXT)

    def add_addon_button(self, key: str, label: str, on_click) -> None:
        """アドオン用のナビゲーションボタンを追加"""
        self._addon_sep.pack(fill="x", padx=8, pady=(12, 8))
        btn = ctk.CTkButton(
            self._addon_frame,
            text=f"🧩  {label}",
            anchor="w",
            fg_color="transparent",
            hover_color=AppTheme.SIDEBAR_HOVER,
            text_color=AppTheme.TEXT,
            font=ctk.CTkFont(family="Yu Gothic UI", size=11),
            height=36,
            corner_radius=6,
            command=on_click,
        )
        btn.pack(fill="x", padx=8, pady=2)
        self._buttons[key] = btn


# ─────────────────────────────────────────────────────────────────
# ボトムステータスバー
# ─────────────────────────────────────────────────────────────────

class StatusBar(ctk.CTkFrame):
    """ウィンドウ下部の常時表示ステータスバー"""

    def __init__(self, parent: ctk.CTk):
        super().__init__(
            parent,
            height=30,
            corner_radius=0,
            fg_color="#111111",
        )
        self.pack_propagate(False)
        self._lm_ok: bool | None = None
        self._build()
        self.start_polling()

    def _build(self) -> None:
        # LM-Studio ステータス
        lm_frame = ctk.CTkFrame(self, fg_color="transparent")
        lm_frame.pack(side="left", padx=(12, 0))

        self._lm_dot = ctk.CTkLabel(
            lm_frame,
            text="●",
            font=ctk.CTkFont(size=10),
            text_color=AppTheme.TEXT_DIM,
            width=14,
        )
        self._lm_dot.pack(side="left")
        self._lm_label = ctk.CTkLabel(
            lm_frame,
            text=" LM-Studio: 確認中...",
            font=ctk.CTkFont(family="Yu Gothic UI", size=9),
            text_color=AppTheme.TEXT_DIM,
        )
        self._lm_label.pack(side="left")

        # 区切り
        ctk.CTkLabel(self, text="|", text_color="#444444",
                     font=ctk.CTkFont(size=9)).pack(side="left", padx=8)

        # 設定フォルダパス
        ctk.CTkLabel(
            self,
            text=str(CONFIGS_DIR),
            font=ctk.CTkFont(family="Yu Gothic UI", size=9),
            text_color=AppTheme.TEXT_DIM,
        ).pack(side="left")

        # 右側
        ctk.CTkLabel(
            self,
            text="v2.0.0",
            font=ctk.CTkFont(family="Yu Gothic UI", size=9),
            text_color=AppTheme.TEXT_DIM,
        ).pack(side="right", padx=(0, 12))

        ctk.CTkLabel(self, text="|", text_color="#444444",
                     font=ctk.CTkFont(size=9)).pack(side="right", padx=4)

        self._mem_label = ctk.CTkLabel(
            self,
            text="RAM: --",
            font=ctk.CTkFont(family="Yu Gothic UI", size=9),
            text_color=AppTheme.TEXT_DIM,
        )
        self._mem_label.pack(side="right", padx=(0, 4))

    def set_lm_status(self, ok: bool) -> None:
        if ok == self._lm_ok:
            return
        self._lm_ok = ok
        if ok:
            self._lm_dot.configure(text_color=AppTheme.OK)
            self._lm_label.configure(
                text=" LM-Studio: 接続中", text_color=AppTheme.OK
            )
        else:
            self._lm_dot.configure(text_color=AppTheme.ERROR)
            self._lm_label.configure(
                text=" LM-Studio: 未接続", text_color=AppTheme.ERROR
            )

    def set_memory(self, mb: float) -> None:
        self._mem_label.configure(text=f"RAM: {mb:.0f} MB")

    def _poll_lm(self) -> None:
        def check():
            ok = check_lm_studio()
            _post_to_main(lambda: self.set_lm_status(ok))
            _post_to_main(lambda: self.after(10_000, self._poll_lm))
        threading.Thread(target=check, daemon=True).start()

    def _poll_memory(self) -> None:
        try:
            import psutil
            mb = psutil.Process().memory_info().rss / 1024 / 1024
            self.set_memory(mb)
        except Exception:
            pass
        self.after(5_000, self._poll_memory)

    def start_polling(self) -> None:
        self.after(500, self._poll_lm)
        self.after(1_000, self._poll_memory)


# ─────────────────────────────────────────────────────────────────
# ホームビュー（LauncherTab + HistoryTab 統合）
# ─────────────────────────────────────────────────────────────────

class HomeView(ctk.CTkFrame):
    """ホーム画面 — セッション選択・CCFolia起動・ログ表示"""

    def __init__(self, parent):
        super().__init__(parent, fg_color=AppTheme.BG, corner_radius=0)
        self._proc: subprocess.Popen | None = None
        self._log_thread: threading.Thread | None = None
        self._session_folders: list[Path] = []
        self._build_ui()

    def _build_ui(self) -> None:
        # ── ヘッダー ────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color=AppTheme.SURFACE, corner_radius=0, height=48)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(
            header,
            text="🚀  ホーム — セッション管理・CCFolia起動",
            font=ctk.CTkFont(family="Yu Gothic UI", size=14, weight="bold"),
            text_color=AppTheme.TEXT_HEAD,
        ).pack(side="left", padx=16, pady=10)

        # ── ボディ（左右分割）─────────────────────────────────
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=8)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=3)
        body.rowconfigure(0, weight=1)

        # ─── 左ペイン: セッション履歴 ─────────────────────────
        left = ctk.CTkFrame(body, fg_color=AppTheme.SURFACE, corner_radius=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        ctk.CTkLabel(
            left,
            text="セッション履歴",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=12, pady=(10, 4))

        self._session_scroll = ctk.CTkScrollableFrame(
            left, fg_color="transparent", label_text=""
        )
        self._session_scroll.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        ctk.CTkButton(
            left,
            text="一覧を更新",
            height=28,
            fg_color="#333333",
            hover_color="#444444",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            command=self._refresh_sessions,
        ).pack(fill="x", padx=8, pady=(0, 8))

        # ─── 右ペイン ─────────────────────────────────────────
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        # 起動設定パネル
        cfg_panel = ctk.CTkFrame(right, fg_color=AppTheme.SURFACE, corner_radius=8)
        cfg_panel.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        cfg_panel.columnconfigure(1, weight=1)

        _lbl = lambda text, r: ctk.CTkLabel(
            cfg_panel,
            text=text,
            font=ctk.CTkFont(family="Yu Gothic UI", size=11),
            text_color=AppTheme.TEXT,
            anchor="e",
        ).grid(row=r, column=0, sticky="e", padx=(12, 6), pady=5)

        # LM-Studio ステータス行
        _lbl("LM-Studio", 0)
        lm_row = ctk.CTkFrame(cfg_panel, fg_color="transparent")
        lm_row.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=5)
        self._lm_dot = ctk.CTkLabel(
            lm_row, text="●", text_color=AppTheme.TEXT_DIM,
            font=ctk.CTkFont(size=11)
        )
        self._lm_dot.pack(side="left")
        self._lm_status_var = tk.StringVar(value=" 確認中...")
        self._lm_status_lbl = ctk.CTkLabel(
            lm_row,
            textvariable=self._lm_status_var,
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            text_color=AppTheme.TEXT_DIM,
        )
        self._lm_status_lbl.pack(side="left")
        ctk.CTkButton(
            lm_row,
            text="再確認",
            width=60,
            height=24,
            fg_color="#333333",
            hover_color="#444444",
            font=ctk.CTkFont(family="Yu Gothic UI", size=9),
            command=self._update_lm_status,
        ).pack(side="left", padx=(8, 0))

        # Room URL
        _lbl("Room URL", 1)
        self._var_url = tk.StringVar()
        ctk.CTkEntry(
            cfg_panel,
            textvariable=self._var_url,
            placeholder_text="https://ccfolia.com/rooms/...",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11),
            fg_color="#1a1a1a",
            border_color="#444444",
        ).grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=5)

        # セッション選択
        _lbl("セッション", 2)
        sess_row = ctk.CTkFrame(cfg_panel, fg_color="transparent")
        sess_row.grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=5)
        sess_row.columnconfigure(0, weight=1)
        self._var_session = tk.StringVar(value="新規セッション")
        self._cb_session = ctk.CTkComboBox(
            sess_row,
            variable=self._var_session,
            values=["新規セッション"],
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            fg_color="#1a1a1a",
            border_color="#444444",
            button_color="#333333",
            state="readonly",
        )
        self._cb_session.grid(row=0, column=0, sticky="ew")

        # デフォルトキャラ
        _lbl("デフォルトキャラ", 3)
        self._var_default_char = tk.StringVar(value="meta_gm")
        ctk.CTkEntry(
            cfg_panel,
            textvariable=self._var_default_char,
            font=ctk.CTkFont(family="Yu Gothic UI", size=11),
            fg_color="#1a1a1a",
            border_color="#444444",
            width=160,
        ).grid(row=3, column=1, sticky="w", padx=(0, 12), pady=5)

        # CDP オプション
        cdp_row = ctk.CTkFrame(cfg_panel, fg_color="transparent")
        cdp_row.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=(2, 8))
        self._var_use_cdp = tk.BooleanVar(value=False)
        self._cdp_check = ctk.CTkCheckBox(
            cdp_row,
            text="既存ブラウザに接続 (CDP)",
            variable=self._var_use_cdp,
            onvalue=True, offvalue=False,
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            text_color=AppTheme.TEXT,
            fg_color=AppTheme.ACCENT,
            command=self._toggle_cdp,
        )
        self._cdp_check.pack(side="left")
        self._var_cdp_url = tk.StringVar(value="http://localhost:9222")
        self._entry_cdp = ctk.CTkEntry(
            cdp_row,
            textvariable=self._var_cdp_url,
            width=220,
            state="disabled",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            fg_color="#1a1a1a",
            border_color="#444444",
        )
        self._entry_cdp.pack(side="left", padx=(10, 0))
        ctk.CTkLabel(
            cdp_row,
            text="  Chrome を --remote-debugging-port=9222 で起動",
            font=ctk.CTkFont(family="Yu Gothic UI", size=9),
            text_color=AppTheme.TEXT_DIM,
        ).pack(side="left")

        # 起動ボタン行
        btn_row = ctk.CTkFrame(right, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        self._btn_start = ctk.CTkButton(
            btn_row,
            text="▶  CCFolia 起動",
            height=52,
            fg_color=AppTheme.LAUNCH,
            hover_color=AppTheme.LAUNCH_HV,
            font=ctk.CTkFont(family="Yu Gothic UI", size=14, weight="bold"),
            text_color="#FFFFFF",
            corner_radius=8,
            command=self._on_start,
        )
        self._btn_start.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self._btn_stop = ctk.CTkButton(
            btn_row,
            text="■  停止",
            height=52,
            width=100,
            fg_color="#3a3a3a",
            hover_color="#555555",
            font=ctk.CTkFont(family="Yu Gothic UI", size=12),
            state="disabled",
            corner_radius=8,
            command=self._on_stop,
        )
        self._btn_stop.pack(side="left")

        self._status_var = tk.StringVar(value="待機中")
        ctk.CTkLabel(
            btn_row,
            textvariable=self._status_var,
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            text_color=AppTheme.TEXT_DIM,
        ).pack(side="left", padx=12)

        ctk.CTkButton(
            btn_row,
            text="ログをクリア",
            width=90,
            height=28,
            fg_color="#333333",
            hover_color="#444444",
            font=ctk.CTkFont(family="Yu Gothic UI", size=9),
            command=self._clear_log,
        ).pack(side="right")

        # コンソールログ（折りたたみ可能）
        log_toggle_row = ctk.CTkFrame(right, fg_color="transparent")
        log_toggle_row.grid(row=2, column=0, sticky="nsew")
        right.rowconfigure(2, weight=1)

        log_header = ctk.CTkFrame(log_toggle_row, fg_color=AppTheme.SURFACE, corner_radius=6, height=30)
        log_header.pack(fill="x")
        log_header.pack_propagate(False)
        self._log_open = tk.BooleanVar(value=True)

        ctk.CTkLabel(
            log_header,
            text="ログ出力",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            log_header,
            text="▼",
            width=30, height=24,
            fg_color="transparent",
            hover_color=AppTheme.SIDEBAR_HOVER,
            text_color=AppTheme.TEXT_DIM,
            font=ctk.CTkFont(size=9),
            command=self._toggle_log,
        ).pack(side="right", padx=4)

        self._log_container = ctk.CTkFrame(
            log_toggle_row, fg_color=AppTheme.SURFACE, corner_radius=6
        )
        self._log_container.pack(fill="both", expand=True, pady=(2, 0))

        self._log_box = ctk.CTkTextbox(
            self._log_container,
            state="disabled",
            font=ctk.CTkFont(family="Courier New", size=9),
            text_color=AppTheme.LOG_PLAIN,
            fg_color="#1a1a1a",
            wrap="word",
        )
        self._log_box.pack(fill="both", expand=True, padx=4, pady=4)

        # ログカラータグ（内部 tk.Text にアクセス）
        for tag, color in [
            ("ok",   AppTheme.LOG_OK),
            ("err",  AppTheme.LOG_ERR),
            ("warn", AppTheme.LOG_WARN),
            ("info", AppTheme.LOG_INFO),
            ("plain", AppTheme.LOG_PLAIN),
        ]:
            self._log_box._textbox.tag_config(tag, foreground=color)

        # セッション詳細パネル（左ペインの選択に連動）
        self._detail_panel = ctk.CTkFrame(left, fg_color="#1a1a1a", corner_radius=6)
        self._detail_panel.pack(fill="x", padx=6, pady=(0, 4))
        self._detail_var = tk.StringVar(value="セッションを選択してください")
        ctk.CTkLabel(
            self._detail_panel,
            textvariable=self._detail_var,
            font=ctk.CTkFont(family="Yu Gothic UI", size=9),
            text_color=AppTheme.TEXT_DIM,
            wraplength=140,
            justify="left",
        ).pack(padx=8, pady=6)

        self._btn_resume = ctk.CTkButton(
            left,
            text="🔄 この状態から再開",
            height=28,
            fg_color="#333333",
            hover_color="#444444",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            state="disabled",
            command=self._on_resume,
        )
        self._btn_resume.pack(fill="x", padx=8, pady=(0, 8))

    # ── セッション履歴 ────────────────────────────────────────────

    def _refresh_sessions(self) -> None:
        for w in self._session_scroll.winfo_children():
            w.destroy()
        self._session_folders = get_session_folders()
        options = ["新規セッション"] + [f.name for f in self._session_folders]
        self._cb_session.configure(values=options)
        if self._var_session.get() not in options:
            self._var_session.set("新規セッション")

        for folder in self._session_folders:
            card = ctk.CTkButton(
                self._session_scroll,
                text=folder.name,
                anchor="w",
                fg_color="#2a2a2a",
                hover_color="#3a3a3a",
                text_color=AppTheme.TEXT,
                font=ctk.CTkFont(family="Yu Gothic UI", size=9),
                height=32,
                corner_radius=4,
                command=lambda f=folder: self._on_session_select(f),
            )
            card.pack(fill="x", pady=1)

    def _on_session_select(self, folder: Path) -> None:
        self._selected_folder = folder
        self._var_session.set(folder.name)

        info_lines = [f"📁 {folder.name}"]
        log_file = folder / "chat_log.jsonl"
        summary_file = folder / "summary.txt"
        if summary_file.exists():
            with open(summary_file, encoding="utf-8") as f:
                info_lines.append(f.read()[:120])
        if log_file.exists():
            try:
                count = sum(1 for l in open(log_file, encoding="utf-8") if l.strip())
                info_lines.append(f"ログ: {count} 件")
            except Exception:
                pass
        self._detail_var.set("\n".join(info_lines))
        self._btn_resume.configure(state="normal")

    def _on_resume(self) -> None:
        folder = getattr(self, "_selected_folder", None)
        if not folder:
            return
        backup_dir = folder / "configs_backup"
        if not backup_dir.exists():
            messagebox.showerror("エラー", "バックアップが見つかりません。",
                                 parent=self.winfo_toplevel())
            return
        msg = f"'{folder.name}' の状態に復元しますか？\n※現在の設定は上書きされます。"
        if messagebox.askyesno("復元と再開", msg, parent=self.winfo_toplevel()):
            try:
                shutil.copytree(backup_dir, CONFIGS_DIR, dirs_exist_ok=True)
                messagebox.showinfo("復元完了", "設定データを復元しました。",
                                    parent=self.winfo_toplevel())
            except Exception as e:
                messagebox.showerror("エラー", f"復元エラー:\n{e}",
                                     parent=self.winfo_toplevel())

    # ── LM-Studio ────────────────────────────────────────────────

    def _update_lm_status(self) -> None:
        def check():
            ok = check_lm_studio()
            _post_to_main(lambda: self._set_lm_status(ok))
        threading.Thread(target=check, daemon=True).start()

    def _set_lm_status(self, ok: bool) -> None:
        if ok:
            self._lm_dot.configure(text_color=AppTheme.OK)
            self._lm_status_var.set(" ✓ 接続中 (localhost:1234)")
            self._lm_status_lbl.configure(text_color=AppTheme.OK)
        else:
            self._lm_dot.configure(text_color=AppTheme.ERROR)
            self._lm_status_var.set(" ✗ 未接続 — LM-Studio を起動してください")
            self._lm_status_lbl.configure(text_color=AppTheme.ERROR)

    # ── CDP トグル ────────────────────────────────────────────────

    def _toggle_cdp(self) -> None:
        if self._var_use_cdp.get():
            self._entry_cdp.configure(state="normal")
        else:
            self._entry_cdp.configure(state="disabled")

    # ── プロセス管理 ─────────────────────────────────────────────

    def _on_start(self) -> None:
        url = self._var_url.get().strip()
        use_cdp = self._var_use_cdp.get()

        if not url and not use_cdp:
            messagebox.showwarning("入力エラー", "Room URL を入力してください",
                                   parent=self.winfo_toplevel())
            return
        if url and not url.startswith("http"):
            messagebox.showwarning(
                "入力エラー",
                "URL は http:// または https:// で始める必要があります",
                parent=self.winfo_toplevel(),
            )
            return

        if use_cdp and not url:
            url = "https://ccfolia.com"

        default_char = self._var_default_char.get().strip() or "meta_gm"
        connector_path = CORE_DIR / "ccfolia_connector.py"

        cmd = [PYTHON, str(connector_path), "--room", url, "--default", default_char]
        if use_cdp:
            cdp_url = self._var_cdp_url.get().strip()
            if cdp_url:
                cmd += ["--cdp", cdp_url]

        self._log(f"起動コマンド: {' '.join(cmd)}\n", "info")

        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stdin=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=str(BASE_DIR),
                env=env,
            )
        except Exception as e:
            messagebox.showerror("起動エラー", str(e), parent=self.winfo_toplevel())
            return

        self._btn_start.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._status_var.set("監視中...")

        self._log_thread = threading.Thread(target=self._read_proc_output, daemon=True)
        self._log_thread.start()

    def _read_proc_output(self) -> None:
        if not self._proc:
            return
        for line in self._proc.stdout:
            _post_to_main(lambda l=line: self._log(l))
        ret = self._proc.wait()
        _post_to_main(lambda: self._on_proc_finished(ret))

    def _on_proc_finished(self, returncode: int) -> None:
        self._proc = None
        self._btn_start.configure(state="normal")
        self._btn_stop.configure(state="disabled")
        self._status_var.set(f"停止済 (終了コード: {returncode})")
        self._log(f"\n--- プロセス終了 (code={returncode}) ---\n", "warn")
        self._refresh_sessions()

    def _on_stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._log("\n停止リクエストを送信しています...\n", "warn")
            try:
                self._proc.stdin.write(json.dumps({"type": "quit"}) + "\n")
                self._proc.stdin.flush()
            except Exception:
                pass
            self._proc.terminate()
        self._btn_stop.configure(state="disabled")
        self._status_var.set("停止中...")

    def send_to_ccfolia(self, character_name: str, text: str) -> None:
        if self._proc and self._proc.poll() is None:
            payload = (
                json.dumps(
                    {"type": "chat", "character": character_name, "text": text},
                    ensure_ascii=False,
                )
                + "\n"
            )
            try:
                if self._proc.stdin:
                    self._proc.stdin.write(payload)
                    self._proc.stdin.flush()
                self._log(f"[システム] CCFoliaへ送信命令を出しました。({character_name})\n", "ok")
            except Exception as e:
                self._log(f"[システムエラー] 送信失敗: {e}\n", "err")
        else:
            self._log("[システム警告] CCFoliaコネクターが起動していないため送信できません。\n", "warn")

    # ── ログ ────────────────────────────────────────────────────

    def _log(self, text: str, tag: str = "plain") -> None:
        self._log_box.configure(state="normal")
        self._log_box._textbox.insert("end", text, tag)
        self._log_box._textbox.see("end")
        self._log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        self._log_box.configure(state="normal")
        self._log_box.delete("0.0", "end")
        self._log_box.configure(state="disabled")

    def _toggle_log(self) -> None:
        if self._log_open.get():
            self._log_container.pack_forget()
            self._log_open.set(False)
        else:
            self._log_container.pack(fill="both", expand=True, pady=(2, 0))
            self._log_open.set(True)

    # ── on_show フック ───────────────────────────────────────────

    def on_show(self) -> None:
        self._refresh_sessions()
        self._update_lm_status()


# ─────────────────────────────────────────────────────────────────
# キャラクター編集ダイアログ（tk.Toplevel 流用）
# ─────────────────────────────────────────────────────────────────

class CharacterDialog(tk.Toplevel):
    LAYERS = ["meta", "setting", "player"]
    ROLES  = ["game_master", "npc_manager", "enemy", "player"]

    def __init__(self, parent, char_data: dict | None = None, existing_ids: list | None = None):
        super().__init__(parent)
        self.result = None
        self.is_edit = char_data is not None
        self.existing_ids = existing_ids or []
        self.char_data = char_data or {}
        self.title("キャラクター編集" if self.is_edit else "キャラクター追加")
        self.geometry("500x560")
        self.resizable(False, False)
        self.grab_set()
        self._build_ui()
        self._load_data()
        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{px}+{py}")

    def _build_ui(self):
        pad = {"padx": 12, "pady": 5}
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="キャラクターID（英数字・_のみ）").grid(row=0, column=0, sticky="w", **pad)
        self.var_id = tk.StringVar()
        self.entry_id = ttk.Entry(frame, textvariable=self.var_id, width=35)
        self.entry_id.grid(row=0, column=1, sticky="w", **pad)
        if self.is_edit:
            self.entry_id.config(state="disabled")
        ttk.Label(frame, text="名前（表示用）").grid(row=1, column=0, sticky="w", **pad)
        self.var_name = tk.StringVar()
        ttk.Entry(frame, textvariable=self.var_name, width=35).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(frame, text="レイヤー").grid(row=2, column=0, sticky="w", **pad)
        self.var_layer = tk.StringVar()
        ttk.Combobox(frame, textvariable=self.var_layer, values=self.LAYERS,
                     state="readonly", width=20).grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(frame, text="役割").grid(row=3, column=0, sticky="w", **pad)
        self.var_role = tk.StringVar()
        ttk.Combobox(frame, textvariable=self.var_role, values=self.ROLES,
                     state="readonly", width=20).grid(row=3, column=1, sticky="w", **pad)
        ttk.Label(frame, text="プロンプトテンプレート").grid(row=4, column=0, sticky="w", **pad)
        self.var_prompt = tk.StringVar()
        self.cb_prompt = ttk.Combobox(frame, textvariable=self.var_prompt,
                                       values=get_template_ids(), state="readonly", width=30)
        self.cb_prompt.grid(row=4, column=1, sticky="w", **pad)
        ttk.Label(frame, text="反応キーワード（カンマ区切り）").grid(row=5, column=0, sticky="w", **pad)
        self.var_keywords = tk.StringVar()
        ttk.Entry(frame, textvariable=self.var_keywords, width=35).grid(row=5, column=1, sticky="w", **pad)
        ttk.Label(frame, text="説明").grid(row=6, column=0, sticky="nw", **pad)
        self.text_desc = tk.Text(frame, width=35, height=3, font=("", 10))
        self.text_desc.grid(row=6, column=1, sticky="w", **pad)
        self.var_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="有効", variable=self.var_enabled).grid(row=7, column=0, sticky="w", **pad)
        self.var_is_ai = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="AI制御", variable=self.var_is_ai).grid(row=7, column=1, sticky="w", **pad)
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=8, column=0, columnspan=2, pady=14)
        ttk.Button(btn_frame, text="保存", command=self._on_save, width=12).pack(side=tk.LEFT, padx=8)
        ttk.Button(btn_frame, text="キャンセル", command=self.destroy, width=12).pack(side=tk.LEFT, padx=8)

    def _load_data(self):
        if not self.char_data:
            self.var_layer.set("setting")
            self.var_role.set("npc_manager")
            return
        self.var_id.set(self.char_data.get("id", ""))
        self.var_name.set(self.char_data.get("name", ""))
        self.var_layer.set(self.char_data.get("layer", "setting"))
        self.var_role.set(self.char_data.get("role", "npc_manager"))
        self.var_prompt.set(self.char_data.get("prompt_id", ""))
        self.var_enabled.set(self.char_data.get("enabled", True))
        self.var_is_ai.set(self.char_data.get("is_ai", True))
        self.text_desc.insert("1.0", self.char_data.get("description", ""))
        self.var_keywords.set(", ".join(self.char_data.get("keywords", [])))

    def _on_save(self):
        char_id = self.var_id.get().strip()
        name = self.var_name.get().strip()
        if not re.match(r"^[a-zA-Z0-9_]+$", char_id):
            return messagebox.showerror("エラー", "IDは英数字と_のみ可能", parent=self)
        if not self.is_edit and char_id in self.existing_ids:
            return messagebox.showerror("エラー", "IDが重複しています", parent=self)
        if not name:
            return messagebox.showerror("エラー", "名前を入力してください", parent=self)
        kw_str = self.var_keywords.get().strip()
        self.result = {
            "id": char_id, "name": name,
            "layer": self.var_layer.get(), "role": self.var_role.get(),
            "keywords": [k.strip() for k in kw_str.split(",")] if kw_str else [],
            "description": self.text_desc.get("1.0", tk.END).strip(),
            "enabled": self.var_enabled.get(), "is_ai": self.var_is_ai.get(),
            "prompt_id": self.var_prompt.get(),
        }
        self.destroy()


# ─────────────────────────────────────────────────────────────────
# アクタービュー（CharacterTab + VTTCharMakerTab 統合）
# ─────────────────────────────────────────────────────────────────

_ROLE_COLORS = {
    "game_master": "#4ec9b0",
    "npc_manager": "#3a86ff",
    "enemy":       "#f44747",
    "player":      "#dcdcaa",
}
_LAYER_LABELS = {"meta": "メタ", "setting": "設定", "player": "プレイヤー"}


class ActorsView(ctk.CTkFrame):
    """アクター管理 — キャラ一覧カード + キャラクターメーカー"""

    def __init__(self, parent):
        super().__init__(parent, fg_color=AppTheme.BG, corner_radius=0)
        sys.path.insert(0, str(CORE_DIR))
        from lm_client import LMClient
        self._lm_client = LMClient()
        self._characters: dict = {}
        self._saved_files: list[Path] = []
        self._last_json_raw: dict = {}
        self._init_vars()
        self._build_ui()

    def _init_vars(self):
        self.var_name     = tk.StringVar(value="名無し")
        self.var_alias    = tk.StringVar(value="")
        self.var_body     = tk.IntVar(value=3)
        self.var_soul     = tk.IntVar(value=3)
        self.var_skill    = tk.IntVar(value=3)
        self.var_magic    = tk.IntVar(value=3)
        self.var_hp       = tk.IntVar(value=10)
        self.var_sp       = tk.IntVar(value=10)
        self.var_evasion  = tk.IntVar(value=2)
        self.var_mobility = tk.IntVar(value=2)
        self.var_armor    = tk.IntVar(value=0)
        self.var_katashiro  = tk.IntVar(value=1)
        self.var_haraegushi = tk.IntVar(value=0)
        self.var_shimenawa  = tk.IntVar(value=0)
        self.var_juryudan   = tk.IntVar(value=0)
        self.var_ireikigu   = tk.IntVar(value=0)
        self.var_meifuku    = tk.IntVar(value=0)
        self.var_jutsuyen   = tk.IntVar(value=0)

    def _build_ui(self) -> None:
        # ── ヘッダー ──
        header = ctk.CTkFrame(self, fg_color=AppTheme.SURFACE, corner_radius=0, height=48)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(
            header,
            text="👥  アクター — キャラクター管理・メーカー",
            font=ctk.CTkFont(family="Yu Gothic UI", size=14, weight="bold"),
            text_color=AppTheme.TEXT_HEAD,
        ).pack(side="left", padx=16, pady=10)

        # ── ボディ（左右分割）──
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=8)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=3)
        body.rowconfigure(0, weight=1)

        # ─── 左ペイン: キャラカードリスト ────────────────────────
        left = ctk.CTkFrame(body, fg_color=AppTheme.SURFACE, corner_radius=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        ctk.CTkLabel(
            left, text="キャラクター一覧",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=12, pady=(10, 4))

        self._char_scroll = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self._char_scroll.pack(fill="both", expand=True, padx=6, pady=(0, 4))

        btn_row = ctk.CTkFrame(left, fg_color="transparent")
        btn_row.pack(fill="x", padx=8, pady=(0, 8))
        for text, cmd in [
            ("追加", self._on_add),
            ("更新", self.refresh_chars),
        ]:
            ctk.CTkButton(
                btn_row, text=text, height=28,
                fg_color="#333333", hover_color="#444444",
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                command=cmd,
            ).pack(side="left", expand=True, fill="x", padx=2)

        # ─── 右ペイン: CTkTabview（管理 / メーカー）─────────────
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._tabs = ctk.CTkTabview(
            right, fg_color=AppTheme.SURFACE, corner_radius=8,
            segmented_button_fg_color=AppTheme.BG,
            segmented_button_selected_color=AppTheme.ACCENT,
            segmented_button_unselected_color="#333333",
            text_color=AppTheme.TEXT,
        )
        self._tabs.pack(fill="both", expand=True)
        self._tabs.add("管理")
        self._tabs.add("メーカー")

        self._build_manage_tab(self._tabs.tab("管理"))
        self._build_maker_tab(self._tabs.tab("メーカー"))

    # ── 管理タブ ─────────────────────────────────────────────────

    def _build_manage_tab(self, parent) -> None:
        ctk.CTkLabel(
            parent, text="キャラクターを選択して編集・削除できます",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            text_color=AppTheme.TEXT_DIM,
        ).pack(anchor="w", padx=8, pady=(8, 4))

        self._detail_box = ctk.CTkTextbox(
            parent, state="disabled", height=200,
            font=ctk.CTkFont(family="Courier New", size=9),
            fg_color="#1a1a1a",
            text_color=AppTheme.TEXT,
        )
        self._detail_box.pack(fill="x", padx=8, pady=(0, 8))

        edit_row = ctk.CTkFrame(parent, fg_color="transparent")
        edit_row.pack(fill="x", padx=8, pady=(0, 8))
        self._btn_edit = ctk.CTkButton(
            edit_row, text="編集", height=32,
            fg_color=AppTheme.ACCENT, hover_color="#005a9e",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11),
            state="disabled", command=self._on_edit,
        )
        self._btn_edit.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self._btn_del = ctk.CTkButton(
            edit_row, text="削除", height=32,
            fg_color="#6b1a1a", hover_color="#8b2a2a",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11),
            state="disabled", command=self._on_delete,
        )
        self._btn_del.pack(side="left", expand=True, fill="x")

    def _build_char_card(self, char_id: str, char: dict) -> ctk.CTkFrame:
        enabled = char.get("enabled", True)
        role    = char.get("role", "")
        layer   = char.get("layer", "")
        role_color = _ROLE_COLORS.get(role, AppTheme.TEXT_DIM)

        card = ctk.CTkFrame(
            self._char_scroll,
            fg_color="#2a2a2a" if enabled else "#1e1e1e",
            corner_radius=6, height=52,
        )
        card.pack(fill="x", pady=2)
        card.pack_propagate(False)
        card.bind("<Button-1>", lambda e, i=char_id: self._on_card_click(i))

        # 左: 有効/無効スイッチ
        sw = ctk.CTkSwitch(
            card,
            text="",
            width=40,
            variable=tk.BooleanVar(value=enabled),
            onvalue=True, offvalue=False,
            fg_color="#333333",
            progress_color=AppTheme.ACCENT,
            command=lambda i=char_id, c=char: self._toggle_enable(i, c),
        )
        sw.pack(side="left", padx=(8, 4))
        if enabled:
            sw.select()
        else:
            sw.deselect()

        # 中: 名前 + バッジ
        mid = ctk.CTkFrame(card, fg_color="transparent")
        mid.pack(side="left", fill="both", expand=True, pady=4)
        ctk.CTkLabel(
            mid,
            text=char.get("name", char_id),
            font=ctk.CTkFont(family="Yu Gothic UI", size=11, weight="bold"),
            text_color=AppTheme.TEXT_HEAD if enabled else AppTheme.TEXT_DIM,
            anchor="w",
        ).pack(anchor="w")
        badge_row = ctk.CTkFrame(mid, fg_color="transparent")
        badge_row.pack(anchor="w")
        ctk.CTkLabel(
            badge_row,
            text=role,
            font=ctk.CTkFont(family="Yu Gothic UI", size=8),
            text_color=role_color,
        ).pack(side="left")
        ctk.CTkLabel(
            badge_row,
            text=f"  [{_LAYER_LABELS.get(layer, layer)}]",
            font=ctk.CTkFont(family="Yu Gothic UI", size=8),
            text_color=AppTheme.TEXT_DIM,
        ).pack(side="left")

        return card

    def refresh_chars(self) -> None:
        for w in self._char_scroll.winfo_children():
            w.destroy()
        self._characters = load_json(CHARACTERS_JSON).get("characters", {})
        self._selected_char_id: str | None = None
        for char_id, char in self._characters.items():
            self._build_char_card(char_id, char)
        self._show_detail(None)

    def _on_card_click(self, char_id: str) -> None:
        self._selected_char_id = char_id
        self._show_detail(self._characters.get(char_id))
        self._btn_edit.configure(state="normal")
        self._btn_del.configure(state="normal")

    def _show_detail(self, char: dict | None) -> None:
        self._detail_box.configure(state="normal")
        self._detail_box.delete("0.0", "end")
        if char:
            lines = [
                f"ID:       {char.get('id', '')}",
                f"名前:     {char.get('name', '')}",
                f"役割:     {char.get('role', '')}",
                f"プロンプト: {char.get('prompt_id', '')}",
                f"キーワード: {', '.join(char.get('keywords', []))}",
                f"AI制御:   {'はい' if char.get('is_ai') else 'いいえ'}",
                f"\n説明:\n{char.get('description', '')}",
            ]
            self._detail_box.insert("0.0", "\n".join(lines))
        self._detail_box.configure(state="disabled")

    def _toggle_enable(self, char_id: str, char: dict) -> None:
        char["enabled"] = not char.get("enabled", True)
        self._characters[char_id] = char
        save_json(CHARACTERS_JSON, {"characters": self._characters})
        self.refresh_chars()

    def _on_add(self) -> None:
        dlg = CharacterDialog(self.winfo_toplevel(),
                              existing_ids=list(self._characters.keys()))
        self.wait_window(dlg)
        if dlg.result:
            self._characters[dlg.result["id"]] = dlg.result
            save_json(CHARACTERS_JSON, {"characters": self._characters})
            self.refresh_chars()

    def _on_edit(self) -> None:
        cid = getattr(self, "_selected_char_id", None)
        if not cid:
            return
        dlg = CharacterDialog(
            self.winfo_toplevel(),
            char_data=self._characters[cid],
            existing_ids=list(self._characters.keys()),
        )
        self.wait_window(dlg)
        if dlg.result:
            self._characters[cid] = dlg.result
            save_json(CHARACTERS_JSON, {"characters": self._characters})
            self.refresh_chars()

    def _on_delete(self) -> None:
        cid = getattr(self, "_selected_char_id", None)
        if not cid:
            return
        if messagebox.askyesno("確認", f"'{cid}' を削除しますか？",
                               parent=self.winfo_toplevel()):
            del self._characters[cid]
            save_json(CHARACTERS_JSON, {"characters": self._characters})
            self.refresh_chars()

    # ── メーカータブ ─────────────────────────────────────────────

    def _build_maker_tab(self, parent) -> None:
        maker = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        maker.pack(fill="both", expand=True)

        # AI生成エリア
        ai_frame = ctk.CTkFrame(maker, fg_color="#1e1e1e", corner_radius=6)
        ai_frame.pack(fill="x", padx=8, pady=(8, 6))
        ctk.CTkLabel(
            ai_frame,
            text="1. AIに自動作成させる",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(8, 4))
        self._maker_input = ctk.CTkTextbox(
            ai_frame, height=70,
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            fg_color="#111111",
            text_color=AppTheme.TEXT,
        )
        self._maker_input.pack(fill="x", padx=10, pady=(0, 4))
        self._maker_input.insert("0.0", "例：射撃戦が得意な少女祓魔師。")
        self._maker_gen_btn = ctk.CTkButton(
            ai_frame,
            text="✨  AIで生成",
            height=34,
            fg_color=AppTheme.ACCENT,
            hover_color="#005a9e",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11),
            command=self._start_generate,
        )
        self._maker_gen_btn.pack(fill="x", padx=10, pady=(0, 8))

        # ステータス調整エリア
        stat_frame = ctk.CTkFrame(maker, fg_color="#1e1e1e", corner_radius=6)
        stat_frame.pack(fill="x", padx=8, pady=(0, 6))
        ctk.CTkLabel(
            stat_frame,
            text="2. ステータス調整",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(8, 4))

        grid = ctk.CTkFrame(stat_frame, fg_color="transparent")
        grid.pack(fill="x", padx=10, pady=(0, 8))

        def _entry_row(label: str, var, r: int, c: int, w: int = 5):
            ctk.CTkLabel(
                grid, text=label,
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                text_color=AppTheme.TEXT,
            ).grid(row=r, column=c * 2, sticky="e", padx=(4, 2), pady=2)
            ctk.CTkEntry(
                grid, textvariable=var, width=60,
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                fg_color="#111111", border_color="#444444",
            ).grid(row=r, column=c * 2 + 1, sticky="w", padx=(0, 8), pady=2)

        _entry_row("名前:", self.var_name, 0, 0)
        _entry_row("二つ名:", self.var_alias, 0, 1)
        _entry_row("体力(HP):", self.var_hp, 1, 0)
        _entry_row("霊力(SP):", self.var_sp, 1, 1)
        _entry_row("回避D:", self.var_evasion, 2, 0)
        _entry_row("機動力:", self.var_mobility, 2, 1)
        _entry_row("装甲:", self.var_armor, 3, 0)
        ctk.CTkFrame(stat_frame, height=1, fg_color="#333333").pack(fill="x", padx=10, pady=4)

        grid2 = ctk.CTkFrame(stat_frame, fg_color="transparent")
        grid2.pack(fill="x", padx=10, pady=(0, 4))
        _entry_row2 = lambda label, var, r, c: ctk.CTkLabel(
            grid2, text=label,
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            text_color=AppTheme.TEXT,
        ).grid(row=r, column=c * 2, sticky="e", padx=(4, 2), pady=2) or ctk.CTkEntry(
            grid2, textvariable=var, width=60,
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            fg_color="#111111", border_color="#444444",
        ).grid(row=r, column=c * 2 + 1, sticky="w", padx=(0, 8), pady=2)

        for (lbl, var), (r, c) in zip(
            [("体:", self.var_body), ("霊:", self.var_soul),
             ("巧:", self.var_skill), ("術:", self.var_magic)],
            [(0, 0), (0, 1), (1, 0), (1, 1)],
        ):
            ctk.CTkLabel(
                grid2, text=lbl,
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                text_color=AppTheme.TEXT,
            ).grid(row=r, column=c * 2, sticky="e", padx=(4, 2), pady=2)
            ctk.CTkEntry(
                grid2, textvariable=var, width=60,
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                fg_color="#111111", border_color="#444444",
            ).grid(row=r, column=c * 2 + 1, sticky="w", padx=(0, 8), pady=2)

        # メモ
        ctk.CTkLabel(
            stat_frame,
            text="キャラ設定・メモ",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(6, 2))
        self._maker_memo = ctk.CTkTextbox(
            stat_frame, height=100,
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            fg_color="#111111", text_color=AppTheme.TEXT,
        )
        self._maker_memo.pack(fill="x", padx=10, pady=(0, 8))

        # 保存・出力
        out_frame = ctk.CTkFrame(maker, fg_color="#1e1e1e", corner_radius=6)
        out_frame.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkLabel(
            out_frame, text="3. 保存と出力",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(8, 4))

        self._maker_status_var = tk.StringVar(value="ステータスを調整して出力してください")
        ctk.CTkLabel(
            out_frame, textvariable=self._maker_status_var,
            font=ctk.CTkFont(family="Yu Gothic UI", size=9),
            text_color=AppTheme.TEXT_DIM,
        ).pack(padx=10, pady=(0, 4))

        for text, cmd in [
            ("💾  このキャラを保存する", self._save_current),
            ("📋  ココフォリア用コマとしてコピー", self._copy_ccfolia),
        ]:
            ctk.CTkButton(
                out_frame, text=text, height=32,
                fg_color="#333333", hover_color="#444444",
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                command=cmd,
            ).pack(fill="x", padx=10, pady=2)

        ctk.CTkLabel(
            out_frame, text="保存済みキャラクター",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(8, 2))

        self._saved_scroll = ctk.CTkScrollableFrame(out_frame, fg_color="transparent", height=100)
        self._saved_scroll.pack(fill="x", padx=8, pady=(0, 4))

        load_del_row = ctk.CTkFrame(out_frame, fg_color="transparent")
        load_del_row.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkButton(
            load_del_row, text="読込", height=28,
            fg_color="#333333", hover_color="#444444",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            command=self._load_selected,
        ).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(
            load_del_row, text="削除", height=28,
            fg_color="#6b1a1a", hover_color="#8b2a2a",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            command=self._delete_selected,
        ).pack(side="left", expand=True, fill="x")

        self._selected_saved_file: Path | None = None
        self._refresh_saved_list()

    def _refresh_saved_list(self) -> None:
        for w in self._saved_scroll.winfo_children():
            w.destroy()
        self._saved_files = []
        if SAVED_PCS_DIR.exists():
            for p in sorted(SAVED_PCS_DIR.glob("*.json"),
                            key=lambda x: x.stat().st_mtime, reverse=True):
                self._saved_files.append(p)
                btn = ctk.CTkButton(
                    self._saved_scroll,
                    text=p.stem, anchor="w", height=28,
                    fg_color="#2a2a2a", hover_color="#3a3a3a",
                    text_color=AppTheme.TEXT,
                    font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                    command=lambda pp=p: self._on_saved_select(pp),
                )
                btn.pack(fill="x", pady=1)

    def _on_saved_select(self, p: Path) -> None:
        self._selected_saved_file = p

    def _load_selected(self) -> None:
        if not self._selected_saved_file:
            return
        data = load_json(self._selected_saved_file)
        self._apply_json_to_ui(data)
        self._maker_status_var.set(f"✓ {data.get('name', 'キャラ')} を読み込みました")

    def _delete_selected(self) -> None:
        if not self._selected_saved_file:
            return
        if messagebox.askyesno(
            "削除確認", f"{self._selected_saved_file.stem} を削除しますか？",
            parent=self.winfo_toplevel()
        ):
            self._selected_saved_file.unlink(missing_ok=True)
            self._selected_saved_file = None
            self._refresh_saved_list()

    def _apply_json_to_ui(self, data: dict) -> None:
        if "prof" in data or "main_stats" in data:
            data = self._convert_old_format(data)
        self._last_json_raw = data
        self.var_name.set(data.get("name", "名無し"))
        self.var_alias.set(data.get("alias", ""))
        self.var_hp.set(data.get("hp", 10))
        self.var_sp.set(data.get("sp", 10))
        self.var_evasion.set(data.get("evasion", 2))
        self.var_mobility.set(data.get("mobility", 2))
        self.var_armor.set(data.get("armor", 0))
        self.var_body.set(data.get("body", 3))
        self.var_soul.set(data.get("soul", 3))
        self.var_skill.set(data.get("skill", 3))
        self.var_magic.set(data.get("magic", 3))
        items = data.get("items", {})
        self.var_katashiro.set(items.get("katashiro", 1))
        self.var_haraegushi.set(items.get("haraegushi", 0))
        self.var_shimenawa.set(items.get("shimenawa", 0))
        self.var_juryudan.set(items.get("juryudan", 0))
        self.var_ireikigu.set(items.get("ireikigu", 0))
        self.var_meifuku.set(items.get("meifuku", 0))
        self.var_jutsuyen.set(items.get("jutsuyen", 0))
        self._maker_memo.delete("0.0", "end")
        self._maker_memo.insert("0.0", data.get("memo", ""))

    def _convert_old_format(self, data: dict) -> dict:
        flat: dict = {}
        prof = data.get("prof", {})
        flat["name"] = prof.get("name", "名無し")
        flat["alias"] = prof.get("alias", "")
        for key in ["body", "soul", "skill", "magic"]:
            vals = data.get("main_stats", {}).get(key, {})
            flat[key] = vals.get("final", vals.get("init", 3))
        for key in ["hp", "sp", "armor", "mobility"]:
            vals = data.get("sub_stats", {}).get(key, {})
            flat[key] = vals.get("final", vals.get("init", 0))
        flat["evasion"] = 2
        items: dict = {}
        item_key_map = {
            "形代": "katashiro", "祓串": "haraegushi", "注連鋼縄": "shimenawa",
            "呪瘤檀": "juryudan", "医霊器具": "ireikigu", "名伏": "meifuku", "術延起点": "jutsuyen",
        }
        for inv in data.get("inventory", []):
            name = inv.get("name", "")
            if name in item_key_map:
                items[item_key_map[name]] = inv.get("count", 0)
        flat["items"] = items
        memo_parts = []
        if data.get("memo"):
            memo_parts.append(data["memo"])
        for k in ["text_history", "text_career", "text_attendance", "text_health",
                  "text_seminary_report", "text_investigation",
                  "text_family_comments", "text_overall_remarks"]:
            if data.get(k):
                memo_parts.append(data[k])
        flat["memo"] = "\n\n".join(memo_parts)
        if "skills" in data:
            flat["skills"] = data["skills"]
        if "weapons" in data:
            flat["weapons"] = data["weapons"]
        return flat

    def _save_current(self) -> None:
        name = self.var_name.get().strip()
        if not name:
            messagebox.showerror("エラー", "名前を入力してください。", parent=self.winfo_toplevel())
            return
        data = self._last_json_raw.copy()
        data.update({
            "name": name, "alias": self.var_alias.get(),
            "hp": self.var_hp.get(), "sp": self.var_sp.get(),
            "evasion": self.var_evasion.get(), "mobility": self.var_mobility.get(),
            "armor": self.var_armor.get(),
            "body": self.var_body.get(), "soul": self.var_soul.get(),
            "skill": self.var_skill.get(), "magic": self.var_magic.get(),
            "memo": self._maker_memo.get("0.0", "end").strip(),
        })
        data["items"] = {
            "katashiro": self.var_katashiro.get(), "haraegushi": self.var_haraegushi.get(),
            "shimenawa": self.var_shimenawa.get(), "juryudan": self.var_juryudan.get(),
            "ireikigu": self.var_ireikigu.get(), "meifuku": self.var_meifuku.get(),
            "jutsuyen": self.var_jutsuyen.get(),
        }
        save_json(SAVED_PCS_DIR / f"{name}.json", data)
        self._maker_status_var.set(f"✓ {name} を保存しました！")
        self._refresh_saved_list()
        self.refresh_chars()

    def _copy_ccfolia(self) -> None:
        name = self.var_name.get()
        memo_text = f"【二つ名】{self.var_alias.get()}\n\n{self._maker_memo.get('0.0', 'end').strip()}"
        commands = "◆能力値を使った判定◆\n"
        commands += "{体}b6=>4  //【体】判定\n{霊}b6=>4  //【霊】判定\n"
        commands += "{巧}b6=>4  //【巧】判定\n{術}b6=>4  //【術】判定\n\n"
        commands += "◆特技◆\n"
        for skill in self._last_json_raw.get("skills", []):
            commands += f"【{skill.get('name', '')}】：{skill.get('description', '')}\n\n"
        commands += "◆攻撃祭具◆\n"
        for weapon in self._last_json_raw.get("weapons", []):
            commands += f"【{weapon.get('name', '')}】：{weapon.get('description', '')}\n\n"
        ccfolia_data = {
            "kind": "character",
            "data": {
                "name": name, "initiative": 0, "memo": memo_text, "commands": commands,
                "status": [
                    {"label": "体力",   "value": self.var_hp.get(),       "max": self.var_hp.get()},
                    {"label": "霊力",   "value": self.var_sp.get(),       "max": self.var_sp.get()},
                    {"label": "回避D",  "value": self.var_evasion.get(),  "max": self.var_evasion.get()},
                    {"label": "形代",   "value": self.var_katashiro.get(),"max": self.var_katashiro.get()},
                ],
                "params": [
                    {"label": "体", "value": str(self.var_body.get())},
                    {"label": "霊", "value": str(self.var_soul.get())},
                    {"label": "巧", "value": str(self.var_skill.get())},
                    {"label": "術", "value": str(self.var_magic.get())},
                ],
            },
        }
        self.clipboard_clear()
        self.clipboard_append(json.dumps(ccfolia_data, ensure_ascii=False))
        self.update()
        self._maker_status_var.set("✓ ококофォリア用にコピー！Ctrl+Vで貼り付け")
        messagebox.showinfo("コピー完了",
                            "コクフォリア用データをコピーしました！\nCtrl+V で貼り付けてください。",
                            parent=self.winfo_toplevel())

    # ── AI生成 ─────────────────────────────────────────────────

    def _build_char_prompt(self, user_req: str) -> str:
        return f"""あなたはTRPG『タクティカル祓魔師』のプレイヤーです。
ユーザーの要望に合わせて、以下のJSONフォーマットの空欄を論理的に埋めてください。
【重要】必ず有効なJSON形式のみを出力し、Markdownコードブロック(```json)などは使用しないでください。
ユーザー要望: {user_req}
{{"name":"","alias":"","hp":15,"sp":15,"evasion":2,"mobility":3,"armor":0,"body":3,"soul":3,"skill":3,"magic":3,"items":{{"katashiro":1,"haraegushi":0,"shimenawa":0,"juryudan":0,"ireikigu":0,"meifuku":0,"jutsuyen":0}},"memo":"","skills":[],"weapons":[]}}
"""

    def _start_generate(self) -> None:
        if not self._lm_client.is_server_running():
            messagebox.showerror("エラー", "LM-Studioが起動していません。",
                                 parent=self.winfo_toplevel())
            return
        self._maker_gen_btn.configure(state="disabled")
        self._maker_status_var.set("生成中...お待ちください")

        def run():
            user_req = self._maker_input.get("0.0", "end").strip()
            sys_prompt = "あなたはデータジェネレーターです。必ず指定されたJSON形式のみを出力し、余計な会話はしないでください。"
            result, _ = self._lm_client.generate_response(
                system_prompt=sys_prompt,
                user_message=self._build_char_prompt(user_req),
                temperature=0.7, max_tokens=1500, timeout=None, no_think=True,
            )
            _post_to_main(lambda r=result: self._on_gen_finish(r))

        threading.Thread(target=run, daemon=True).start()

    def _on_gen_finish(self, result: str) -> None:
        self._maker_gen_btn.configure(state="normal")
        if not result:
            self._maker_status_var.set("❌ 生成失敗")
            return
        clean = result.replace("```json", "").replace("```", "").strip()
        try:
            data = json.loads(clean)
            self._apply_json_to_ui(data)
            self._maker_status_var.set("✓ 生成完了！内容を調整してください")
            return
        except json.JSONDecodeError:
            pass
        data = parse_llm_json_robust(result)
        if data:
            self._apply_json_to_ui(data)
            self._maker_status_var.set("✓ 生成完了（フォールバック）！内容を調整してください")
        else:
            self._maker_status_var.set("❌ JSONパースエラー")

    # ── on_show フック ───────────────────────────────────────────

    def on_show(self) -> None:
        self.refresh_chars()
        self._refresh_saved_list()


# ─────────────────────────────────────────────────────────────────
# 世界観ビュー（WorldSettingTab 移植）
# ─────────────────────────────────────────────────────────────────

class WorldView(ctk.CTkFrame):
    """世界観・ルール設定エディター"""

    def __init__(self, parent):
        super().__init__(parent, fg_color=AppTheme.BG, corner_radius=0)
        self._text_boxes: dict[str, ctk.CTkTextbox] = {}
        self._rule_vars: dict[str, tk.BooleanVar] = {}
        self._rule_boxes: dict[str, ctk.CTkTextbox] = {}
        self._build_ui()
        self.load()

    def _build_ui(self) -> None:
        # ヘッダー
        header = ctk.CTkFrame(self, fg_color=AppTheme.SURFACE, corner_radius=0, height=48)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(
            header,
            text="🌍  世界観・ルール設定",
            font=ctk.CTkFont(family="Yu Gothic UI", size=14, weight="bold"),
            text_color=AppTheme.TEXT_HEAD,
        ).pack(side="left", padx=16, pady=10)

        # 保存ボタン群（ヘッダー右）
        self._status_var = tk.StringVar(value="")
        ctk.CTkLabel(
            header,
            textvariable=self._status_var,
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            text_color=AppTheme.TEXT_DIM,
        ).pack(side="right", padx=8)
        ctk.CTkButton(
            header, text="再読込", width=70, height=30,
            fg_color="#333333", hover_color="#444444",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            command=self.load,
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            header, text="保存", width=70, height=30,
            fg_color=AppTheme.ACCENT, hover_color="#005a9e",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11),
            command=self.save,
        ).pack(side="right", padx=(0, 4))

        # CTkTabview
        self._tab = ctk.CTkTabview(
            self, fg_color=AppTheme.SURFACE, corner_radius=0,
            segmented_button_fg_color=AppTheme.BG,
            segmented_button_selected_color=AppTheme.ACCENT,
            segmented_button_unselected_color="#333333",
            text_color=AppTheme.TEXT,
        )
        self._tab.pack(fill="both", expand=True)

        # ── 基本設定タブ ─────────────────────────────────────────
        basic = self._tab.add("基本設定")
        basic_scroll = ctk.CTkScrollableFrame(basic, fg_color="transparent")
        basic_scroll.pack(fill="both", expand=True)

        fields = [
            ("world_lore",       "🌐 世界観・基本設定",            8),
            ("session_scenario", "📜 シナリオ概要・あらすじ",       5),
            ("pc_skills",        "⚔️  PCスキル・現在のステータス",  6),
            ("gm_instructions",  "📋 GMへの追加指示",               4),
        ]
        for key, label, height in fields:
            ctk.CTkLabel(
                basic_scroll, text=label,
                font=ctk.CTkFont(family="Yu Gothic UI", size=10, weight="bold"),
                text_color=AppTheme.TEXT,
            ).pack(anchor="w", padx=10, pady=(10, 2))
            box = ctk.CTkTextbox(
                basic_scroll, height=height * 18,
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                fg_color="#1a1a1a", text_color=AppTheme.TEXT, wrap="word",
            )
            box.pack(fill="x", padx=10, pady=(0, 2))
            self._text_boxes[key] = box

        # ── ルールタブ群 ──────────────────────────────────────────
        rule_tabs = [
            ("シナリオ進行", "scenario_data_enabled",    "scenario_data"),
            ("追加ルール",   "additional_rules_enabled", "additional_rules"),
            ("コアルール",   "core_rules_enabled",       "core_rules"),
            ("キャラ作成",   "char_creation_enabled",    "char_creation"),
            ("成長ルール",   "growth_rules_enabled",     "growth_rules"),
        ]
        for tab_name, var_key, txt_key in rule_tabs:
            frame = self._tab.add(tab_name)
            var = tk.BooleanVar(value=False)
            self._rule_vars[var_key] = var

            sw_row = ctk.CTkFrame(frame, fg_color="transparent")
            sw_row.pack(fill="x", padx=10, pady=(10, 6))
            ctk.CTkSwitch(
                sw_row,
                text="このデータをAIの記憶に読み込ませる",
                variable=var, onvalue=True, offvalue=False,
                fg_color="#333333", progress_color=AppTheme.ACCENT,
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                text_color=AppTheme.TEXT,
            ).pack(side="left")

            box = ctk.CTkTextbox(
                frame,
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                fg_color="#1a1a1a", text_color=AppTheme.TEXT, wrap="word",
            )
            box.pack(fill="both", expand=True, padx=10, pady=(0, 8))
            self._rule_boxes[txt_key] = box

    def load(self) -> None:
        data = load_json(WORLD_SETTING_JSON)
        for key, box in self._text_boxes.items():
            box.delete("0.0", "end")
            box.insert("0.0", data.get(key, ""))
        for var_key, var in self._rule_vars.items():
            var.set(data.get(var_key, False))
        for txt_key, box in self._rule_boxes.items():
            box.delete("0.0", "end")
            box.insert("0.0", data.get(txt_key, ""))
        self._status_var.set("読み込み完了")

    def save(self) -> None:
        data = load_json(WORLD_SETTING_JSON)
        for key, box in self._text_boxes.items():
            data[key] = box.get("0.0", "end").strip()
        for var_key, var in self._rule_vars.items():
            data[var_key] = var.get()
        for txt_key, box in self._rule_boxes.items():
            data[txt_key] = box.get("0.0", "end").strip()
        save_json(WORLD_SETTING_JSON, data)
        self._status_var.set("保存しました")
        self.after(3_000, lambda: self._status_var.set(""))

    def on_show(self) -> None:
        self.load()


# ─────────────────────────────────────────────────────────────────
# プロンプトダイアログ（tk.Toplevel 流用）
# ─────────────────────────────────────────────────────────────────

class PromptDialog(tk.Toplevel):
    def __init__(self, parent, template_id: str | None = None,
                 template_data: dict | None = None, existing_ids: list | None = None):
        super().__init__(parent)
        self.result = None
        self.is_edit = template_id is not None
        self.existing_ids = existing_ids or []
        self.orig_id = template_id
        self.template_data = template_data or {}
        self.title("プロンプト編集" if self.is_edit else "プロンプト新規作成")
        self.geometry("640x640")
        self.grab_set()
        self._build_ui()
        self._load_data()
        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{px}+{py}")

    def _build_ui(self):
        pad = {"padx": 12, "pady": 4}
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="テンプレートID").grid(row=0, column=0, sticky="w", **pad)
        self.var_id = tk.StringVar()
        self.entry_id = ttk.Entry(frame, textvariable=self.var_id, width=35)
        self.entry_id.grid(row=0, column=1, sticky="ew", **pad)
        if self.is_edit:
            self.entry_id.config(state="disabled")
        ttk.Label(frame, text="System Prompt").grid(row=1, column=0, sticky="nw", **pad)
        self.text_system = scrolledtext.ScrolledText(frame, width=45, height=8, font=("", 10), wrap=tk.WORD)
        self.text_system.grid(row=1, column=1, sticky="ew", **pad)
        ttk.Label(frame, text="Instructions").grid(row=2, column=0, sticky="nw", **pad)
        self.text_instructions = scrolledtext.ScrolledText(frame, width=45, height=4, font=("", 10), wrap=tk.WORD)
        self.text_instructions.grid(row=2, column=1, sticky="ew", **pad)
        param_frame = ttk.LabelFrame(frame, text="LLMパラメータ", padding=8)
        param_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=8, padx=12)
        ttk.Label(param_frame, text="Temperature").grid(row=0, column=0, sticky="w", padx=8)
        self.var_temp = tk.DoubleVar(value=0.7)
        ttk.Spinbox(param_frame, textvariable=self.var_temp, from_=0.0, to=1.0,
                    increment=0.05, format="%.2f", width=8).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(param_frame, text="Max Tokens").grid(row=0, column=2, sticky="w", padx=8)
        self.var_tokens = tk.IntVar(value=200)
        ttk.Spinbox(param_frame, textvariable=self.var_tokens, from_=50, to=8000,
                    increment=10, width=8).grid(row=0, column=3, sticky="w", padx=8)
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=12)
        ttk.Button(btn_frame, text="保存", command=self._on_save, width=12).pack(side=tk.LEFT, padx=8)
        ttk.Button(btn_frame, text="キャンセル", command=self.destroy, width=12).pack(side=tk.LEFT, padx=8)

    def _load_data(self):
        if not self.template_data:
            return
        self.var_id.set(self.orig_id or "")
        self.text_system.insert("1.0", self.template_data.get("system", ""))
        self.text_instructions.insert("1.0", self.template_data.get("instructions", ""))
        self.var_temp.set(self.template_data.get("temperature", 0.7))
        self.var_tokens.set(self.template_data.get("max_tokens", 200))

    def _on_save(self):
        tmpl_id = self.var_id.get().strip()
        if not re.match(r"^[a-zA-Z0-9_]+$", tmpl_id):
            return messagebox.showerror("エラー", "IDは英数字と_のみ可能", parent=self)
        if not self.is_edit and tmpl_id in self.existing_ids:
            return messagebox.showerror("エラー", "重複しています", parent=self)
        self.result = {
            "id": tmpl_id,
            "system": self.text_system.get("1.0", tk.END).strip(),
            "instructions": self.text_instructions.get("1.0", tk.END).strip(),
            "temperature": float(self.var_temp.get()),
            "max_tokens": int(self.var_tokens.get()),
        }
        self.destroy()


# ─────────────────────────────────────────────────────────────────
# AI設定ビュー（PromptTab + SessionTab + GeneratorTab 統合）
# ─────────────────────────────────────────────────────────────────

class AIConfigView(ctk.CTkFrame):
    """AI設定 — プロンプト管理・セッション設定・コンテンツジェネレーター"""

    def __init__(self, parent):
        super().__init__(parent, fg_color=AppTheme.BG, corner_radius=0)
        sys.path.insert(0, str(CORE_DIR))
        from lm_client import LMClient
        self._lm_client = LMClient()
        self._templates: dict = {}
        self._char_vars: dict[str, tk.BooleanVar] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        # ヘッダー
        header = ctk.CTkFrame(self, fg_color=AppTheme.SURFACE, corner_radius=0, height=48)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(
            header,
            text="📝  AI設定 — プロンプト・セッション・ジェネレーター",
            font=ctk.CTkFont(family="Yu Gothic UI", size=14, weight="bold"),
            text_color=AppTheme.TEXT_HEAD,
        ).pack(side="left", padx=16, pady=10)

        # CTkTabview
        self._tab = ctk.CTkTabview(
            self, fg_color=AppTheme.SURFACE, corner_radius=0,
            segmented_button_fg_color=AppTheme.BG,
            segmented_button_selected_color=AppTheme.ACCENT,
            segmented_button_unselected_color="#333333",
            text_color=AppTheme.TEXT,
        )
        self._tab.pack(fill="both", expand=True)

        self._build_prompt_tab(self._tab.add("プロンプト"))
        self._build_session_tab(self._tab.add("セッション設定"))
        self._build_generator_tab(self._tab.add("ジェネレーター"))

    # ── プロンプトタブ ────────────────────────────────────────────

    def _build_prompt_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=2)
        parent.rowconfigure(0, weight=1)

        # 左: テンプレートリスト
        left = ctk.CTkFrame(parent, fg_color="#1e1e1e", corner_radius=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)

        ctk.CTkLabel(
            left, text="プロンプトテンプレート",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(8, 4))

        self._prompt_scroll = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self._prompt_scroll.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        btn_row = ctk.CTkFrame(left, fg_color="transparent")
        btn_row.pack(fill="x", padx=8, pady=(0, 8))
        for text, cmd in [("新規作成", self._prompt_add),
                           ("編集",     self._prompt_edit),
                           ("削除",     self._prompt_delete),
                           ("更新",     self.refresh_prompts)]:
            ctk.CTkButton(
                btn_row, text=text, height=26,
                fg_color="#333333", hover_color="#444444",
                font=ctk.CTkFont(family="Yu Gothic UI", size=9),
                command=cmd,
            ).pack(side="left", expand=True, fill="x", padx=1)

        # 右: プレビュー
        right = ctk.CTkFrame(parent, fg_color="#1e1e1e", corner_radius=6)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)

        ctk.CTkLabel(
            right, text="プレビュー",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(8, 4))

        self._prompt_preview = ctk.CTkTextbox(
            right, state="disabled",
            font=ctk.CTkFont(family="Courier New", size=9),
            fg_color="#111111", text_color=AppTheme.TEXT, wrap="word",
        )
        self._prompt_preview.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._selected_prompt_id: str | None = None
        self.refresh_prompts()

    def refresh_prompts(self) -> None:
        for w in self._prompt_scroll.winfo_children():
            w.destroy()
        self._templates = load_json(PROMPTS_JSON).get("templates", {})
        for tmpl_id in self._templates:
            btn = ctk.CTkButton(
                self._prompt_scroll,
                text=tmpl_id, anchor="w", height=30,
                fg_color="#2a2a2a", hover_color="#3a3a3a",
                text_color=AppTheme.TEXT,
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                command=lambda i=tmpl_id: self._prompt_select(i),
            )
            btn.pack(fill="x", pady=1)

    def _prompt_select(self, tmpl_id: str) -> None:
        self._selected_prompt_id = tmpl_id
        tmpl = self._templates.get(tmpl_id, {})
        self._prompt_preview.configure(state="normal")
        self._prompt_preview.delete("0.0", "end")
        lines = [
            f"ID: {tmpl_id}",
            f"Temp: {tmpl.get('temperature', '')}  Tokens: {tmpl.get('max_tokens', '')}",
            f"\n[System]\n{tmpl.get('system', '')}",
            f"\n[Instructions]\n{tmpl.get('instructions', '')}",
        ]
        self._prompt_preview.insert("0.0", "\n".join(lines))
        self._prompt_preview.configure(state="disabled")

    def _prompt_add(self) -> None:
        dlg = PromptDialog(self.winfo_toplevel(), existing_ids=list(self._templates.keys()))
        self.wait_window(dlg)
        if dlg.result:
            new_id = dlg.result.pop("id")
            self._templates[new_id] = dlg.result
            save_json(PROMPTS_JSON, {"templates": self._templates})
            self.refresh_prompts()

    def _prompt_edit(self) -> None:
        if not self._selected_prompt_id:
            return
        tmpl_id = self._selected_prompt_id
        dlg = PromptDialog(self.winfo_toplevel(),
                           template_id=tmpl_id,
                           template_data=self._templates[tmpl_id],
                           existing_ids=list(self._templates.keys()))
        self.wait_window(dlg)
        if dlg.result:
            dlg.result.pop("id", None)
            self._templates[tmpl_id] = dlg.result
            save_json(PROMPTS_JSON, {"templates": self._templates})
            self.refresh_prompts()

    def _prompt_delete(self) -> None:
        if not self._selected_prompt_id:
            return
        tmpl_id = self._selected_prompt_id
        if messagebox.askyesno("確認", f"'{tmpl_id}' を削除しますか？",
                               parent=self.winfo_toplevel()):
            del self._templates[tmpl_id]
            save_json(PROMPTS_JSON, {"templates": self._templates})
            self.refresh_prompts()

    # ── セッション設定タブ ────────────────────────────────────────

    def _build_session_tab(self, parent) -> None:
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=8, pady=8)

        # セッション情報
        info_frame = ctk.CTkFrame(scroll, fg_color="#1e1e1e", corner_radius=6)
        info_frame.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            info_frame, text="セッション情報",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(8, 4))

        row1 = ctk.CTkFrame(info_frame, fg_color="transparent")
        row1.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            row1, text="セッション名",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            text_color=AppTheme.TEXT, width=100, anchor="e",
        ).pack(side="left", padx=(0, 8))
        self._sess_name_var = tk.StringVar()
        ctk.CTkEntry(
            row1, textvariable=self._sess_name_var,
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            fg_color="#111111", border_color="#444444",
        ).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            info_frame, text="メモ",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(4, 2))
        self._sess_memo = ctk.CTkTextbox(
            info_frame, height=60,
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            fg_color="#111111", text_color=AppTheme.TEXT,
        )
        self._sess_memo.pack(fill="x", padx=10, pady=(0, 8))

        # キャラクター選択
        char_frame = ctk.CTkFrame(scroll, fg_color="#1e1e1e", corner_radius=6)
        char_frame.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            char_frame, text="このセッションで使用するキャラクター",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(8, 4))

        self._char_check_frame = ctk.CTkScrollableFrame(
            char_frame, fg_color="transparent", height=150
        )
        self._char_check_frame.pack(fill="x", padx=10, pady=(0, 8))

        # ボタン群
        btn_row = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_row.pack(fill="x")
        for text, cmd in [
            ("保存", self._sess_save),
            ("読み込み", self._sess_load),
            ("キャラ一覧を更新", self._sess_refresh_chars),
        ]:
            ctk.CTkButton(
                btn_row, text=text, height=32,
                fg_color="#333333", hover_color="#444444",
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                command=cmd,
            ).pack(side="left", padx=4)

        self._sess_load()

    def _sess_refresh_chars(self, selected_ids: list | None = None) -> None:
        for w in self._char_check_frame.winfo_children():
            w.destroy()
        self._char_vars.clear()
        for char_id, char in load_json(CHARACTERS_JSON).get("characters", {}).items():
            var = tk.BooleanVar(
                value=(char_id in selected_ids) if selected_ids else char.get("enabled", True)
            )
            self._char_vars[char_id] = var
            row = ctk.CTkFrame(self._char_check_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkCheckBox(
                row,
                text=f"{char.get('name', char_id)}  [{char.get('role', '')}]",
                variable=var, onvalue=True, offvalue=False,
                fg_color=AppTheme.ACCENT,
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                text_color=AppTheme.TEXT,
            ).pack(side="left", padx=8)

    def _sess_save(self) -> None:
        name = self._sess_name_var.get().strip()
        if not name:
            messagebox.showwarning("入力エラー", "セッション名を入力してください",
                                   parent=self.winfo_toplevel())
            return
        selected = [cid for cid, var in self._char_vars.items() if var.get()]
        save_json(SESSION_JSON, {
            "session_name": name,
            "memo": self._sess_memo.get("0.0", "end").strip(),
            "selected_characters": selected,
        })
        messagebox.showinfo("完了", "セッション設定を保存しました",
                            parent=self.winfo_toplevel())

    def _sess_load(self) -> None:
        data = load_json(SESSION_JSON)
        self._sess_name_var.set(data.get("session_name", ""))
        self._sess_memo.delete("0.0", "end")
        self._sess_memo.insert("0.0", data.get("memo", ""))
        self._sess_refresh_chars(selected_ids=data.get("selected_characters"))

    # ── ジェネレータータブ ────────────────────────────────────────

    def _build_generator_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=2)
        parent.rowconfigure(0, weight=1)

        left = ctk.CTkFrame(parent, fg_color="#1e1e1e", corner_radius=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)

        ctk.CTkLabel(
            left, text="作成対象",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(8, 2))

        self._gen_target_var = tk.StringVar(value="エネミー（敵）作成")
        ctk.CTkComboBox(
            left,
            variable=self._gen_target_var,
            values=["エネミー（敵）作成", "シナリオ概要・イベント作成",
                    "アイテム・祭具作成", "その他（カスタム）"],
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            fg_color="#111111", border_color="#444444",
            button_color="#333333",
            state="readonly",
        ).pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(
            left, text="追加要望・テーマなど",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(0, 2))

        self._gen_input = ctk.CTkTextbox(
            left, height=150,
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            fg_color="#111111", text_color=AppTheme.TEXT,
        )
        self._gen_input.pack(fill="x", padx=10, pady=(0, 8))

        self._gen_btn = ctk.CTkButton(
            left, text="✨  生成開始", height=34,
            fg_color=AppTheme.ACCENT, hover_color="#005a9e",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11),
            command=self._gen_start,
        )
        self._gen_btn.pack(fill="x", padx=10, pady=(0, 4))

        self._gen_status_var = tk.StringVar(value="待機中...")
        ctk.CTkLabel(
            left, textvariable=self._gen_status_var,
            font=ctk.CTkFont(family="Yu Gothic UI", size=9),
            text_color=AppTheme.TEXT_DIM,
        ).pack(padx=10, pady=(0, 4))

        ctk.CTkButton(
            left, text="📋  全文コピー", height=28,
            fg_color="#333333", hover_color="#444444",
            font=ctk.CTkFont(family="Yu Gothic UI", size=9),
            command=self._gen_copy,
        ).pack(fill="x", padx=10, pady=(0, 8))

        right = ctk.CTkFrame(parent, fg_color="#1e1e1e", corner_radius=6)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)

        ctk.CTkLabel(
            right, text="生成結果",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(8, 4))

        self._gen_output = ctk.CTkTextbox(
            right,
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            fg_color="#111111", text_color=AppTheme.TEXT, wrap="word",
        )
        self._gen_output.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _gen_start(self) -> None:
        if not self._lm_client.is_server_running():
            messagebox.showerror("エラー", "LM-Studioが起動していません。",
                                 parent=self.winfo_toplevel())
            return
        self._gen_btn.configure(state="disabled")
        self._gen_status_var.set("生成中 (ルールと世界観を統合して構築中...)")

        def run():
            user_req = self._gen_input.get("0.0", "end").strip()
            try:
                compressed_path = BASE_DIR / "configs" / "world_setting_compressed.txt"
                compressed_data = compressed_path.read_text(encoding="utf-8") if compressed_path.exists() else ""
            except Exception:
                compressed_data = ""
            sys_prompt = (
                "あなたはTRPG『タクティカル祓魔師』の厳格なシステム管理者であり、熟練GMです。\n"
                f"【公式ルール・世界観データ】\n{compressed_data}\n\n"
                "【絶対厳守事項】\n"
                "1. オリジナルスキルの捏造は絶対に許されません。\n"
                "2. 初期能力値やHP等のパラメータは、チートにならない範囲でルールに則り決定してください。\n"
                "3. 世界観データを踏まえ、キャラクターの背景設定も作成してください。\n"
                "4. 必ずJSONで出力してください。"
            )
            user_msg = f"以下の要望に合うデータを生成してください。\n要望: {user_req}"
            try:
                result, _ = self._lm_client.generate_response(
                    system_prompt=sys_prompt, user_message=user_msg,
                    temperature=0.4, max_tokens=8192, timeout=None,
                )
                _post_to_main(lambda r=result: self._gen_finish(r))
            except Exception as e:
                _post_to_main(lambda err=e: self._gen_status_var.set(f"❌ 内部エラー: {err}"))
                _post_to_main(lambda: self._gen_btn.configure(state="normal"))

        threading.Thread(target=run, daemon=True).start()

    def _gen_finish(self, result: str) -> None:
        self._gen_btn.configure(state="normal")
        self._gen_output.delete("0.0", "end")
        self._gen_output.insert("0.0", result)
        self._gen_status_var.set("✓ AI生成完了！")

    def _gen_copy(self) -> None:
        text = self._gen_output.get("0.0", "end").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._gen_status_var.set("✓ 全文をコピーしました")

    # ── on_show フック ───────────────────────────────────────────

    def on_show(self) -> None:
        self.refresh_prompts()
        self._sess_load()


# ─────────────────────────────────────────────────────────────────
# システムビュー（EnvSettingsTab + DependencyTab + AddonManagementTab 統合）
# ─────────────────────────────────────────────────────────────────

class SystemView(ctk.CTkFrame):
    """システム設定 — 環境設定・依存関係・アドオン管理"""

    _ENV_PATH    = CONFIGS_DIR / ".env"
    _ENV_EXAMPLE = CONFIGS_DIR / ".env.example"

    _FIELDS = [
        ("BROWSER_USE_PROVIDER", "Browser Use プロバイダー", "local",                "推論エンジン",          "combo"),
        ("BROWSER_USE_MODEL",    "Browser Use モデル",       "",                     "モデル名（空欄=自動）", "combo"),
        ("LM_STUDIO_URL",        "LM Studio URL",            "http://localhost:1234","ローカル LLM サーバー", "entry"),
        ("VLM_PROVIDER",         "VLM プロバイダー",          "local",                "Canvas 解析用 VLM",     "combo"),
        ("VLM_MODEL",            "VLM モデル",               "",                     "VLM モデル名（空欄=自動）", "combo"),
        ("OPENAI_API_KEY",       "OpenAI API Key",           "",                     "クラウド利用時（任意）", "password"),
        ("ANTHROPIC_API_KEY",    "Anthropic API Key",        "",                     "クラウド利用時（任意）", "password"),
    ]
    _COMBO_OPTIONS: dict[str, list[str]] = {
        "BROWSER_USE_PROVIDER": ["local", "openai", "anthropic"],
        "BROWSER_USE_MODEL":    ["", "gpt-4o", "gpt-4o-mini", "claude-sonnet-4-20250514"],
        "VLM_PROVIDER":         ["local", "openai"],
        "VLM_MODEL":            ["", "gpt-4o", "gpt-4o-mini"],
    }

    def __init__(self, parent):
        super().__init__(parent, fg_color=AppTheme.BG, corner_radius=0)
        self._env_vars: dict[str, tk.StringVar] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        # ヘッダー
        header = ctk.CTkFrame(self, fg_color=AppTheme.SURFACE, corner_radius=0, height=48)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(
            header,
            text="⚙️   システム — 環境設定・依存関係・アドオン管理",
            font=ctk.CTkFont(family="Yu Gothic UI", size=14, weight="bold"),
            text_color=AppTheme.TEXT_HEAD,
        ).pack(side="left", padx=16, pady=10)

        # CTkTabview
        self._tab = ctk.CTkTabview(
            self, fg_color=AppTheme.SURFACE, corner_radius=0,
            segmented_button_fg_color=AppTheme.BG,
            segmented_button_selected_color=AppTheme.ACCENT,
            segmented_button_unselected_color="#333333",
            text_color=AppTheme.TEXT,
        )
        self._tab.pack(fill="both", expand=True)

        self._build_env_tab(self._tab.add("環境設定"))
        self._build_dep_tab(self._tab.add("依存関係"))
        self._build_addon_tab(self._tab.add("アドオン管理"))

    # ── 環境設定タブ ──────────────────────────────────────────────

    def _build_env_tab(self, parent) -> None:
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=8, pady=8)

        frm = ctk.CTkFrame(scroll, fg_color="#1e1e1e", corner_radius=6)
        frm.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            frm, text="環境設定 (configs/.env)",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(8, 6))

        for key, label, default, desc, input_type in self._FIELDS:
            row = ctk.CTkFrame(frm, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=3)
            ctk.CTkLabel(
                row, text=label, width=160, anchor="e",
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                text_color=AppTheme.TEXT,
            ).pack(side="left", padx=(0, 8))

            var = tk.StringVar(value=default)
            self._env_vars[key] = var

            if input_type == "password":
                entry = ctk.CTkEntry(
                    row, textvariable=var, show="*", width=280,
                    font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                    fg_color="#111111", border_color="#444444",
                )
                entry.pack(side="left")
                ctk.CTkButton(
                    row, text="表示", width=50, height=24,
                    fg_color="#333333", hover_color="#444444",
                    font=ctk.CTkFont(family="Yu Gothic UI", size=9),
                    command=lambda e=entry: self._toggle_show(e),
                ).pack(side="left", padx=(4, 0))
            elif input_type == "combo":
                ctk.CTkComboBox(
                    row, variable=var, width=240,
                    values=self._COMBO_OPTIONS.get(key, []),
                    font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                    fg_color="#111111", border_color="#444444",
                    button_color="#333333",
                ).pack(side="left")
            else:
                ctk.CTkEntry(
                    row, textvariable=var, width=320,
                    font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                    fg_color="#111111", border_color="#444444",
                ).pack(side="left")

            ctk.CTkLabel(
                row, text=f"  {desc}",
                font=ctk.CTkFont(family="Yu Gothic UI", size=9),
                text_color=AppTheme.TEXT_DIM,
            ).pack(side="left")

        # ボタン群
        btn_row = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_row.pack(fill="x", pady=8)

        self._env_status_var = tk.StringVar(value="")
        for text, cmd in [
            ("保存", self._env_save),
            ("再読み込み", self._env_load),
            (".env.example からコピー", self._env_copy_example),
        ]:
            ctk.CTkButton(
                btn_row, text=text, height=32,
                fg_color="#333333", hover_color="#444444",
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                command=cmd,
            ).pack(side="left", padx=4)

        ctk.CTkLabel(
            btn_row, textvariable=self._env_status_var,
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            text_color=AppTheme.OK,
        ).pack(side="left", padx=12)

        # LM Studio 接続テスト
        lm_frm = ctk.CTkFrame(scroll, fg_color="#1e1e1e", corner_radius=6)
        lm_frm.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            lm_frm, text="ローカル AI (LM Studio) 接続テスト",
            font=ctk.CTkFont(family="Yu Gothic UI", size=11, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(8, 4))

        lm_row = ctk.CTkFrame(lm_frm, fg_color="transparent")
        lm_row.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            lm_row, text="接続状態:",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            text_color=AppTheme.TEXT,
        ).pack(side="left")
        self._lm_result_var = tk.StringVar(value="未確認")
        self._lm_result_lbl = ctk.CTkLabel(
            lm_row, textvariable=self._lm_result_var,
            font=ctk.CTkFont(family="Yu Gothic UI", size=10, weight="bold"),
            text_color=AppTheme.TEXT_DIM,
        )
        self._lm_result_lbl.pack(side="left", padx=8)
        ctk.CTkButton(
            lm_row, text="接続テスト", width=100, height=28,
            fg_color=AppTheme.ACCENT, hover_color="#005a9e",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            command=self._lm_test,
        ).pack(side="left")

        self._lm_models_var = tk.StringVar(value="")
        ctk.CTkLabel(
            lm_frm, textvariable=self._lm_models_var,
            font=ctk.CTkFont(family="Yu Gothic UI", size=9),
            text_color=AppTheme.TEXT_DIM,
        ).pack(anchor="w", padx=10, pady=(0, 8))

        self._env_load()

    @staticmethod
    def _toggle_show(entry: ctk.CTkEntry) -> None:
        entry.configure(show="" if entry.cget("show") == "*" else "*")

    def _env_load(self) -> None:
        if not self._ENV_PATH.exists():
            self._env_status_var.set(".env ファイルが見つかりません")
            return
        try:
            with open(self._ENV_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, _, v = line.partition("=")
                        k, v = k.strip(), v.strip()
                        if k in self._env_vars:
                            self._env_vars[k].set(v)
            self._env_status_var.set("✓ 読み込み完了")
        except Exception as e:
            self._env_status_var.set(f"読み込みエラー: {e}")

    def _env_save(self) -> None:
        CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            lines = ["# 環境設定 — GUI から自動生成", "# 手動編集も可能です", ""]
            for key, _label, _default, desc, _ in self._FIELDS:
                value = self._env_vars[key].get().strip()
                if value:
                    lines.append(f"# {desc}")
                    lines.append(f"{key}={value}")
                else:
                    lines.append(f"# {key}=")
                lines.append("")
            with open(self._ENV_PATH, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self._env_status_var.set("✓ 保存しました")
        except Exception as e:
            self._env_status_var.set(f"保存エラー: {e}")

    def _env_copy_example(self) -> None:
        if not self._ENV_EXAMPLE.exists():
            self._env_status_var.set(".env.example が見つかりません")
            return
        shutil.copy2(self._ENV_EXAMPLE, self._ENV_PATH)
        self._env_load()
        self._env_status_var.set("✓ .env.example からコピーしました")

    def _lm_test(self) -> None:
        url = self._env_vars.get("LM_STUDIO_URL", tk.StringVar()).get().strip() or "http://localhost:1234"
        self._lm_result_var.set("接続中...")
        self._lm_result_lbl.configure(text_color=AppTheme.TEXT_DIM)

        def _check():
            try:
                r = requests.get(f"{url}/v1/models", timeout=3)
                if r.status_code == 200:
                    models = [m.get("id", "?") for m in r.json().get("data", [])]
                    model_text = ", ".join(models[:5]) if models else "(モデル未ロード)"
                    _post_to_main(lambda mt=model_text: self._set_lm_result(True, mt))
                else:
                    _post_to_main(lambda s=r.status_code: self._set_lm_result(False, f"HTTP {s}"))
            except Exception as exc:
                _post_to_main(lambda e=str(exc): self._set_lm_result(False, e))

        threading.Thread(target=_check, daemon=True).start()

    def _set_lm_result(self, ok: bool, detail: str) -> None:
        if ok:
            self._lm_result_var.set("✓ 接続成功")
            self._lm_result_lbl.configure(text_color=AppTheme.OK)
            self._lm_models_var.set(f"利用可能モデル: {detail}")
        else:
            self._lm_result_var.set("✗ 接続失敗 — LM Studio を起動してください")
            self._lm_result_lbl.configure(text_color=AppTheme.ERROR)
            self._lm_models_var.set(f"エラー: {detail}")

    # ── 依存関係タブ ──────────────────────────────────────────────

    def _build_dep_tab(self, parent) -> None:
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(8, 4))

        self._dep_summary_var = tk.StringVar(value="チェック中...")
        ctk.CTkLabel(
            top, textvariable=self._dep_summary_var,
            font=ctk.CTkFont(family="Yu Gothic UI", size=11, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            top, text="再チェック", width=90, height=28,
            fg_color="#333333", hover_color="#444444",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10),
            command=self._dep_check,
        ).pack(side="right")

        # ttk.Treeview は CTkFrame 内に埋め込んで流用（混在OK）
        tree_frame = ctk.CTkFrame(parent, fg_color=AppTheme.SURFACE, corner_radius=6)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        cols = ("status", "package", "group", "description", "install")
        self._dep_tree = ttk.Treeview(
            tree_frame, columns=cols, show="headings", height=14
        )
        for col, width, label in [
            ("status", 60, "状態"), ("package", 180, "パッケージ"),
            ("group", 120, "グループ"), ("description", 200, "説明"),
            ("install", 300, "インストールコマンド"),
        ]:
            self._dep_tree.heading(col, text=label)
            self._dep_tree.column(col, width=width, anchor="center" if col == "status" else "w")
        self._dep_tree.tag_configure("ok",      foreground="#4ec9b0")
        self._dep_tree.tag_configure("missing", foreground="#f44747")

        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._dep_tree.yview)
        self._dep_tree.configure(yscrollcommand=sb.set)
        self._dep_tree.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        sb.pack(side="right", fill="y", pady=4)

        # インストールボタン
        inst_frame = ctk.CTkFrame(parent, fg_color="#1e1e1e", corner_radius=6)
        inst_frame.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkLabel(
            inst_frame, text="一括インストール",
            font=ctk.CTkFont(family="Yu Gothic UI", size=10, weight="bold"),
            text_color=AppTheme.TEXT,
        ).pack(anchor="w", padx=10, pady=(6, 4))

        inst_row = ctk.CTkFrame(inst_frame, fg_color="transparent")
        inst_row.pack(fill="x", padx=10, pady=(0, 8))
        for label, group in [
            ("Browser Use 連携", "browser_use"),
            ("ナレッジ検索", "knowledge"),
            ("開発ツール", "dev"),
        ]:
            ctk.CTkButton(
                inst_row, text=f"{label}をインストール", height=28,
                fg_color="#333333", hover_color="#444444",
                font=ctk.CTkFont(family="Yu Gothic UI", size=9),
                command=lambda g=group: self._dep_install(g),
            ).pack(side="left", padx=4)

        self._dep_inst_status_var = tk.StringVar(value="")
        ctk.CTkLabel(
            inst_frame, textvariable=self._dep_inst_status_var,
            font=ctk.CTkFont(family="Yu Gothic UI", size=9),
            text_color=AppTheme.TEXT_DIM,
        ).pack(anchor="w", padx=10, pady=(0, 4))

        self.after(200, self._dep_check)

    def _dep_check(self) -> None:
        try:
            from dependency_checker import _GROUP_LABELS, check_all
            self._dep_tree.delete(*self._dep_tree.get_children())
            results = check_all()
            missing = 0
            for r in results:
                tag = "ok" if r.installed else "missing"
                status = ("✓" + (f" ({r.version})" if r.version else "")) if r.installed else "✗"
                group_label = _GROUP_LABELS.get(r.dep.group, r.dep.group)
                install_cmd = f'pip install "{r.dep.pip_name}"' if not r.installed else ""
                self._dep_tree.insert("", "end",
                    values=(status, r.dep.pip_name, group_label, r.dep.description, install_cmd),
                    tags=(tag,))
                if not r.installed:
                    missing += 1
            self._dep_summary_var.set(
                "✓ 全パッケージインストール済み" if missing == 0
                else f"✗ {missing} 個のパッケージが不足しています"
            )
        except Exception as e:
            self._dep_summary_var.set(f"チェックエラー: {e}")

    def _dep_install(self, group: str) -> None:
        try:
            from dependency_checker import _GROUP_LABELS, install_group
            label = _GROUP_LABELS.get(group, group)
            self._dep_inst_status_var.set(f"{label} をインストール中...")

            def _run():
                ok, output = install_group(group)
                _post_to_main(lambda o=ok, out=output: self._dep_install_done(o, group, out))

            threading.Thread(target=_run, daemon=True).start()
        except Exception as e:
            self._dep_inst_status_var.set(f"エラー: {e}")

    def _dep_install_done(self, ok: bool, group: str, output: str) -> None:
        try:
            from dependency_checker import _GROUP_LABELS
            label = _GROUP_LABELS.get(group, group)
        except Exception:
            label = group
        if ok:
            self._dep_inst_status_var.set(f"✓ {label} のインストール完了")
        else:
            self._dep_inst_status_var.set(f"✗ {label} のインストール失敗")
            messagebox.showerror("インストールエラー",
                                 f"{label} のインストールに失敗しました:\n\n{output[-500:]}",
                                 parent=self.winfo_toplevel())
        self._dep_check()

    # ── アドオン管理タブ ──────────────────────────────────────────

    def _build_addon_tab(self, parent) -> None:
        # AddonManagementTab を CTkFrame 内に埋め込む
        try:
            from addon_management_tab import AddonManagementTab
            wrapper = tk.Frame(parent, bg=AppTheme.BG)
            wrapper.pack(fill="both", expand=True)
            self._addon_mgr_tab = AddonManagementTab(wrapper)
            self._addon_mgr_tab.pack(fill="both", expand=True)
        except Exception as e:
            ctk.CTkLabel(
                parent,
                text=f"アドオン管理の読み込みエラー:\n{e}",
                font=ctk.CTkFont(family="Yu Gothic UI", size=10),
                text_color=AppTheme.ERROR,
            ).pack(expand=True)

    def get_addon_enabled_ids(self) -> list[str]:
        """現在有効なアドオンIDリストを返す"""
        try:
            from addon_management_tab import AddonStateManager
            state_mgr = AddonStateManager(ADDON_STATE_JSON)
            return state_mgr.state.get("enabled", [])
        except Exception:
            return []

    # ── on_show フック ───────────────────────────────────────────

    def on_show(self) -> None:
        self._env_load()
        self._dep_check()


# ─────────────────────────────────────────────────────────────────
# メインアプリケーション
# ─────────────────────────────────────────────────────────────────

class TacticalAILauncherV2(ctk.CTk):
    """タクティカル祓魔師 TRPG AI — 統合ランチャー v2"""

    def __init__(self):
        super().__init__()
        _setup_ctk_appearance()
        self.title("タクティカル祓魔師 AI — 統合ランチャー v2")
        self.geometry("1200x780")
        self.minsize(900, 620)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._views: dict[str, ctk.CTkFrame] = {}
        self._current_view: str | None = None

        self._build_layout()
        self._init_views()
        self._load_addon_sidebar_slots()
        self._show_view("home")
        self.status_bar.start_polling()
        self.after(40, self._drain_ui_queue)

    def _build_layout(self) -> None:
        # grid で3ペイン固定配置
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)
        self.grid_columnconfigure(0, minsize=190, weight=0)
        self.grid_columnconfigure(1, weight=1)

        # サイドバー
        self.sidebar = Sidebar(self, on_nav_click=self._show_view)
        self.sidebar.grid(row=0, column=0, sticky="nsw")

        # コンテンツエリア
        self.content_area = ctk.CTkFrame(self, fg_color=AppTheme.BG, corner_radius=0)
        self.content_area.grid(row=0, column=1, sticky="nsew")

        # ステータスバー
        self.status_bar = StatusBar(self)
        self.status_bar.grid(row=1, column=0, columnspan=2, sticky="ew")

    def _init_views(self) -> None:
        self._views["home"]   = HomeView(self.content_area)
        self._views["actors"] = ActorsView(self.content_area)
        self._views["world"]  = WorldView(self.content_area)
        self._views["ai"]     = AIConfigView(self.content_area)
        self._views["system"] = SystemView(self.content_area)

    def _show_view(self, view_key: str) -> None:
        if self._current_view and self._current_view in self._views:
            self._views[self._current_view].pack_forget()
        if view_key not in self._views:
            return
        self._views[view_key].pack(fill="both", expand=True)
        self._current_view = view_key
        self.sidebar.set_active(view_key)
        on_show = getattr(self._views[view_key], "on_show", None)
        if callable(on_show):
            on_show()

    def _load_addon_sidebar_slots(self) -> None:
        """有効なアドオンの GUI タブをサイドバーに動的追加"""
        if not ADDONS_DIR.is_dir():
            return
        try:
            root_dir = str(BASE_DIR)
            if root_dir not in sys.path:
                sys.path.insert(0, root_dir)

            from core.addons import AddonManager
            from addon_management_tab import AddonStateManager

            state_mgr = AddonStateManager(ADDON_STATE_JSON)
            enabled_ids = state_mgr.state.get("enabled", [])

            mgr = AddonManager(addons_dir=ADDONS_DIR)
            manifests = mgr.discover()

            for manifest in manifests:
                if manifest.id not in enabled_ids:
                    continue
                if not manifest.gui_tab or not manifest.gui_tab_label:
                    continue
                try:
                    addon_dir  = ADDONS_DIR / manifest.id
                    entry_path = addon_dir / manifest.entry_point
                    spec = importlib.util.spec_from_file_location(
                        f"addons.{manifest.id}", entry_path
                    )
                    if not (spec and spec.loader):
                        continue
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)

                    # ttk.Frame サブクラスを自動検出
                    tab_cls = next(
                        (c for c in mod.__dict__.values()
                         if isinstance(c, type)
                         and issubclass(c, ttk.Frame)
                         and c is not ttk.Frame),
                        None,
                    )
                    if not tab_cls:
                        continue

                    # ttk.Frame タブを CTkFrame でラップ
                    view_key = f"addon_{manifest.id}"
                    wrapper = ctk.CTkFrame(self.content_area, fg_color=AppTheme.BG, corner_radius=0)
                    ttk_tab = tab_cls(wrapper)
                    ttk_tab.pack(fill="both", expand=True)
                    self._views[view_key] = wrapper

                    self.sidebar.add_addon_button(
                        view_key,
                        manifest.gui_tab_label,
                        on_click=lambda k=view_key: self._show_view(k),
                    )
                except Exception as e:
                    print(f"[launcher_v2] アドオンタブ読み込みエラー ({manifest.id}): {e}")
        except Exception as e:
            print(f"[launcher_v2] アドオンスキャンエラー: {e}")

    # ── メニュー的なヘルパー ──────────────────────────────────────

    def _drain_ui_queue(self) -> None:
        """バックグラウンドスレッドからポストされた UI コールバックをメインスレッドで実行"""
        try:
            while True:
                _UI_QUEUE.get_nowait()()
        except _queue.Empty:
            pass
        self.after(40, self._drain_ui_queue)

    def get_home_view(self) -> HomeView:
        return self._views["home"]  # type: ignore[return-value]

    # ── ウィンドウクローズ ────────────────────────────────────────

    def _on_close(self) -> None:
        home: HomeView = self._views.get("home")  # type: ignore[assignment]
        if home:
            proc = getattr(home, "_proc", None)
            if proc and proc.poll() is None:
                if not messagebox.askyesno(
                    "終了確認",
                    "CCFoliaコネクターが動作中です。\n終了しますか？",
                    parent=self,
                ):
                    return
                proc.terminate()
        self.destroy()


# ─────────────────────────────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = TacticalAILauncherV2()
    app.mainloop()
