# ================================
# ファイル: core/launcher.py
# タクティカル祓魔師TRPG AIシステム - 統合ランチャー
# ================================

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

import requests

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
WORLD_SETTING_JSON = CONFIGS_DIR / "world_setting.json"
SESSIONS_DIR = BASE_DIR / "sessions"
SAVED_PCS_DIR = CONFIGS_DIR / "saved_pcs"
CORE_DIR = BASE_DIR / "core"

SAVED_PCS_DIR.mkdir(parents=True, exist_ok=True)
PYTHON = sys.executable

# ==========================================
# ユーティリティ
# ==========================================


def compress_tokens_safe(text):
    compressed = re.sub(r"\n+", "\n", text)
    compressed = re.sub(r"[ \t　]+", " ", compressed)
    return compressed


def parse_llm_json_robust(text: str) -> dict:
    import json
    import re

    clean_text = re.sub(r"```json\n?|```\n?", "", text).strip()
    start = clean_text.find("{")
    end = clean_text.rfind("}")

    parsed_data = {}
    if start != -1 and end != -1:
        try:
            parsed_data = json.loads(clean_text[start : end + 1])
            print("DEBUG: JSONパース成功！")
            return parsed_data
        except json.JSONDecodeError:
            pass

    print("DEBUG: JSONパース失敗。正規表現による強制抽出モードに移行します！")
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


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_template_ids() -> list:
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


# ==========================================
# タブ0: CCFolia 起動
# ==========================================


class LauncherTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=12)
        self._proc: subprocess.Popen | None = None
        self._log_thread: threading.Thread | None = None
        self._build_ui()
        self._refresh_sessions()
        self._update_lm_status()

    def _build_ui(self):
        top = ttk.LabelFrame(self, text="起動設定", padding=10)
        top.pack(fill=tk.X, pady=(0, 8))
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="LM-Studio").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.lm_status_var = tk.StringVar(value="確認中...")
        self.lm_status_label = ttk.Label(
            top, textvariable=self.lm_status_var, font=("", 10, "bold")
        )
        self.lm_status_label.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(top, text="再確認", command=self._update_lm_status, width=8).grid(
            row=0, column=2, padx=8
        )

        ttk.Label(top, text="Room URL").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.var_url = tk.StringVar()
        ttk.Entry(top, textvariable=self.var_url, width=55).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=8
        )

        ttk.Label(top, text="セッション").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        session_frame = ttk.Frame(top)
        session_frame.grid(row=2, column=1, columnspan=2, sticky="ew", padx=8)
        self.var_session = tk.StringVar(value="新規セッション")
        self.cb_session = ttk.Combobox(
            session_frame, textvariable=self.var_session, state="readonly", width=50
        )
        self.cb_session.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(session_frame, text="更新", command=self._refresh_sessions, width=6).pack(
            side=tk.LEFT, padx=(4, 0)
        )

        ttk.Label(top, text="デフォルトキャラ").grid(row=3, column=0, sticky="w", padx=8, pady=4)
        self.var_default_char = tk.StringVar(value="meta_gm")
        ttk.Entry(top, textvariable=self.var_default_char, width=24).grid(
            row=3, column=1, sticky="w", padx=8
        )

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=(0, 8))

        self.btn_start = ttk.Button(
            btn_frame, text="▶  CCFolia 起動", command=self._on_start, width=20
        )
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_stop = ttk.Button(
            btn_frame, text="■  停止", command=self._on_stop, width=12, state="disabled"
        )
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(btn_frame, text="ログをクリア", command=self._clear_log, width=12).pack(
            side=tk.RIGHT
        )

        self.status_var = tk.StringVar(value="待機中")
        ttk.Label(btn_frame, textvariable=self.status_var, foreground="gray").pack(
            side=tk.LEFT, padx=12
        )

        log_frame = ttk.LabelFrame(self, text="ログ出力", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            font=("Courier New", 9),
            state="disabled",
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white",
            wrap=tk.WORD,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_config("ok", foreground="#4ec9b0")
        self.log_text.tag_config("err", foreground="#f44747")
        self.log_text.tag_config("warn", foreground="#dcdcaa")
        self.log_text.tag_config("info", foreground="#9cdcfe")
        self.log_text.tag_config("plain", foreground="#d4d4d4")

    def _refresh_sessions(self):
        folders = get_session_folders()
        options = ["新規セッション"] + [f.name for f in folders]
        self.cb_session["values"] = options
        if self.var_session.get() not in options:
            self.var_session.set("新規セッション")

    def _update_lm_status(self):
        def check():
            ok = check_lm_studio()
            self.after(0, lambda: self._set_lm_status(ok))

        threading.Thread(target=check, daemon=True).start()

    def _set_lm_status(self, ok: bool):
        if ok:
            self.lm_status_var.set("✓ 接続中 (localhost:1234)")
            self.lm_status_label.config(foreground="green")
        else:
            self.lm_status_var.set("✗ 未接続 — LM-Studio を起動してください")
            self.lm_status_label.config(foreground="red")

    def _on_start(self):
        url = self.var_url.get().strip()
        if not url:
            messagebox.showwarning(
                "入力エラー", "Room URL を入力してください", parent=self.winfo_toplevel()
            )
            return
        if not url.startswith("http"):
            messagebox.showwarning(
                "入力エラー",
                "URL は http:// または https:// で始める必要があります",
                parent=self.winfo_toplevel(),
            )
            return

        default_char = self.var_default_char.get().strip() or "meta_gm"
        connector_path = CORE_DIR / "ccfolia_connector.py"

        cmd = [PYTHON, str(connector_path), "--room", url, "--default", default_char]

        self._log(f"起動コマンド: {' '.join(cmd)}\n", "info")

        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            # ★ ここに stdin=subprocess.PIPE を追加！
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

        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.status_var.set("監視中...")

        self._log_thread = threading.Thread(target=self._read_proc_output, daemon=True)
        self._log_thread.start()

    def _read_proc_output(self):
        if not self._proc:
            return
        for line in self._proc.stdout:
            self.after(0, lambda l=line: self._log(l))
        ret = self._proc.wait()
        self.after(0, lambda: self._on_proc_finished(ret))

    def _on_proc_finished(self, returncode: int):
        self._proc = None
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.status_var.set(f"停止済 (終了コード: {returncode})")
        self._log(f"\n--- プロセス終了 (code={returncode}) ---\n", "warn")
        self._refresh_sessions()

    def _on_stop(self):
        if self._proc and self._proc.poll() is None:
            self._log("\n停止リクエストを送信しています...\n", "warn")
            # 停止命令を投げる
            try:
                self._proc.stdin.write(json.dumps({"type": "quit"}) + "\n")
                self._proc.stdin.flush()
            except Exception:
                pass
            self._proc.terminate()
        self.btn_stop.config(state="disabled")
        self.status_var.set("停止中...")

    def _log(self, text: str, tag: str = None):
        if not tag:
            tag = "plain"
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, text, tag)
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)

    # ★ ここに追加：ココフォリアに直接テキストを流し込む関数
    def send_to_ccfolia(self, character_name: str, text: str):
        """AIが生成したテキストなどをココフォリアのチャット欄に自動送信する"""
        if self._proc and self._proc.poll() is None:
            payload = (
                json.dumps(
                    {"type": "chat", "character": character_name, "text": text}, ensure_ascii=False
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
            self._log(
                "[システム警告] CCFoliaコネクターが起動していないため送信できません。\n", "warn"
            )


# ==========================================
# タブ1: キャラクターメーカー (簡素化版)
# ==========================================
class VTTCharMakerTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=10)
        sys.path.insert(0, str(CORE_DIR))
        from lm_client import LMClient

        self.lm_client = LMClient()

        self.saved_files = []
        self._init_vars()
        self._build_ui()
        self._refresh_saved_list()

    def _init_vars(self):
        self._last_json_raw = {}
        self.var_name = tk.StringVar(value="名無し")
        self.var_alias = tk.StringVar(value="")
        # 主能力値
        self.var_body = tk.IntVar(value=3)
        self.var_soul = tk.IntVar(value=3)
        self.var_skill = tk.IntVar(value=3)
        self.var_magic = tk.IntVar(value=3)
        # 副能力値
        self.var_hp = tk.IntVar(value=10)
        self.var_sp = tk.IntVar(value=10)
        self.var_evasion = tk.IntVar(value=2)
        self.var_mobility = tk.IntVar(value=2)
        self.var_armor = tk.IntVar(value=0)
        # アイテム
        self.var_katashiro = tk.IntVar(value=1)
        self.var_haraegushi = tk.IntVar(value=0)
        self.var_shimenawa = tk.IntVar(value=0)
        self.var_juryudan = tk.IntVar(value=0)
        self.var_ireikigu = tk.IntVar(value=0)
        self.var_meifuku = tk.IntVar(value=0)
        self.var_jutsuyen = tk.IntVar(value=0)

    def _build_ui(self):
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # --- 左ペイン: AI生成 & 保存リスト ---
        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        f_ai = ttk.LabelFrame(left, text="1. AIに自動作成させる", padding=8)
        f_ai.pack(fill=tk.X, pady=(0, 10))
        self.text_input = scrolledtext.ScrolledText(f_ai, width=20, height=4, font=("", 10))
        self.text_input.pack(fill=tk.X, pady=(0, 5))
        self.text_input.insert("1.0", "例：射撃戦が得意な少女祓魔師。")
        self.btn_gen = ttk.Button(f_ai, text="✨ AIで生成", command=self._start_generate)
        self.btn_gen.pack(fill=tk.X)

        f_list = ttk.LabelFrame(left, text="保存済みキャラクター", padding=8)
        f_list.pack(fill=tk.BOTH, expand=True)
        self.listbox = tk.Listbox(f_list, font=("", 11))
        self.listbox.pack(fill=tk.BOTH, expand=True, pady=(0, 5))
        btn_frame = ttk.Frame(f_list)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="読込", command=self._load_selected).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=1
        )
        ttk.Button(btn_frame, text="削除", command=self._delete_selected).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=1
        )

        # --- 中央ペイン: ステータス・アイテム ---
        mid = ttk.Frame(paned)
        paned.add(mid, weight=2)

        f_basic = ttk.LabelFrame(mid, text="2. ステータス・アイテム調整", padding=8)
        f_basic.pack(fill=tk.BOTH, expand=True)

        def make_entry(parent, label, var, r, c, w=5):
            ttk.Label(parent, text=label).grid(row=r, column=c, sticky="w", padx=4, pady=2)
            ttk.Entry(parent, textvariable=var, width=w).grid(
                row=r, column=c + 1, sticky="w", padx=4, pady=2
            )

        make_entry(f_basic, "名前:", self.var_name, 0, 0, 15)
        make_entry(f_basic, "二つ名:", self.var_alias, 0, 2, 15)

        ttk.Separator(f_basic, orient=tk.HORIZONTAL).grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=6
        )

        make_entry(f_basic, "体力(HP):", self.var_hp, 2, 0)
        make_entry(f_basic, "霊力(SP):", self.var_sp, 2, 2)
        make_entry(f_basic, "回避D:", self.var_evasion, 3, 0)
        make_entry(f_basic, "機動力:", self.var_mobility, 3, 2)
        make_entry(f_basic, "装甲:", self.var_armor, 4, 0)

        ttk.Separator(f_basic, orient=tk.HORIZONTAL).grid(
            row=5, column=0, columnspan=4, sticky="ew", pady=6
        )

        make_entry(f_basic, "体:", self.var_body, 6, 0)
        make_entry(f_basic, "霊:", self.var_soul, 6, 2)
        make_entry(f_basic, "巧:", self.var_skill, 7, 0)
        make_entry(f_basic, "術:", self.var_magic, 7, 2)

        ttk.Separator(f_basic, orient=tk.HORIZONTAL).grid(
            row=8, column=0, columnspan=4, sticky="ew", pady=6
        )

        make_entry(f_basic, "形代:", self.var_katashiro, 9, 0)
        make_entry(f_basic, "祓串:", self.var_haraegushi, 9, 2)
        make_entry(f_basic, "注連鋼縄:", self.var_shimenawa, 10, 0)
        make_entry(f_basic, "呪瘤檀:", self.var_juryudan, 10, 2)
        make_entry(f_basic, "医霊器具:", self.var_ireikigu, 11, 0)
        make_entry(f_basic, "名伏:", self.var_meifuku, 11, 2)
        make_entry(f_basic, "術延起点:", self.var_jutsuyen, 12, 0)

        # --- 右ペイン: 設定・出力 ---
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        f_memo = ttk.LabelFrame(right, text="3. キャラ設定・メモ", padding=8)
        f_memo.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        self.text_memo = scrolledtext.ScrolledText(f_memo, font=("", 10), wrap=tk.WORD)
        self.text_memo.pack(fill=tk.BOTH, expand=True)

        f_out = ttk.LabelFrame(right, text="4. 保存と出力", padding=8)
        f_out.pack(fill=tk.X)

        self.status_var = tk.StringVar(value="ステータスを調整して出力してください")
        ttk.Label(
            f_out, textvariable=self.status_var, foreground="blue", font=("", 9, "bold")
        ).pack(pady=(0, 4))

        ttk.Button(f_out, text="💾 このキャラを保存する", command=self._save_current).pack(
            fill=tk.X, pady=2
        )
        ttk.Button(
            f_out, text="📋 ココフォリア用コマとしてコピー", command=self._copy_ccfolia
        ).pack(fill=tk.X, pady=2)

    # ---- リスト管理 ----

    def _refresh_saved_list(self):
        self.listbox.delete(0, tk.END)
        self.saved_files = []
        if SAVED_PCS_DIR.exists():
            for p in sorted(
                SAVED_PCS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True
            ):
                self.saved_files.append(p)
                self.listbox.insert(tk.END, p.stem)

    def _get_selected_file(self):
        idx = self.listbox.curselection()
        if not idx:
            return None
        return self.saved_files[idx[0]]

    # ---- 保存・読込・削除 ----

    def _save_current(self):
        name = self.var_name.get().strip()
        if not name:
            messagebox.showerror("エラー", "名前を入力してください。")
            return

        data = self._last_json_raw.copy()
        data.update(
            {
                "name": name,
                "alias": self.var_alias.get(),
                "hp": self.var_hp.get(),
                "sp": self.var_sp.get(),
                "evasion": self.var_evasion.get(),
                "mobility": self.var_mobility.get(),
                "armor": self.var_armor.get(),
                "body": self.var_body.get(),
                "soul": self.var_soul.get(),
                "skill": self.var_skill.get(),
                "magic": self.var_magic.get(),
                "memo": self.text_memo.get("1.0", tk.END).strip(),
            }
        )
        data["items"] = {
            "katashiro": self.var_katashiro.get(),
            "haraegushi": self.var_haraegushi.get(),
            "shimenawa": self.var_shimenawa.get(),
            "juryudan": self.var_juryudan.get(),
            "ireikigu": self.var_ireikigu.get(),
            "meifuku": self.var_meifuku.get(),
            "jutsuyen": self.var_jutsuyen.get(),
        }

        save_json(SAVED_PCS_DIR / f"{name}.json", data)
        self.status_var.set(f"✓ {name} を保存しました！")
        self._refresh_saved_list()

    def _load_selected(self):
        file_path = self._get_selected_file()
        if not file_path:
            return
        data = load_json(file_path)
        self._apply_json_to_ui(data)
        self.status_var.set(f"✓ {data.get('name', 'キャラ')} を読み込みました")

    def _delete_selected(self):
        file_path = self._get_selected_file()
        if not file_path:
            return
        if messagebox.askyesno("削除確認", f"{file_path.stem} を削除しますか？"):
            file_path.unlink(missing_ok=True)
            self._refresh_saved_list()

    # ---- UI反映 (新旧フォーマット両対応) ----

    def _apply_json_to_ui(self, data: dict):
        # 旧ネスト形式の検出と変換
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

        self.text_memo.delete("1.0", tk.END)
        self.text_memo.insert("1.0", data.get("memo", ""))

    def _convert_old_format(self, data: dict) -> dict:
        """旧ネスト形式(prof/main_stats/sub_stats等)をフラット形式に変換"""
        flat = {}
        prof = data.get("prof", {})
        flat["name"] = prof.get("name", "名無し")
        flat["alias"] = prof.get("alias", "")

        main_stats = data.get("main_stats", {})
        for key in ["body", "soul", "skill", "magic"]:
            vals = main_stats.get(key, {})
            flat[key] = vals.get("final", vals.get("init", 3))

        sub_stats = data.get("sub_stats", {})
        for key in ["hp", "sp", "armor", "mobility"]:
            vals = sub_stats.get(key, {})
            flat[key] = vals.get("final", vals.get("init", 0))
        flat["evasion"] = 2

        # 旧inventoryからitemsを抽出
        items = {}
        item_key_map = {
            "形代": "katashiro", "祓串": "haraegushi", "注連鋼縄": "shimenawa",
            "呪瘤檀": "juryudan", "医霊器具": "ireikigu", "名伏": "meifuku",
            "術延起点": "jutsuyen",
        }
        for inv in data.get("inventory", []):
            name = inv.get("name", "")
            if name in item_key_map:
                items[item_key_map[name]] = inv.get("count", 0)
        flat["items"] = items

        # 旧テキストフィールドをmemoに結合
        memo_parts = []
        if data.get("memo"):
            memo_parts.append(data["memo"])
        for text_key in [
            "text_history", "text_career", "text_attendance", "text_health",
            "text_seminary_report", "text_investigation",
            "text_family_comments", "text_overall_remarks",
        ]:
            if data.get(text_key):
                memo_parts.append(data[text_key])
        flat["memo"] = "\n\n".join(memo_parts)

        # skills/weaponsは旧形式にもあれば保持
        if "skills" in data:
            flat["skills"] = data["skills"]
        if "weapons" in data:
            flat["weapons"] = data["weapons"]

        return flat

    # ---- AI生成 (1段階) ----

    def _build_char_prompt(self, user_req: str) -> str:
        return f"""あなたはTRPG『タクティカル祓魔師』のプレイヤーです。
ユーザーの要望に合わせて、以下のJSONフォーマットの空欄を論理的に埋めてください。
武器や特技などはユーザーの要望に合わせて複数個作成してください。
【ルール概要】体+霊+巧+(術×2)=11、HP=体、SP=霊、機動力=ceil(max(体,巧)/2)(最低2)
【重要】必ず有効なJSON形式のみを出力し、Markdownコードブロック(```json)などは使用しないでください。

ユーザー要望: {user_req}

{{
  "name": "キャラクターの名前", "alias": "二つ名",
  "hp": 15, "sp": 15, "evasion": 2, "mobility": 3, "armor": 0,
  "body": 3, "soul": 3, "skill": 3, "magic": 3,
  "items": {{"katashiro": 1, "haraegushi": 0, "shimenawa": 0, "juryudan": 0, "ireikigu": 0, "meifuku": 0, "jutsuyen": 0}},
  "memo": "キャラクターの背景や性格",
  "skills": [
    {{"name": "戦術機動", "description": "手番開始時に使用可能。『難易度:NORMAL』で【巧】判定を行う。成功した場合、即座に回避ダイスを2つ獲得し、更にその手番中は最大で【機動力】の2倍のマスを移動できる。 ただし、手番中に行う能動的な行動の判定の難易度が1段階上昇する。【巧】判定に失敗した場合、回避ダイスの獲得と移動距離の増加は行われず、判定の難易度上昇だけを被る。"}}
  ],
  "weapons": [
    {{"name": "大型遠隔祭具", "description": "【巧】の値を参照して「遠隔攻撃」を行い、攻撃成功時、「5」点の物理ダメージを与える。"}}
  ]
}}
"""

    def _start_generate(self):
        if not self.lm_client.is_server_running():
            messagebox.showerror("エラー", "LM-Studioが起動していません。")
            return
        self.btn_gen.config(state="disabled")
        self.status_var.set("生成中...お待ちください")

        def run():
            user_req = self.text_input.get("1.0", tk.END).strip()
            sys_prompt = "あなたはデータジェネレーターです。必ず指定されたJSON形式のみを出力し、余計な会話はしないでください。"
            user_msg = self._build_char_prompt(user_req)
            result, _ = self.lm_client.generate_response(
                system_prompt=sys_prompt,
                user_message=user_msg,
                temperature=0.7,
                max_tokens=1500,
                timeout=None,
                no_think=True,
            )
            self.after(0, self._on_finish, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_finish(self, result: str):
        self.btn_gen.config(state="normal")
        if not result:
            self.status_var.set("❌ 生成失敗")
            return

        # まず標準JSONパースを試行
        clean = result.replace("```json", "").replace("```", "").strip()
        try:
            data = json.loads(clean)
            self._apply_json_to_ui(data)
            self.status_var.set("✓ 生成完了！内容を調整してください")
            return
        except json.JSONDecodeError:
            pass

        # フォールバック: ロバストパーサー
        data = parse_llm_json_robust(result)
        if data:
            self._apply_json_to_ui(data)
            self.status_var.set("✓ 生成完了（フォールバック）！内容を調整してください")
        else:
            self.status_var.set("❌ JSONパースエラー")
            print(f"パース失敗した出力: {result[:200]}")

    # ---- CCFolia出力 ----

    def _copy_ccfolia(self):
        name = self.var_name.get()
        memo_text = (
            f"【二つ名】{self.var_alias.get()}\n\n{self.text_memo.get('1.0', tk.END).strip()}"
        )

        commands = "◆能力値を使った判定◆\n"
        commands += "{体}b6=>4  //【体】判定\n"
        commands += "{霊}b6=>4  //【霊】判定\n"
        commands += "{巧}b6=>4  //【巧】判定\n"
        commands += "{術}b6=>4  //【術】判定\n\n"

        commands += "◆戦闘中用の判定◆\n"
        commands += "{巧}b6=>4  //戦術機動\n"
        commands += "({体})b6=>4  //近接攻撃\n"
        commands += "({巧})b6=>4  //遠隔攻撃\n"
        commands += "({霊})b6=>4  //霊的攻撃\n"
        commands += "({術})b6=>4  //術発動\n\n"

        commands += "2d6  //ダメージ\n"
        commands += "1d3  //霊的ダメージ\n"
        commands += "b6=>4  //回避判定\n\n"

        commands += "C({体力})  //残り体力\n"
        commands += "C({霊力})  //残り霊力\n\n"

        commands += "◆支給装備◆\n"
        commands += "【形代】：キャラクターが「死亡」した時、①【形代】を1つ消費することで「死亡」を回避する②【体力】【霊力】を半分まで回復した状態でマップ上の「リスポーン地点」にキャラクターを戻す。　また、手番中に好きなタイミングで【形代】を1つ消費することで、キャラクターは【霊力】を2点回復することができる。\n\n"
        commands += "【祓串】：1つ消費することで自身を中心とした7*7マスのどこかに配置するか、近接攻撃または遠隔攻撃に使用できる。近接攻撃に使用した場合は1d6点、遠隔攻撃に使用した場合は3点の「物理ダメージ」を与える。\n\n"
        commands += "【注連鋼縄】：3つ消費することで、【巧】の値を参照してマップ上に設置する。結界に関するルールは2-7：結界の設置についてを参照。\n\n"
        commands += "【呪瘤檀】：攻撃の代わりにこのアイテムを使用する。自分を中心とした5＊5マスのいずれかのマス1つを「中心」に定め、「中心」と隣接する3＊3のマスにいるキャラクター全員に2点の霊的ダメージを与える（回避は『難易度：NORMAL』）。\n\n"

        commands += "◆特技◆\n"
        for skill in self._last_json_raw.get("skills", []):
            commands += f"【{skill.get('name', '')}】：{skill.get('description', '')}\n\n"

        commands += "◆攻撃祭具◆\n"
        for weapon in self._last_json_raw.get("weapons", []):
            commands += f"【{weapon.get('name', '')}】：{weapon.get('description', '')}\n\n"

        commands += "[Credit: 非公式タクティカル祓魔師キャラクターシートVer0.8 著作者様]"

        ccfolia_data = {
            "kind": "character",
            "data": {
                "name": name,
                "initiative": 0,
                "memo": memo_text,
                "commands": commands,
                "status": [
                    {"label": "体力", "value": self.var_hp.get(), "max": self.var_hp.get()},
                    {"label": "霊力", "value": self.var_sp.get(), "max": self.var_sp.get()},
                    {
                        "label": "回避D",
                        "value": self.var_evasion.get(),
                        "max": self.var_evasion.get(),
                    },
                    {
                        "label": "形代",
                        "value": self.var_katashiro.get(),
                        "max": self.var_katashiro.get(),
                    },
                    {
                        "label": "祓串",
                        "value": self.var_haraegushi.get(),
                        "max": self.var_haraegushi.get(),
                    },
                    {
                        "label": "注連鋼縄",
                        "value": self.var_shimenawa.get(),
                        "max": self.var_shimenawa.get(),
                    },
                    {
                        "label": "呪瘤檀",
                        "value": self.var_juryudan.get(),
                        "max": self.var_juryudan.get(),
                    },
                    {
                        "label": "医霊器具",
                        "value": self.var_ireikigu.get(),
                        "max": self.var_ireikigu.get(),
                    },
                    {
                        "label": "名伏",
                        "value": self.var_meifuku.get(),
                        "max": self.var_meifuku.get(),
                    },
                    {
                        "label": "術延起点",
                        "value": self.var_jutsuyen.get(),
                        "max": self.var_jutsuyen.get(),
                    },
                ],
                "params": [
                    {"label": "体", "value": str(self.var_body.get())},
                    {"label": "霊", "value": str(self.var_soul.get())},
                    {"label": "巧", "value": str(self.var_skill.get())},
                    {"label": "術", "value": str(self.var_magic.get())},
                    {"label": "機動力", "value": str(self.var_mobility.get())},
                    {"label": "装甲", "value": str(self.var_armor.get())},
                ],
            },
        }

        self.clipboard_clear()
        self.clipboard_append(json.dumps(ccfolia_data, ensure_ascii=False))
        self.update()

        self.status_var.set("✓ ココフォリア用にコピー！Ctrl+Vで貼り付け")
        messagebox.showinfo(
            "コピー完了",
            "ココフォリア用のクリップボードデータをコピーしました！\n\nココフォリアの画面を開いて Ctrl+V (貼り付け) を押すだけで、見やすいチャットパレット付きの駒が生成されます。",
        )


# ==========================================
# タブ2〜6: キャラクター管理、プロンプト、設定等 (省略なし)
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
        ttk.Label(frame, text="反応キーワード（カンマ区切り）").grid(
            row=5, column=0, sticky="w", **pad
        )
        self.var_keywords = tk.StringVar()
        ttk.Entry(frame, textvariable=self.var_keywords, width=35).grid(
            row=5, column=1, sticky="w", **pad
        )
        ttk.Label(frame, text="説明").grid(row=6, column=0, sticky="nw", **pad)
        self.text_desc = tk.Text(frame, width=35, height=3, font=("", 10))
        self.text_desc.grid(row=6, column=1, sticky="w", **pad)
        self.var_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="有効", variable=self.var_enabled).grid(
            row=7, column=0, sticky="w", **pad
        )
        self.var_is_ai = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="AI制御", variable=self.var_is_ai).grid(
            row=7, column=1, sticky="w", **pad
        )
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=8, column=0, columnspan=2, pady=14)
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
            "id": char_id,
            "name": name,
            "layer": self.var_layer.get(),
            "role": self.var_role.get(),
            "keywords": [k.strip() for k in kw_str.split(",")] if kw_str else [],
            "description": self.text_desc.get("1.0", tk.END).strip(),
            "enabled": self.var_enabled.get(),
            "is_ai": self.var_is_ai.get(),
            "prompt_id": self.var_prompt.get(),
        }
        self.destroy()


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
        self.characters = load_json(CHARACTERS_JSON).get("characters", {})
        self.listbox.delete(0, tk.END)
        for char_id, char in self.characters.items():
            mark = "✓" if char.get("enabled") else "✗"
            self.listbox.insert(tk.END, f" {mark}  {char.get('name', char_id)}")
        self._show_detail(None)

    def _on_select(self, _=None):
        idx = self.listbox.curselection()
        if not idx:
            return
        self._show_detail(list(self.characters.values())[idx[0]])

    def _show_detail(self, char):
        self.detail_text.config(state="normal")
        self.detail_text.delete("1.0", tk.END)
        if char:
            lines = [
                f"ID: {char.get('id', '')}",
                f"名前: {char.get('name', '')}",
                f"役割: {char.get('role', '')}",
                f"プロンプト: {char.get('prompt_id', '')}",
                f"キーワード: {', '.join(char.get('keywords', []))}",
                f"AI制御: {'はい' if char.get('is_ai') else 'いいえ'}",
                f"\n説明:\n{char.get('description', '')}",
            ]
            self.detail_text.insert("1.0", "\n".join(lines))
        self.detail_text.config(state="disabled")

    def _on_add(self):
        dlg = CharacterDialog(self.winfo_toplevel(), existing_ids=list(self.characters.keys()))
        self.wait_window(dlg)
        if dlg.result:
            self.characters[dlg.result["id"]] = dlg.result
            save_json(CHARACTERS_JSON, {"characters": self.characters})
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
            self.characters[char_id] = dlg.result
            save_json(CHARACTERS_JSON, {"characters": self.characters})
            self.refresh()

    def _on_delete(self):
        idx = self.listbox.curselection()
        if not idx:
            return
        char_id = list(self.characters.keys())[idx[0]]
        if messagebox.askyesno("確認", f"'{char_id}' を削除しますか？"):
            del self.characters[char_id]
            save_json(CHARACTERS_JSON, {"characters": self.characters})
            self.refresh()


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
            param_frame, textvariable=self.var_tokens, from_=50, to=8000, increment=10, width=8
        ).grid(row=0, column=3, sticky="w", padx=8)
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
        self._show_preview(None, None)

    def _on_select(self, _=None):
        idx = self.listbox.curselection()
        if not idx:
            return
        tmpl_id = list(self.templates.keys())[idx[0]]
        self._show_preview(tmpl_id, self.templates[tmpl_id])

    def _show_preview(self, tmpl_id, tmpl):
        self.preview_text.config(state="normal")
        self.preview_text.delete("1.0", tk.END)
        if tmpl and tmpl_id:
            lines = [
                f"ID: {tmpl_id}",
                f"Temp: {tmpl.get('temperature', '')}",
                f"Tokens: {tmpl.get('max_tokens', '')}",
                f"\n[System]\n{tmpl.get('system', '')}",
                f"\n[Instructions]\n{tmpl.get('instructions', '')}",
            ]
            self.preview_text.insert("1.0", "\n".join(lines))
        self.preview_text.config(state="disabled")

    def _on_add(self):
        dlg = PromptDialog(self.winfo_toplevel(), existing_ids=list(self.templates.keys()))
        self.wait_window(dlg)
        if dlg.result:
            new_id = dlg.result.pop("id")
            self.templates[new_id] = dlg.result
            save_json(PROMPTS_JSON, {"templates": self.templates})
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
            dlg.result.pop("id", None)
            self.templates[tmpl_id] = dlg.result
            save_json(PROMPTS_JSON, {"templates": self.templates})
            self.refresh()

    def _on_delete(self):
        idx = self.listbox.curselection()
        if not idx:
            return
        tmpl_id = list(self.templates.keys())[idx[0]]
        if messagebox.askyesno("確認", f"'{tmpl_id}' を削除しますか？"):
            del self.templates[tmpl_id]
            save_json(PROMPTS_JSON, {"templates": self.templates})
            self.refresh()


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
        for w in self.check_inner.winfo_children():
            w.destroy()
        self.char_vars.clear()
        for char_id, char in load_json(CHARACTERS_JSON).get("characters", {}).items():
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


class HistoryTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=12)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
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
        self.session_folders = get_session_folders()
        for d in self.session_folders:
            self.listbox.insert(tk.END, f" {d.name}")
        self._show_summary(None)

    def _on_select(self, _=None):
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
            info = f"【フォルダ】\n{folder_path.name}\n\n"
            summary_file = folder_path / "summary.txt"
            log_file = folder_path / "chat_log.jsonl"
            if summary_file.exists():
                with open(summary_file, encoding="utf-8") as f:
                    info += f"【あらすじ】\n{f.read()}\n"
            else:
                info += "【あらすじ】\n(サマリーは作成されていません)\n\n"
            if log_file.exists():
                try:
                    with open(log_file, encoding="utf-8") as f:
                        lines = sum(1 for l in f if l.strip())
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
            return messagebox.showerror("エラー", "バックアップが見つかりません。")
        msg = f"'{self.selected_folder.name}' の状態に復元しますか？\n※現在の設定は上書きされます。"
        if messagebox.askyesno("復元と再開", msg):
            try:
                shutil.copytree(backup_dir, CONFIGS_DIR, dirs_exist_ok=True)
                messagebox.showinfo("復元完了", "設定データを復元しました。")
                self.event_generate("<<ConfigsRestored>>", when="tail")
            except Exception as e:
                messagebox.showerror("エラー", f"復元エラー:\n{e}")


class WorldSettingTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=12)
        self._build_ui()
        self.load()

    def _build_ui(self):
        self.inner_notebook = ttk.Notebook(self)
        self.inner_notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        tab_basic = ttk.Frame(self.inner_notebook, padding=8)
        self.inner_notebook.add(tab_basic, text=" 基本設定 ")
        self.texts = {}
        fields = [
            ("world_lore", "世界観・基本設定", 8),
            ("session_scenario", "シナリオ概要・あらすじ", 6),
            ("pc_skills", "PCスキル・現在のステータス", 8),
            ("gm_instructions", "GMへの追加指示", 4),
        ]
        for i, (key, label, height) in enumerate(fields):
            ttk.Label(tab_basic, text=label, font=("", 10, "bold")).pack(
                anchor="w", pady=(4 if i else 0, 2)
            )
            st = scrolledtext.ScrolledText(tab_basic, height=height, font=("", 10), wrap=tk.WORD)
            st.pack(fill=tk.BOTH, expand=True)
            self.texts[key] = st

        def create_rule_tab(title, var_name, txt_name):
            frame = ttk.Frame(self.inner_notebook, padding=8)
            self.inner_notebook.add(frame, text=f" {title} ")
            var = tk.BooleanVar(value=False)
            setattr(self, var_name, var)
            ttk.Checkbutton(frame, text="✅ このデータをAIの記憶に読み込ませる", variable=var).pack(
                anchor="w", pady=(0, 6)
            )
            txt = scrolledtext.ScrolledText(frame, font=("", 10), wrap=tk.WORD)
            txt.pack(fill=tk.BOTH, expand=True)
            setattr(self, txt_name, txt)

        create_rule_tab("シナリオ進行", "var_scenario_en", "txt_scenario")
        create_rule_tab("追加ルール", "var_additional_en", "txt_additional")
        create_rule_tab("コアルール", "var_core_en", "txt_core")
        create_rule_tab("キャラ作成", "var_char_en", "txt_char")
        create_rule_tab("成長ルール", "var_growth_en", "txt_growth")
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="保存", command=self.save, width=12).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="再読込", command=self.load, width=12).pack(side=tk.LEFT, padx=4)
        self.status_var = tk.StringVar(value="")
        ttk.Label(btn_frame, textvariable=self.status_var, foreground="gray").pack(
            side=tk.LEFT, padx=8
        )

    def load(self):
        data = load_json(WORLD_SETTING_JSON)
        for key, st in self.texts.items():
            st.delete("1.0", tk.END)
            st.insert("1.0", data.get(key, ""))
        self.var_scenario_en.set(data.get("scenario_data_enabled", True))
        self.txt_scenario.delete("1.0", tk.END)
        self.txt_scenario.insert("1.0", data.get("scenario_data", ""))
        self.var_additional_en.set(data.get("additional_rules_enabled", False))
        self.txt_additional.delete("1.0", tk.END)
        self.txt_additional.insert("1.0", data.get("additional_rules", ""))
        self.var_core_en.set(data.get("core_rules_enabled", True))
        self.txt_core.delete("1.0", tk.END)
        self.txt_core.insert("1.0", data.get("core_rules", ""))
        self.var_char_en.set(data.get("char_creation_enabled", False))
        self.txt_char.delete("1.0", tk.END)
        self.txt_char.insert("1.0", data.get("char_creation", ""))
        self.var_growth_en.set(data.get("growth_rules_enabled", False))
        self.txt_growth.delete("1.0", tk.END)
        self.txt_growth.insert("1.0", data.get("growth_rules", ""))
        self.status_var.set("読み込み完了")

    def save(self):
        data = load_json(WORLD_SETTING_JSON)
        for key, st in self.texts.items():
            data[key] = st.get("1.0", tk.END).strip()
        data["scenario_data_enabled"] = self.var_scenario_en.get()
        data["scenario_data"] = self.txt_scenario.get("1.0", tk.END).strip()
        data["additional_rules_enabled"] = self.var_additional_en.get()
        data["additional_rules"] = self.txt_additional.get("1.0", tk.END).strip()
        data["core_rules_enabled"] = self.var_core_en.get()
        data["core_rules"] = self.txt_core.get("1.0", tk.END).strip()
        data["char_creation_enabled"] = self.var_char_en.get()
        data["char_creation"] = self.txt_char.get("1.0", tk.END).strip()
        data["growth_rules_enabled"] = self.var_growth_en.get()
        data["growth_rules"] = self.txt_growth.get("1.0", tk.END).strip()
        save_json(WORLD_SETTING_JSON, data)
        self.status_var.set("保存しました")
        self.after(3000, lambda: self.status_var.set(""))


class GeneratorTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=12)
        sys.path.insert(0, str(CORE_DIR))
        from lm_client import LMClient

        self.lm_client = LMClient()
        self._build_ui()

    def _build_ui(self):
        left = ttk.Frame(self)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 10))
        ttk.Label(left, text="作成対象", font=("", 10, "bold")).pack(anchor="w", pady=(0, 2))
        self.var_target = tk.StringVar(value="エネミー（敵）作成")
        targets = [
            "エネミー（敵）作成",
            "シナリオ概要・イベント作成",
            "アイテム・祭具作成",
            "その他（カスタム）",
        ]
        ttk.Combobox(
            left, textvariable=self.var_target, values=targets, state="readonly", width=32
        ).pack(anchor="w", pady=(0, 10))
        ttk.Label(left, text="追加要望・テーマなど", font=("", 10, "bold")).pack(
            anchor="w", pady=(0, 2)
        )
        self.text_input = scrolledtext.ScrolledText(left, width=35, height=10, font=("", 10))
        self.text_input.pack(anchor="w", pady=(0, 10))
        self.btn_gen = ttk.Button(left, text="✨ 生成開始", command=self._start_generate, width=30)
        self.btn_gen.pack(anchor="w", pady=(0, 2))
        self.status_var = tk.StringVar(value="待機中...")
        ttk.Label(left, textvariable=self.status_var, foreground="gray").pack(
            anchor="w", pady=(0, 10)
        )
        out_frame = ttk.LabelFrame(left, text="出力・コピー", padding=6)
        out_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(out_frame, text="📋 全文コピー", command=self._copy_all, width=18).pack(
            fill=tk.X, pady=2
        )
        right = ttk.Frame(self)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(right, text="生成結果", font=("", 10, "bold")).pack(anchor="w", pady=(0, 2))
        self.text_output = scrolledtext.ScrolledText(
            right, font=("", 10), wrap=tk.WORD, bg="#f5f5f5"
        )
        self.text_output.pack(fill=tk.BOTH, expand=True)

    def _start_generate(self):
        if not self.lm_client.is_server_running():
            import tkinter.messagebox as messagebox

            messagebox.showerror("エラー", "LM-Studioが起動していません。")
            return
        self.btn_gen.config(state="disabled")
        self.status_var.set("生成中 (ルールと世界観を統合して構築中...)")
        self.update()

        def run():
            user_req = self.text_input.get("1.0", "end").strip()
            try:
                with open("configs/world_setting_compressed.txt", encoding="utf-8") as f:
                    compressed_data = f.read()
            except FileNotFoundError:
                compressed_data = "※エラー: configs/world_setting_compressed.txt が見つかりません。"
            sys_prompt = (
                "あなたはTRPG『タクティカル祓魔師』の厳格なシステム管理者であり、熟練GMです。\n"
                f"【公式ルール・世界観データ】\n{compressed_data}\n\n"
                "【絶対厳守事項】\n"
                "1. オリジナルスキルの捏造は絶対に許されません。必ずデータ内に存在する特技や術（ARTS）のみを使用してください。\n"
                "2. 初期能力値やHP等のパラメータは、チートにならない範囲でルールに則り決定してください。\n"
                "3. 世界観データを踏まえ、キャラクターの背景設定(lore)も作成してください。\n"
                "4. AIの内部での推論・計算プロセスは極力短く終わらせ、直ちに以下のJSON形式で出力を開始してください。\n"
                "5. 絶対に `{` から出力を開始し、解説の文章は一切出力しないでください。\n"
                "{\n"
                '  "name": "(名前)",\n'
                '  "department": "(境界対策課などデータに存在する所属)",\n'
                '  "body": 3, "soul": 3, "skill": 3, "magic": 3,\n'
                '  "hp": 10, "sp": 10, "armor": 0, "mobility": 4,\n'
                '  "weapon": "(データ内の武器名)",\n'
                '  "cloak": "(データ内の狩衣・防具名)",\n'
                '  "skills": [{"name": "(データ内のスキル/術名)", "cost": "(コスト)", "condition": "(条件)", "effect": "(効果)"}],\n'
                '  "text_history": "(世界観に沿った過去の経歴やエピソード)",\n'
                '  "text_career": "(現在の役職や任務)",\n'
                '  "text_overall_remarks": "(GMからの所見、トラウマや影などのフレーバー)"\n'
                "}"
            )
            user_msg = f"以下の要望に合うキャラクターデータを生成してください。\n要望: {user_req}"
            try:
                result_content, _ = self.lm_client.generate_response(
                    system_prompt=sys_prompt,
                    user_message=user_msg,
                    temperature=0.4,
                    max_tokens=8192,
                    timeout=None,
                )
                self.after(0, self._on_finish, result_content)
            except Exception as e:
                self.after(0, lambda err=e: self.status_var.set(f"❌ 内部エラー: {err}"))
                self.after(0, lambda: self.btn_gen.config(state="normal"))

        import threading

        threading.Thread(target=run, daemon=True).start()

    def _on_finish(self, result_content: str):
        self.btn_gen.config(state="normal")
        self.text_output.delete("1.0", tk.END)
        self.text_output.insert("1.0", result_content)
        self.status_var.set("✓ AI生成完了！")

    def _copy_all(self):
        text = self.text_output.get("1.0", tk.END).strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status_var.set("✓ 全文をコピーしました")


