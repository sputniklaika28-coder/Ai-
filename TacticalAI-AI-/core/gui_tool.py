# ================================
# ファイル: core/gui_tool.py
# タクティカル祓魔師TRPG AIシステム - GUI設定ツール
# ================================

import json
import shutil
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

# --- パス設定 ---
_THIS = Path(__file__).resolve()
if _THIS.parent.name == "core":
    BASE_DIR = _THIS.parent.parent
else:
    BASE_DIR = _THIS.parent
CONFIGS_DIR = BASE_DIR / "configs"
CHARACTERS_JSON = CONFIGS_DIR / "characters.json"
PROMPTS_JSON = CONFIGS_DIR / "prompts.json"
SESSION_JSON = CONFIGS_DIR / "session_config.json"
SESSIONS_DIR = BASE_DIR / "sessions"

# ==========================================
# ユーティリティ関数
# ==========================================


def load_json(path: Path) -> dict:
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
        except json.JSONDecodeError:
            return {}
    return {}


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_template_ids() -> list:
    data = load_json(PROMPTS_JSON)
    return list(data.get("templates", {}).keys())


# ==========================================
# キャラクター編集ダイアログ
# ==========================================


class CharacterDialog(tk.Toplevel):
    LAYERS = ["meta", "setting", "player"]
    ROLES = ["game_master", "npc_manager", "enemy", "player"]

    def __init__(self, parent, char_data: dict = None, existing_ids: list = None):
        super().__init__(parent)
        self.result = None
        self.is_edit = char_data is not None
        self.existing_ids = existing_ids or []
        self.char_data = char_data or {}

        self.title("キャラクター編集" if self.is_edit else "キャラクター追加")
        self.geometry("500x520")
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

        ttk.Label(frame, text="キャラクターID（英数字・_のみ）").grid(
            row=0, column=0, sticky="w", **pad
        )
        self.var_id = tk.StringVar()
        self.entry_id = ttk.Entry(frame, textvariable=self.var_id, width=35)
        self.entry_id.grid(row=0, column=1, sticky="w", **pad)
        if self.is_edit:
            self.entry_id.config(state="disabled")

        ttk.Label(frame, text="名前（表示用）").grid(row=1, column=0, sticky="w", **pad)
        self.var_name = tk.StringVar()
        ttk.Entry(frame, textvariable=self.var_name, width=35).grid(
            row=1, column=1, sticky="w", **pad
        )

        ttk.Label(frame, text="レイヤー").grid(row=2, column=0, sticky="w", **pad)
        self.var_layer = tk.StringVar()
        ttk.Combobox(
            frame, textvariable=self.var_layer, values=self.LAYERS, state="readonly", width=20
        ).grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(frame, text="役割").grid(row=3, column=0, sticky="w", **pad)
        self.var_role = tk.StringVar()
        ttk.Combobox(
            frame, textvariable=self.var_role, values=self.ROLES, state="readonly", width=20
        ).grid(row=3, column=1, sticky="w", **pad)

        ttk.Label(frame, text="プロンプトテンプレート").grid(row=4, column=0, sticky="w", **pad)
        self.var_prompt = tk.StringVar()
        self.cb_prompt = ttk.Combobox(
            frame,
            textvariable=self.var_prompt,
            values=get_template_ids(),
            state="readonly",
            width=30,
        )
        self.cb_prompt.grid(row=4, column=1, sticky="w", **pad)

        ttk.Label(frame, text="説明").grid(row=5, column=0, sticky="nw", **pad)
        self.text_desc = tk.Text(frame, width=35, height=3, font=("", 10))
        self.text_desc.grid(row=5, column=1, sticky="w", **pad)

        self.var_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="有効", variable=self.var_enabled).grid(
            row=6, column=0, sticky="w", **pad
        )
        self.var_is_ai = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="AI制御", variable=self.var_is_ai).grid(
            row=6, column=1, sticky="w", **pad
        )

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=7, column=0, columnspan=2, pady=14)
        ttk.Button(btn_frame, text="保存", command=self._on_save, width=12).pack(
            side=tk.LEFT, padx=8
        )
        ttk.Button(btn_frame, text="キャンセル", command=self.destroy, width=12).pack(
            side=tk.LEFT, padx=8
        )

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

    def _on_save(self):
        char_id = self.var_id.get().strip()
        name = self.var_name.get().strip()
        import re

        if not re.match(r"^[a-zA-Z0-9_]+$", char_id):
            messagebox.showerror("エラー", "IDは英数字と_のみ使用可能です", parent=self)
            return
        if not self.is_edit and char_id in self.existing_ids:
            messagebox.showerror("エラー", f"ID '{char_id}' はすでに使用されています", parent=self)
            return
        if not name:
            messagebox.showerror("エラー", "名前を入力してください", parent=self)
            return
        if not self.var_prompt.get():
            messagebox.showerror("エラー", "プロンプトテンプレートを選択してください", parent=self)
            return

        self.result = {
            "id": char_id,
            "name": name,
            "layer": self.var_layer.get(),
            "role": self.var_role.get(),
            "description": self.text_desc.get("1.0", tk.END).strip(),
            "enabled": self.var_enabled.get(),
            "is_ai": self.var_is_ai.get(),
            "prompt_id": self.var_prompt.get(),
        }
        self.destroy()


