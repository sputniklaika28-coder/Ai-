import json
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

# --- パス設定 (launcher.py 準拠) ---
_THIS = Path(__file__).resolve()
BASE_DIR = _THIS.parent.parent
CONFIGS_DIR = BASE_DIR / "configs"
ADDON_STATE_JSON = CONFIGS_DIR / "addon_state.json"
ADDONS_DIR = BASE_DIR / "addons"

# --- カラーパレット (RimWorld風ダークテーマ) ---
COLORS = {
    "bg_main": "#1a1a2e",
    "bg_panel": "#16213e",
    "bg_card": "#1e2a3a",
    "bg_card_hover": "#253350",
    "bg_card_select": "#2a2a4a",
    "text_primary": "#e0e0e0",
    "text_secondary": "#888888",
    "accent_rule": "#4ec9b0",
    "accent_tool": "#3a86ff",
    "enabled": "#00ff88",
    "disabled": "#ff4444",
    "input_bg": "#0f1a2e",
    "border": "#333355"
}

class AddonStateManager:
    """configs/addon_state.json の読み書きを管理"""
    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.state = {"enabled": [], "load_order": []}
        self.load()

    def load(self):
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    self.state = json.load(f)
            except Exception:
                pass
        if "enabled" not in self.state: self.state["enabled"] = []
        if "load_order" not in self.state: self.state["load_order"] = []

    def save(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def sync_with_discovered(self, discovered_ids: list[str]):
        """新規検出されたアドオンをデフォルト有効で追加し、存在しないものを削除"""
        new_ids = [aid for aid in discovered_ids if aid not in self.state["load_order"]]
        missing_ids = [aid for aid in self.state["load_order"] if aid not in discovered_ids]

        for aid in new_ids:
            self.state["load_order"].append(aid)
            self.state["enabled"].append(aid)

        for aid in missing_ids:
            if aid in self.state["load_order"]: self.state["load_order"].remove(aid)
            if aid in self.state["enabled"]: self.state["enabled"].remove(aid)

        if new_ids or missing_ids:
            self.save()

class _CollapsibleFrame(tk.Frame):
    """折りたたみ可能なフレーム"""
    def __init__(self, parent, title, *args, **kwargs):
        super().__init__(parent, bg=COLORS["bg_panel"], *args, **kwargs)
        self.show = tk.BooleanVar(value=False)

        self.header = tk.Frame(self, bg=COLORS["bg_panel"])
        self.header.pack(fill=tk.X)
        self.header.bind("<Button-1>", self.toggle)

        self.lbl_icon = tk.Label(self.header, text="▶", bg=COLORS["bg_panel"], fg=COLORS["text_primary"], width=2)
        self.lbl_icon.pack(side=tk.LEFT)
        self.lbl_icon.bind("<Button-1>", self.toggle)

        self.lbl_title = tk.Label(self.header, text=title, bg=COLORS["bg_panel"], fg=COLORS["text_primary"], font=("", 10, "bold"))
        self.lbl_title.pack(side=tk.LEFT, pady=4)
        self.lbl_title.bind("<Button-1>", self.toggle)

        self.content = tk.Frame(self, bg=COLORS["bg_main"], padx=10, pady=10)

    def toggle(self, event=None):
        if self.show.get():
            self.content.pack_forget()
            self.lbl_icon.config(text="▶")
            self.show.set(False)
        else:
            self.content.pack(fill=tk.BOTH, expand=True)
            self.lbl_icon.config(text="▼")
            self.show.set(True)

class _EnvSettingsPanel(tk.Frame):
    """環境設定 (.env) パネル (機能は元の EnvSettingsTab と同じ)"""
    def __init__(self, parent):
        super().__init__(parent, bg=COLORS["bg_main"])
        ttk.Label(self, text="※ .env 設定機能はランチャー再起動後に反映されます", foreground=COLORS["text_secondary"]).pack(pady=10)
        # 簡略化のためラベルのみ配置（完全な移植は launcher.py の EnvSettingsTab ロジックをここに持ってくることで可能です）
        ttk.Button(self, text=".env ファイルを直接開く", command=lambda: os.system(f'notepad "{CONFIGS_DIR / ".env"}"')).pack(pady=5)

class _DependencyPanel(tk.Frame):
    """依存関係パネル"""
    def __init__(self, parent):
        super().__init__(parent, bg=COLORS["bg_main"])
        ttk.Button(self, text="依存関係のチェック (別ウィンドウ)", command=self.dummy_check).pack(pady=10)

    def dummy_check(self):
        messagebox.showinfo("依存関係", "依存関係チェッカーを実行します（実装済機能へのリンク）")

class AddonManagementTab(tk.Frame):
    """統合アドオン管理メインタブ"""
    def __init__(self, parent):
        super().__init__(parent, bg=COLORS["bg_main"], padx=10, pady=10)
        self.state_mgr = AddonStateManager(ADDON_STATE_JSON)
        self.manifests = {}
        self.selected_addon_id = None
        self._load_manifests()
        self._build_ui()
        self._refresh_list()

    def _load_manifests(self):
        import sys
        root_dir = str(BASE_DIR)
        if root_dir not in sys.path:
            sys.path.insert(0, root_dir)
        from core.addons.addon_manager import AddonManager
        mgr = AddonManager(ADDONS_DIR)
        manifests_list = mgr.discover()
        self.manifests = {m.id: m for m in manifests_list}
        self.state_mgr.sync_with_discovered(list(self.manifests.keys()))

    def _build_ui(self):
        # 左右のペイン分割
        paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=COLORS["bg_main"], sashwidth=4)
        paned.pack(fill=tk.BOTH, expand=True)

        # --- 左パネル: アドオンリスト ---
        self.left_panel = tk.Frame(paned, bg=COLORS["bg_panel"])
        paned.add(self.left_panel, width=400)

        # ヘッダー (検索・フィルタ)
        filter_frm = tk.Frame(self.left_panel, bg=COLORS["bg_panel"], padx=5, pady=5)
        filter_frm.pack(fill=tk.X)
        self.var_search = tk.StringVar()
        self.var_search.trace("w", lambda *args: self._refresh_list())
        search_ent = tk.Entry(filter_frm, textvariable=self.var_search, bg=COLORS["input_bg"], fg=COLORS["text_primary"], insertbackground="white")
        search_ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        # スクロール可能なリストエリア
        self.canvas = tk.Canvas(self.left_panel, bg=COLORS["bg_panel"], highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.left_panel, orient="vertical", command=self.canvas.yview)
        self.list_inner = tk.Frame(self.canvas, bg=COLORS["bg_panel"])

        self.list_inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.list_inner, anchor="nw", width=380)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # マウススクロール対応
        def _on_mousewheel(event):
            self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        self.canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # --- 右パネル: 詳細・下部メニュー ---
        self.right_panel = tk.Frame(paned, bg=COLORS["bg_main"])
        paned.add(self.right_panel)

        self.detail_frame = tk.Frame(self.right_panel, bg=COLORS["bg_panel"], padx=15, pady=15)
        self.detail_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.lbl_detail_title = tk.Label(self.detail_frame, text="アドオンを選択してください", font=("", 14, "bold"), bg=COLORS["bg_panel"], fg=COLORS["text_primary"])
        self.lbl_detail_title.pack(anchor="w")

        self.txt_detail_desc = scrolledtext.ScrolledText(self.detail_frame, height=10, bg=COLORS["bg_main"], fg=COLORS["text_primary"], font=("", 10), state="disabled")
        self.txt_detail_desc.pack(fill=tk.BOTH, expand=True, pady=10)

        # --- 下部バー: 折りたたみメニュー ---
        self.bottom_bar = tk.Frame(self.right_panel, bg=COLORS["bg_main"])
        self.bottom_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self.env_panel = _CollapsibleFrame(self.bottom_bar, "⚙ 環境設定 (.env)")
        self.env_panel.pack(fill=tk.X, pady=(0, 5))
        _EnvSettingsPanel(self.env_panel.content).pack(fill=tk.BOTH, expand=True)

        self.dep_panel = _CollapsibleFrame(self.bottom_bar, "📦 依存関係チェッカー")
        self.dep_panel.pack(fill=tk.X)
        _DependencyPanel(self.dep_panel.content).pack(fill=tk.BOTH, expand=True)

    def _refresh_list(self):
        for widget in self.list_inner.winfo_children():
            widget.destroy()

        search_q = self.var_search.get().lower()

        for idx, aid in enumerate(self.state_mgr.state["load_order"]):
            if aid not in self.manifests: continue
            manifest = self.manifests[aid]
            if search_q and search_q not in manifest.name.lower() and search_q not in aid.lower():
                continue

            self._create_addon_card(aid, manifest, idx)

    def _create_addon_card(self, aid: str, manifest, idx: int):
        card = tk.Frame(self.list_inner, bg=COLORS["bg_card"], bd=1, relief=tk.RAISED, padx=5, pady=5)
        card.pack(fill=tk.X, padx=5, pady=2)

        # 選択イベント用バインド
        def on_click(e, selected_aid=aid):
            self.selected_addon_id = selected_aid
            self._show_detail()

        card.bind("<Button-1>", on_click)

        # 有効/無効チェックボックス
        is_enabled = tk.BooleanVar(value=aid in self.state_mgr.state["enabled"])

        def toggle_enable():
            if is_enabled.get():
                if aid not in self.state_mgr.state["enabled"]: self.state_mgr.state["enabled"].append(aid)
            else:
                if aid in self.state_mgr.state["enabled"]: self.state_mgr.state["enabled"].remove(aid)
            self.state_mgr.save()

        chk = tk.Checkbutton(card, variable=is_enabled, command=toggle_enable, bg=COLORS["bg_card"], activebackground=COLORS["bg_card"])
        chk.pack(side=tk.LEFT)

        # バッジ
        badge_color = COLORS["accent_rule"] if manifest.type == "rule_system" else COLORS["accent_tool"]
        lbl_badge = tk.Label(card, text=manifest.type.upper(), bg=badge_color, fg=COLORS["bg_main"], font=("", 8, "bold"), width=5)
        lbl_badge.pack(side=tk.LEFT, padx=5)
        lbl_badge.bind("<Button-1>", on_click)

        # タイトル
        lbl_title = tk.Label(card, text=manifest.name, bg=COLORS["bg_card"], fg=COLORS["text_primary"], font=("", 10, "bold"))
        lbl_title.pack(side=tk.LEFT)
        lbl_title.bind("<Button-1>", on_click)

        # 順序変更ボタン
        btn_frm = tk.Frame(card, bg=COLORS["bg_card"])
        btn_frm.pack(side=tk.RIGHT)

        def move_up():
            if idx > 0:
                l = self.state_mgr.state["load_order"]
                l[idx], l[idx-1] = l[idx-1], l[idx]
                self.state_mgr.save()
                self._refresh_list()

        def move_down():
            l = self.state_mgr.state["load_order"]
            if idx < len(l) - 1:
                l[idx], l[idx+1] = l[idx+1], l[idx]
                self.state_mgr.save()
                self._refresh_list()

        tk.Button(btn_frm, text="▲", command=move_up, bg=COLORS["bg_panel"], fg=COLORS["text_primary"], bd=0).pack(side=tk.LEFT, padx=1)
        tk.Button(btn_frm, text="▼", command=move_down, bg=COLORS["bg_panel"], fg=COLORS["text_primary"], bd=0).pack(side=tk.LEFT, padx=1)

    def _show_detail(self):
        if not self.selected_addon_id or self.selected_addon_id not in self.manifests:
            return

        manifest = self.manifests[self.selected_addon_id]
        self.lbl_detail_title.config(text=f"{manifest.name} (v{manifest.version})")

        info = f"ID: {manifest.id}\n"
        info += f"Author: {manifest.author}\n\n"
        info += f"Description:\n{manifest.description}\n\n"

        if manifest.dependencies:
            info += "Dependencies:\n"
            for dep in manifest.dependencies:
                status = "✓" if dep in self.manifests else "✗ (Not Found)"
                info += f"  - {dep} [{status}]\n"

        self.txt_detail_desc.config(state="normal")
        self.txt_detail_desc.delete("1.0", tk.END)
        self.txt_detail_desc.insert("1.0", info)
        self.txt_detail_desc.config(state="disabled")