# ==========================================
# メインウィンドウ
# ==========================================


class TacticalAILauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("タクティカル祓魔師 AI — 統合ランチャー")
        self.geometry("1100x750")
        self.minsize(850, 600)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._apply_style()
        self._build_menu()
        self._build_tabs()
        self._build_statusbar()
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
        f_menu.add_command(label="終了", command=self._on_close)
        menubar.add_cascade(label="ファイル", menu=f_menu)
        self.config(menu=menubar)

    def _build_tabs(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tab_launch = LauncherTab(self.notebook)
        self.tab_maker = VTTCharMakerTab(self.notebook)
        self.tab_char = CharacterTab(self.notebook)
        self.tab_prompt = PromptTab(self.notebook)
        self.tab_session = SessionTab(self.notebook)
        self.tab_history = HistoryTab(self.notebook)
        self.tab_world = WorldSettingTab(self.notebook)
        self.tab_generator = GeneratorTab(self.notebook)

        self.notebook.add(self.tab_launch, text=" ▶ CCFolia起動 ")
        self.notebook.add(self.tab_maker, text=" 🎲 キャラクターメーカー ")
        self.notebook.add(self.tab_char, text=" 👥 キャラ管理 ")
        self.notebook.add(self.tab_prompt, text=" 📝 プロンプト ")
        self.notebook.add(self.tab_session, text=" ⚙️ セッション ")
        self.notebook.add(self.tab_history, text=" 🕒 履歴 ")
        self.notebook.add(self.tab_world, text=" 🌍 世界観 ")
        self.notebook.add(self.tab_generator, text=" 🛠️ 汎用ジェネレーター ")

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_change)

    def _on_tab_change(self, event):
        try:
            idx = event.widget.index(event.widget.select())
            if idx == 0:
                self.tab_launch._refresh_sessions()
                self.tab_launch._update_lm_status()
            elif idx == 2:
                self.tab_char.refresh()
            elif idx == 3:
                self.tab_prompt.refresh()
            elif idx == 4:
                self.tab_session._load_session()
            elif idx == 5:
                self.tab_history.refresh()
            elif idx == 6:
                self.tab_world.load()
        except Exception:
            pass

    def _refresh_all_tabs(self):
        self.tab_char.refresh()
        self.tab_prompt.refresh()
        self.tab_session._load_session()
        self.tab_launch._refresh_sessions()
        self.tab_world.load()

    def _build_statusbar(self):
        ttk.Label(
            self, text=f"設定ファイル: {CONFIGS_DIR}", relief=tk.SUNKEN, anchor="w", font=("", 9)
        ).pack(side=tk.BOTTOM, fill=tk.X)

    def _on_close(self):
        proc = getattr(self.tab_launch, "_proc", None)
        if proc and proc.poll() is None:
            if not messagebox.askyesno(
                "終了確認", "CCFoliaコネクターが動作中です。\n終了しますか？"
            ):
                return
            proc.terminate()
        self.destroy()


if __name__ == "__main__":
    app = TacticalAILauncher()
    app.mainloop()