# ==========================================
# プロンプト編集ダイアログ
# ==========================================


class PromptDialog(tk.Toplevel):
    def __init__(
        self, parent, template_id: str = None, template_data: dict = None, existing_ids: list = None
    ):
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
        self.text_system = scrolledtext.ScrolledText(
            frame, width=45, height=8, font=("", 10), wrap=tk.WORD
        )
        self.text_system.grid(row=1, column=1, sticky="ew", **pad)

        ttk.Label(frame, text="Instructions").grid(row=2, column=0, sticky="nw", **pad)
        self.text_instructions = scrolledtext.ScrolledText(
            frame, width=45, height=4, font=("", 10), wrap=tk.WORD
        )
        self.text_instructions.grid(row=2, column=1, sticky="ew", **pad)

        param_frame = ttk.LabelFrame(frame, text="LLMパラメータ", padding=8)
        param_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=8, padx=12)

        ttk.Label(param_frame, text="Temperature").grid(row=0, column=0, sticky="w", padx=8)
        self.var_temp = tk.DoubleVar(value=0.7)
        ttk.Spinbox(
            param_frame,
            textvariable=self.var_temp,
            from_=0.0,
            to=1.0,
            increment=0.05,
            format="%.2f",
            width=8,
        ).grid(row=0, column=1, sticky="w", padx=8)

        ttk.Label(param_frame, text="Max Tokens").grid(row=0, column=2, sticky="w", padx=8)
        self.var_tokens = tk.IntVar(value=200)
        ttk.Spinbox(
            param_frame, textvariable=self.var_tokens, from_=50, to=500, increment=10, width=8
        ).grid(row=0, column=3, sticky="w", padx=8)

        ttk.Label(param_frame, text="Top P").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.var_topp = tk.DoubleVar(value=0.85)
        ttk.Spinbox(
            param_frame,
            textvariable=self.var_topp,
            from_=0.0,
            to=1.0,
            increment=0.05,
            format="%.2f",
            width=8,
        ).grid(row=1, column=1, sticky="w", padx=8)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=12)
        ttk.Button(btn_frame, text="保存", command=self._on_save, width=12).pack(
            side=tk.LEFT, padx=8
        )
        ttk.Button(btn_frame, text="キャンセル", command=self.destroy, width=12).pack(
            side=tk.LEFT, padx=8
        )

    def _load_data(self):
        if not self.template_data:
            return
        self.var_id.set(self.orig_id or "")
        self.text_system.insert("1.0", self.template_data.get("system", ""))
        self.text_instructions.insert("1.0", self.template_data.get("instructions", ""))
        self.var_temp.set(self.template_data.get("temperature", 0.7))
        self.var_tokens.set(self.template_data.get("max_tokens", 200))
        self.var_topp.set(self.template_data.get("top_p", 0.85))

    def _on_save(self):
        import re

        tmpl_id = self.var_id.get().strip()
        if not re.match(r"^[a-zA-Z0-9_]+$", tmpl_id):
            return messagebox.showerror("エラー", "IDは英数字と_のみ使用可能です", parent=self)
        if not self.is_edit and tmpl_id in self.existing_ids:
            return messagebox.showerror("エラー", f"ID '{tmpl_id}' は使用済です", parent=self)
        try:
            temp = float(self.var_temp.get())
            topp = float(self.var_topp.get())
            tokens = int(self.var_tokens.get())
            if not (0.0 <= temp <= 1.0):
                raise ValueError("Temperature")
            if not (0.0 <= topp <= 1.0):
                raise ValueError("Top P")
            if not (50 <= tokens <= 500):
                raise ValueError("Max Tokens")
        except ValueError as e:
            return messagebox.showerror("エラー", f"パラメータの値が不正です: {e}", parent=self)

        self.result = {
            "id": tmpl_id,
            "system": self.text_system.get("1.0", tk.END).strip(),
            "instructions": self.text_instructions.get("1.0", tk.END).strip(),
            "temperature": round(temp, 2),
            "max_tokens": tokens,
            "top_p": round(topp, 2),
        }
        self.destroy()


# ==========================================
# タブ1：キャラクター管理
# ==========================================


class CharacterTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=12)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        left = ttk.Frame(self)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(left, text="キャラクター一覧", font=("", 11, "bold")).pack(
            anchor="w", pady=(0, 4)
        )
        list_frame = ttk.Frame(left)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.listbox = tk.Listbox(
            list_frame, selectmode=tk.SINGLE, font=("", 11), width=36, activestyle="dotbox"
        )
        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        right = ttk.Frame(self, padding=(12, 0))
        right.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Button(right, text="追加", command=self._on_add, width=12).pack(pady=4)
        ttk.Button(right, text="編集", command=self._on_edit, width=12).pack(pady=4)
        ttk.Button(right, text="削除", command=self._on_delete, width=12).pack(pady=4)
        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        ttk.Button(right, text="更新", command=self.refresh, width=12).pack(pady=4)

        ttk.Label(right, text="詳細", font=("", 10, "bold")).pack(anchor="w", pady=(8, 2))
        self.detail_text = tk.Text(
            right, width=26, height=14, state="disabled", font=("", 9), wrap=tk.WORD, bg="#f5f5f5"
        )
        self.detail_text.pack(fill=tk.BOTH, expand=True)

    def refresh(self):
        data = load_json(CHARACTERS_JSON)
        self.characters = data.get("characters", {})
        self.listbox.delete(0, tk.END)
        for char_id, char in self.characters.items():
            enabled_mark = "✓" if char.get("enabled") else "✗"
            self.listbox.insert(tk.END, f" {enabled_mark}  {char.get('name', char_id)}")
        self._show_detail(None)

    def _on_select(self, event=None):
        idx = self.listbox.curselection()
        if not idx:
            return
        char_id = list(self.characters.keys())[idx[0]]
        self._show_detail(self.characters[char_id])

    def _show_detail(self, char):
        self.detail_text.config(state="normal")
        self.detail_text.delete("1.0", tk.END)
        if char:
            lines = [
                f"ID: {char.get('id', '')}",
                f"名前: {char.get('name', '')}",
                f"レイヤー: {char.get('layer', '')}",
                f"役割: {char.get('role', '')}",
                f"プロンプト: {char.get('prompt_id', '')}",
                f"有効: {'はい' if char.get('enabled') else 'いいえ'}",
                f"AI制御: {'はい' if char.get('is_ai') else 'いいえ'}",
                f"\n説明:\n{char.get('description', '')}",
            ]
            self.detail_text.insert("1.0", "\n".join(lines))
        self.detail_text.config(state="disabled")

    def _on_add(self):
        dlg = CharacterDialog(self.winfo_toplevel(), existing_ids=list(self.characters.keys()))
        self.wait_window(dlg)
        if dlg.result:
            chars = load_json(CHARACTERS_JSON).get("characters", {})
            chars[dlg.result["id"]] = dlg.result
            save_json(CHARACTERS_JSON, {"characters": chars})
            self.refresh()

    def _on_edit(self):
        idx = self.listbox.curselection()
        if not idx:
            return
        char_id = list(self.characters.keys())[idx[0]]
        dlg = CharacterDialog(
            self.winfo_toplevel(),
            char_data=self.characters[char_id],
            existing_ids=list(self.characters.keys()),
        )
        self.wait_window(dlg)
        if dlg.result:
            chars = load_json(CHARACTERS_JSON).get("characters", {})
            chars[char_id] = dlg.result
            save_json(CHARACTERS_JSON, {"characters": chars})
            self.refresh()

    def _on_delete(self):
        idx = self.listbox.curselection()
        if not idx:
            return
        char_id = list(self.characters.keys())[idx[0]]
        if messagebox.askyesno("確認", f"'{char_id}' を削除しますか？"):
            chars = load_json(CHARACTERS_JSON).get("characters", {})
            if char_id in chars:
                del chars[char_id]
            save_json(CHARACTERS_JSON, {"characters": chars})
            self.refresh()


# ==========================================
# タブ2：プロンプト管理
# ==========================================


class PromptTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=12)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        left = ttk.Frame(self)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(left, text="プロンプトテンプレート", font=("", 11, "bold")).pack(
            anchor="w", pady=(0, 4)
        )
        list_frame = ttk.Frame(left)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self.listbox = tk.Listbox(
            list_frame, selectmode=tk.SINGLE, font=("", 11), width=36, activestyle="dotbox"
        )
        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        right = ttk.Frame(self, padding=(12, 0))
        right.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Button(right, text="新規作成", command=self._on_add, width=12).pack(pady=4)
        ttk.Button(right, text="編集", command=self._on_edit, width=12).pack(pady=4)
        ttk.Button(right, text="削除", command=self._on_delete, width=12).pack(pady=4)
        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        ttk.Button(right, text="更新", command=self.refresh, width=12).pack(pady=4)

        ttk.Label(right, text="プレビュー", font=("", 10, "bold")).pack(anchor="w", pady=(8, 2))
        self.preview_text = tk.Text(
            right, width=28, height=16, state="disabled", font=("", 9), wrap=tk.WORD, bg="#f5f5f5"
        )
        self.preview_text.pack(fill=tk.BOTH, expand=True)

    def refresh(self):
        self.templates = load_json(PROMPTS_JSON).get("templates", {})
        self.listbox.delete(0, tk.END)
        for tmpl_id in self.templates:
            self.listbox.insert(tk.END, f"  {tmpl_id}")
        self._show_preview(None)

    def _on_select(self, event=None):
        idx = self.listbox.curselection()
        if not idx:
            return
        tmpl_id = list(self.templates.keys())[idx[0]]
        self._show_preview(tmpl_id, self.templates[tmpl_id])

    def _show_preview(self, tmpl_id, tmpl=None):
        self.preview_text.config(state="normal")
        self.preview_text.delete("1.0", tk.END)
        if tmpl and tmpl_id:
            lines = [
                f"ID: {tmpl_id}",
                f"Temp: {tmpl.get('temperature', '')}",
                f"Tokens: {tmpl.get('max_tokens', '')}",
                f"TopP: {tmpl.get('top_p', '')}",
                f"\n[System]\n{tmpl.get('system', '')}",
                f"\n[Instructions]\n{tmpl.get('instructions', '')}",
            ]
            self.preview_text.insert("1.0", "\n".join(lines))
        self.preview_text.config(state="disabled")

    def _on_add(self):
        dlg = PromptDialog(self.winfo_toplevel(), existing_ids=list(self.templates.keys()))
        self.wait_window(dlg)
        if dlg.result:
            tmpls = load_json(PROMPTS_JSON).get("templates", {})
            new_id = dlg.result.pop("id")
            tmpls[new_id] = dlg.result
            save_json(PROMPTS_JSON, {"templates": tmpls})
            self.refresh()

    def _on_edit(self):
        idx = self.listbox.curselection()
        if not idx:
            return
        tmpl_id = list(self.templates.keys())[idx[0]]
        dlg = PromptDialog(
            self.winfo_toplevel(),
            template_id=tmpl_id,
            template_data=self.templates[tmpl_id],
            existing_ids=list(self.templates.keys()),
        )
        self.wait_window(dlg)
        if dlg.result:
            tmpls = load_json(PROMPTS_JSON).get("templates", {})
            dlg.result.pop("id", None)
            tmpls[tmpl_id] = dlg.result
            save_json(PROMPTS_JSON, {"templates": tmpls})
            self.refresh()

    def _on_delete(self):
        idx = self.listbox.curselection()
        if not idx:
            return
        tmpl_id = list(self.templates.keys())[idx[0]]
        if messagebox.askyesno("確認", f"'{tmpl_id}' を削除しますか？"):
            tmpls = load_json(PROMPTS_JSON).get("templates", {})
            if tmpl_id in tmpls:
                del tmpls[tmpl_id]
            save_json(PROMPTS_JSON, {"templates": tmpls})
            self.refresh()


# ==========================================
# タブ3：セッション設定
# ==========================================


class SessionTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=16)
        self._build_ui()
        self._load_session()

    def _build_ui(self):
        name_frame = ttk.LabelFrame(self, text="セッション情報", padding=10)
        name_frame.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(name_frame, text="セッション名").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.var_session_name = tk.StringVar()
        ttk.Entry(name_frame, textvariable=self.var_session_name, width=40).grid(
            row=0, column=1, sticky="w", padx=8
        )

        ttk.Label(name_frame, text="メモ").grid(row=1, column=0, sticky="nw", padx=8, pady=4)
        self.text_memo = tk.Text(name_frame, width=40, height=3, font=("", 10))
        self.text_memo.grid(row=1, column=1, sticky="w", padx=8)

        char_frame = ttk.LabelFrame(self, text="このセッションで使用するキャラクター", padding=10)
        char_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        scroll_frame = ttk.Frame(char_frame)
        scroll_frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(scroll_frame, bg="white", highlightthickness=0)
        sb = ttk.Scrollbar(scroll_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.config(yscrollcommand=sb.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.check_inner = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.check_inner, anchor="nw")
        self.check_inner.bind(
            "<Configure>", lambda e: self.canvas.config(scrollregion=self.canvas.bbox("all"))
        )

        self.char_vars = {}

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="保存", command=self._save_session, width=12).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_frame, text="読み込み", command=self._load_session, width=12).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_frame, text="キャラ一覧を更新", command=self._refresh_chars, width=16).pack(
            side=tk.LEFT, padx=4
        )

    def _refresh_chars(self, selected_ids: list = None):
        for widget in self.check_inner.winfo_children():
            widget.destroy()
        self.char_vars.clear()
        chars = load_json(CHARACTERS_JSON).get("characters", {})
        for char_id, char in chars.items():
            var = tk.BooleanVar(
                value=(char_id in selected_ids) if selected_ids else char.get("enabled", True)
            )
            self.char_vars[char_id] = var
            ttk.Checkbutton(
                self.check_inner,
                text=f"{char.get('name', char_id)}  [{char.get('role', '')}]",
                variable=var,
            ).pack(anchor="w", padx=8, pady=2)

    def _save_session(self):
        name = self.var_session_name.get().strip()
        if not name:
            return messagebox.showwarning("入力エラー", "セッション名を入力してください")
        selected = [cid for cid, var in self.char_vars.items() if var.get()]
        save_json(
            SESSION_JSON,
            {
                "session_name": name,
                "memo": self.text_memo.get("1.0", tk.END).strip(),
                "selected_characters": selected,
            },
        )
        messagebox.showinfo("完了", "セッション設定を保存しました")

    def _load_session(self):
        data = load_json(SESSION_JSON)
        self.var_session_name.set(data.get("session_name", ""))
        self.text_memo.delete("1.0", tk.END)
        self.text_memo.insert("1.0", data.get("memo", ""))
        self._refresh_chars(selected_ids=data.get("selected_characters", None))


# ==========================================
# タブ4：履歴・再開 (Phase C 追加部分)
# ==========================================


class HistoryTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=12)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        # --- 左: セッションフォルダ一覧 ---
        left = ttk.Frame(self)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ttk.Label(left, text="保存済みセッション一覧", font=("", 11, "bold")).pack(
            anchor="w", pady=(0, 4)
        )

        list_frame = ttk.Frame(left)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.listbox = tk.Listbox(
            list_frame, selectmode=tk.SINGLE, font=("", 11), width=36, activestyle="dotbox"
        )
        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        ttk.Button(left, text="一覧を更新", command=self.refresh, width=12).pack(pady=8, anchor="w")

        # --- 右: サマリー表示 & 復元ボタン ---
        right = ttk.Frame(self, padding=(12, 0))
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.btn_resume = ttk.Button(
            right,
            text="🔄 この状態から再開（設定を復元）",
            command=self._on_resume,
            width=35,
            state="disabled",
        )
        self.btn_resume.pack(pady=(0, 8), fill=tk.X)

        ttk.Label(right, text="あらすじ（サマリー）", font=("", 10, "bold")).pack(
            anchor="w", pady=(0, 2)
        )
        self.summary_text = tk.Text(
            right, width=40, height=20, state="disabled", font=("", 10), wrap=tk.WORD, bg="#f5f5f5"
        )
        self.summary_text.pack(fill=tk.BOTH, expand=True)

        self.selected_folder = None

    def refresh(self):
        self.listbox.delete(0, tk.END)
        self.session_folders = []
        if SESSIONS_DIR.exists():
            # フォルダを新しい順に並べる
            dirs = sorted([d for d in SESSIONS_DIR.iterdir() if d.is_dir()], reverse=True)
            for d in dirs:
                self.session_folders.append(d)
                self.listbox.insert(tk.END, f" {d.name}")
        self._show_summary(None)

    def _on_select(self, event=None):
        idx = self.listbox.curselection()
        if not idx:
            return
        self.selected_folder = self.session_folders[idx[0]]
        self._show_summary(self.selected_folder)
        self.btn_resume.config(state="normal")

    def _show_summary(self, folder_path: Path):
        self.summary_text.config(state="normal")
        self.summary_text.delete("1.0", tk.END)

        if folder_path:
            summary_file = folder_path / "summary.txt"
            log_file = folder_path / "chat_log.jsonl"

            info = f"【フォルダ】\n{folder_path.name}\n\n"

            if summary_file.exists():
                with open(summary_file, encoding="utf-8") as f:
                    info += f"【あらすじ】\n{f.read()}\n"
            else:
                info += "【あらすじ】\n(サマリーファイルは作成されていません)\n\n"

            if log_file.exists():
                # 行数を数えてログ件数を表示
                try:
                    with open(log_file, encoding="utf-8") as f:
                        lines = sum(1 for line in f if line.strip())
                    info += f"\n【ログ記録数】 {lines} 件\n"
                except Exception:
                    pass

            self.summary_text.insert("1.0", info)

        self.summary_text.config(state="disabled")

    def _on_resume(self):
        if not self.selected_folder:
            return

        backup_dir = self.selected_folder / "configs_backup"
        if not backup_dir.exists():
            messagebox.showerror("エラー", "バックアップデータが見つかりません。")
            return

        msg = (
            f"'{self.selected_folder.name}' の状態に復元しますか？\n\n"
            "※現在の 'configs' フォルダ内にあるキャラクターやプロンプト設定は上書きされます。\n"
            "（現在の設定を残したい場合は、先に手動でコピーしてください）"
        )

        if messagebox.askyesno("復元と再開の確認", msg):
            try:
                # configs_backup の中身を configs に上書きコピー
                shutil.copytree(backup_dir, CONFIGS_DIR, dirs_exist_ok=True)
                messagebox.showinfo(
                    "復元完了",
                    "設定データを復元しました。\n"
                    "他のタブを開いて設定が戻っているか確認してください。\n\n"
                    "※チャットの文脈を引き継いで再開する機能は次回のアップデートで有効になります。",
                )

                # 他のタブをリフレッシュするイベントを発行
                self.event_generate("<<ConfigsRestored>>", when="tail")
            except Exception as e:
                messagebox.showerror("エラー", f"復元中にエラーが発生しました:\n{e}")


# ==========================================
# メインウィンドウ
# ==========================================


class TacticalAIGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("タクティカル祓魔師 AI設定ツール")
        self.geometry("820x600")
        self.minsize(700, 500)
        self._apply_style()
        self._build_menu()
        self._build_tabs()
        self._build_statusbar()

        # 復元イベントを受け取ってタブを更新
        self.bind("<<ConfigsRestored>>", lambda e: self._refresh_all_tabs())

    def _apply_style(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TNotebook.Tab", font=("", 11), padding=(12, 6))

    def _build_menu(self):
        menubar = tk.Menu(self)
        f_menu = tk.Menu(menubar, tearoff=0)
        f_menu.add_command(
            label="設定フォルダを開く",
            command=lambda: subprocess.Popen(f'explorer "{CONFIGS_DIR}"'),
        )
        f_menu.add_command(
            label="セッション履歴フォルダを開く",
            command=lambda: subprocess.Popen(f'explorer "{SESSIONS_DIR}"'),
        )
        f_menu.add_separator()
        f_menu.add_command(label="終了", command=self.quit)
        menubar.add_cascade(label="ファイル", menu=f_menu)
        self.config(menu=menubar)

    def _build_tabs(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tab_char = CharacterTab(self.notebook)
        self.tab_prompt = PromptTab(self.notebook)
        self.tab_session = SessionTab(self.notebook)
        self.tab_history = HistoryTab(self.notebook)  # ★追加

        self.notebook.add(self.tab_char, text="  キャラクター管理  ")
        self.notebook.add(self.tab_prompt, text="  プロンプト管理  ")
        self.notebook.add(self.tab_session, text="  セッション設定  ")
        self.notebook.add(self.tab_history, text="  履歴・再開  ")  # ★追加

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_change)

    def _refresh_all_tabs(self):
        self.tab_char.refresh()
        self.tab_prompt.refresh()
        self.tab_session._load_session()

    def _on_tab_change(self, event):
        idx = event.widget.index(event.widget.select())
        if idx == 0:
            self.tab_char.refresh()
        elif idx == 1:
            self.tab_prompt.refresh()
        elif idx == 2:
            self.tab_session._refresh_chars()
        elif idx == 3:
            self.tab_history.refresh()

    def _build_statusbar(self):
        ttk.Label(
            self, text=f"設定ファイル: {CONFIGS_DIR}", relief=tk.SUNKEN, anchor="w", font=("", 9)
        ).pack(side=tk.BOTTOM, fill=tk.X)


# ==========================================
# エントリーポイント
# ==========================================

if __name__ == "__main__":
    app = TacticalAIGUI()
    app.mainloop()
