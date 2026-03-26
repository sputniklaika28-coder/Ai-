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
from tkinter import messagebox, scrolledtext, simpledialog, ttk

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
# タブ1: キャラクターメーカー
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
        self.vars_prof = {
            "name": tk.StringVar(),
            "pl_name": tk.StringVar(),
            "gender": tk.StringVar(),
            "alias": tk.StringVar(),
            "race": tk.StringVar(),
            "affiliation": tk.StringVar(),
            "department": tk.StringVar(),
            "rank": tk.StringVar(),
            "origin": tk.StringVar(),
            "age": tk.StringVar(),
            "title": tk.StringVar(),
            "attack_style": tk.StringVar(),
            "education": tk.StringVar(),
        }
        self.vars_main_stats = {}
        for stat in ["body", "soul", "skill", "magic"]:
            self.vars_main_stats[stat] = {
                "init": tk.IntVar(value=3),
                "mod": tk.IntVar(value=0),
                "skill": tk.IntVar(value=0),
                "growth": tk.IntVar(value=0),
                "final": tk.IntVar(value=3),
            }
            for k in ["init", "mod", "skill", "growth"]:
                self.vars_main_stats[stat][k].trace_add(
                    "write", lambda *args, s=stat: self._calc_main_stat(s)
                )
        self.vars_sub_stats = {}
        for stat in ["hp", "sp", "armor", "mobility"]:
            self.vars_sub_stats[stat] = {
                "init": tk.IntVar(value=10 if stat in ["hp", "sp"] else 0),
                "cloak": tk.IntVar(value=0),
                "skill": tk.IntVar(value=0),
                "mod": tk.IntVar(value=0),
                "final": tk.IntVar(value=10 if stat in ["hp", "sp"] else 0),
            }
            for k in ["init", "cloak", "skill", "mod"]:
                self.vars_sub_stats[stat][k].trace_add(
                    "write", lambda *args, s=stat: self._calc_sub_stat(s)
                )

        self.vars_equip = {
            "cloak_name": tk.StringVar(value="標準狩衣"),
            "weapon_name": tk.StringVar(value="支給祭具"),
            "weapon2_name": tk.StringVar(value=""),
        }
        self.vars_combat_mods = {
            "melee": tk.IntVar(value=0),
            "ranged": tk.IntVar(value=0),
            "anti_body": tk.IntVar(value=0),
            "anti_skill": tk.IntVar(value=0),
            "anti_soul": tk.IntVar(value=0),
            "anti_magic": tk.IntVar(value=0),
        }

        self.vars_skills = []
        for _ in range(8):
            self.vars_skills.append(
                {
                    "name": tk.StringVar(),
                    "cost": tk.StringVar(),
                    "condition": tk.StringVar(),
                    "effect": tk.StringVar(),
                }
            )

        self.vars_inventory = []
        for i in range(13):
            def_name = ""
            if i == 0:
                def_name = "形代"
            elif i == 1:
                def_name = "祓串"
            elif i == 2:
                def_name = "注連鋼縄"
            self.vars_inventory.append(
                {
                    "name": tk.StringVar(value=def_name),
                    "type": tk.StringVar(value="支給" if i < 3 else ""),
                    "count": tk.IntVar(value=7 if i == 0 or i == 1 else (21 if i == 2 else 0)),
                }
            )

        self.vars_accessories = []
        for _ in range(8):
            self.vars_accessories.append({"name": tk.StringVar(), "memo": tk.StringVar()})

        self.vars_lore = {
            "real_name": tk.StringVar(value="対人呪殺を防ぐため非公開"),
            "birthday": tk.StringVar(),
            "height": tk.StringVar(),
            "weight": tk.StringVar(),
            "blood_type": tk.StringVar(),
            "religion": tk.StringVar(),
            "service_years": tk.StringVar(),
            "innate_type": tk.StringVar(value="生得(ナチュラル)祓魔師"),
            "apt_weapon": tk.StringVar(value="なし"),
            "apt_test": tk.StringVar(value="前線部隊"),
            "apt_support": tk.StringVar(value="なし"),
            "eval_body": tk.StringVar(value="C"),
            "eval_soul": tk.StringVar(value="C"),
            "eval_output": tk.StringVar(value="C"),
            "eval_resist": tk.StringVar(value="C"),
            "eval_tool": tk.StringVar(value="C"),
            "curse_coeff": tk.StringVar(value="任務に支障なし"),
            "impairment": tk.StringVar(value="障骸なし"),
            "examiner_name": tk.StringVar(value="黒服の神祇官"),
        }
        self.var_use_extra_rules = tk.BooleanVar(value=True)

    def _calc_main_stat(self, stat):
        try:
            total = sum(
                self.vars_main_stats[stat][k].get() for k in ["init", "mod", "skill", "growth"]
            )
            self.vars_main_stats[stat]["final"].set(total)
        except tk.TclError:
            pass

    def _calc_sub_stat(self, stat):
        try:
            total = sum(
                self.vars_sub_stats[stat][k].get() for k in ["init", "cloak", "skill", "mod"]
            )
            self.vars_sub_stats[stat]["final"].set(total)
        except tk.TclError:
            pass

    def _build_ui(self):
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned, padding=(0, 0, 10, 0))
        paned.add(left, weight=1)

        f_ai = ttk.LabelFrame(left, text="1. AI自動作成", padding=8)
        f_ai.pack(fill=tk.X, pady=(0, 10))
        self.text_input = scrolledtext.ScrolledText(f_ai, height=4, font=("", 10))
        self.text_input.pack(fill=tk.X, pady=(0, 5))
        self.text_input.insert("1.0", "例：近接特化のベテラン。過去に重傷を負い、少し影がある。")

        ttk.Checkbutton(
            f_ai, text="拡張データ(追加ルール等)を適用する", variable=self.var_use_extra_rules
        ).pack(anchor="w", pady=(0, 5))

        self.btn_gen = ttk.Button(f_ai, text="✨ シート構成でAI生成", command=self._start_generate)
        self.btn_gen.pack(fill=tk.X)
        self.status_var = tk.StringVar(value="待機中")
        ttk.Label(f_ai, textvariable=self.status_var, foreground="gray").pack(pady=2)

        f_list = ttk.LabelFrame(left, text="保存済みPC", padding=8)
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

        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=3)

        self.sheet_notebook = ttk.Notebook(right_frame)
        self.sheet_notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_page1 = ttk.Frame(self.sheet_notebook, padding=5)
        self.tab_page2 = ttk.Frame(self.sheet_notebook, padding=5)
        self.tab_page3 = ttk.Frame(self.sheet_notebook, padding=5)
        self.tab_page4 = ttk.Frame(self.sheet_notebook, padding=5)
        self.tab_output = ttk.Frame(self.sheet_notebook, padding=5)

        self.sheet_notebook.add(self.tab_page1, text=" Page 1: プロフ・能力 ")
        self.sheet_notebook.add(self.tab_page2, text=" Page 2: 特技・スキル ")
        self.sheet_notebook.add(self.tab_page3, text=" Page 3: 所持品・備品 ")
        self.sheet_notebook.add(self.tab_page4, text=" Page 4: 設定欄 (全網羅) ")
        self.sheet_notebook.add(self.tab_output, text=" 💾 保存・出力 ")

        self._build_page1(self.tab_page1)
        self._build_page2(self.tab_page2)
        self._build_page3(self.tab_page3)
        self._build_page4(self.tab_page4)
        self._build_output_page(self.tab_output)

    def _build_page1(self, parent):
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        f_prof = ttk.LabelFrame(inner, text="プロフィール", padding=8)
        f_prof.pack(fill=tk.X, pady=5, padx=5)
        prof_layout = [
            [("通名", "name"), ("PL名", "pl_name"), ("性別", "gender"), ("年齢", "age")],
            [
                ("二つ名", "alias"),
                ("種族", "race"),
                ("所属", "affiliation"),
                ("部門", "department"),
            ],
            [
                ("階級", "rank"),
                ("出身", "origin"),
                ("役職/学名", "title"),
                ("攻撃スタイル", "attack_style"),
            ],
            [("最終学歴", "education")],
        ]
        for r_idx, row in enumerate(prof_layout):
            for c_idx, (label, key) in enumerate(row):
                ttk.Label(f_prof, text=label).grid(
                    row=r_idx, column=c_idx * 2, sticky="e", padx=2, pady=2
                )
                ttk.Entry(f_prof, textvariable=self.vars_prof[key], width=12).grid(
                    row=r_idx, column=c_idx * 2 + 1, sticky="w", padx=2, pady=2
                )

        f_stat = ttk.LabelFrame(inner, text="能力値・副能力値", padding=8)
        f_stat.pack(fill=tk.X, pady=5, padx=5)

        headers = ["初期値", "補正値", "特技", "成長", "最終値"]
        for i, h in enumerate(headers):
            ttk.Label(f_stat, text=h, font=("", 8, "bold")).grid(row=0, column=i + 1, padx=4)

        stats_map = [
            ("【体】", "body"),
            ("【霊】", "soul"),
            ("【巧】", "skill"),
            ("【術】", "magic"),
        ]
        for r, (label, key) in enumerate(stats_map, 1):
            ttk.Label(f_stat, text=label).grid(row=r, column=0, sticky="e")
            ttk.Entry(f_stat, textvariable=self.vars_main_stats[key]["init"], width=5).grid(
                row=r, column=1
            )
            ttk.Entry(f_stat, textvariable=self.vars_main_stats[key]["mod"], width=5).grid(
                row=r, column=2
            )
            ttk.Entry(f_stat, textvariable=self.vars_main_stats[key]["skill"], width=5).grid(
                row=r, column=3
            )
            ttk.Entry(f_stat, textvariable=self.vars_main_stats[key]["growth"], width=5).grid(
                row=r, column=4
            )
            ttk.Entry(
                f_stat, textvariable=self.vars_main_stats[key]["final"], width=5, state="readonly"
            ).grid(row=r, column=5)

        ttk.Separator(f_stat, orient=tk.HORIZONTAL).grid(
            row=5, column=0, columnspan=6, sticky="ew", pady=5
        )

        sub_headers = ["初期値", "狩衣", "特技", "補正値", "最終値"]
        for i, h in enumerate(sub_headers):
            ttk.Label(f_stat, text=h, font=("", 8, "bold")).grid(row=6, column=i + 1, padx=4)

        sub_stats_map = [
            ("【体力】", "hp"),
            ("【霊力】", "sp"),
            ("【装甲】", "armor"),
            ("【機動力】", "mobility"),
        ]
        for r, (label, key) in enumerate(sub_stats_map, 7):
            ttk.Label(f_stat, text=label).grid(row=r, column=0, sticky="e")
            ttk.Entry(f_stat, textvariable=self.vars_sub_stats[key]["init"], width=5).grid(
                row=r, column=1
            )
            ttk.Entry(f_stat, textvariable=self.vars_sub_stats[key]["cloak"], width=5).grid(
                row=r, column=2
            )
            ttk.Entry(f_stat, textvariable=self.vars_sub_stats[key]["skill"], width=5).grid(
                row=r, column=3
            )
            ttk.Entry(f_stat, textvariable=self.vars_sub_stats[key]["mod"], width=5).grid(
                row=r, column=4
            )
            ttk.Entry(
                f_stat, textvariable=self.vars_sub_stats[key]["final"], width=5, state="readonly"
            ).grid(row=r, column=5)

        f_combat = ttk.LabelFrame(inner, text="判定補正・装備", padding=8)
        f_combat.pack(fill=tk.X, pady=5, padx=5)
        ttk.Label(f_combat, text="狩衣(防具名):").grid(row=0, column=0, sticky="e")
        ttk.Entry(f_combat, textvariable=self.vars_equip["cloak_name"], width=15).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(f_combat, text="攻性祭具(主):").grid(row=0, column=2, sticky="e")
        ttk.Entry(f_combat, textvariable=self.vars_equip["weapon_name"], width=15).grid(
            row=0, column=3, sticky="w"
        )
        ttk.Label(f_combat, text="攻性祭具(副):").grid(row=0, column=4, sticky="e")
        ttk.Entry(f_combat, textvariable=self.vars_equip["weapon2_name"], width=15).grid(
            row=0, column=5, sticky="w"
        )
        ttk.Separator(f_combat, orient=tk.HORIZONTAL).grid(
            row=1, column=0, columnspan=6, sticky="ew", pady=5
        )
        ttk.Label(f_combat, text="近接判定:").grid(row=2, column=0, sticky="e")
        ttk.Entry(f_combat, textvariable=self.vars_combat_mods["melee"], width=5).grid(
            row=2, column=1, sticky="w"
        )
        ttk.Label(f_combat, text="遠隔判定:").grid(row=2, column=2, sticky="e")
        ttk.Entry(f_combat, textvariable=self.vars_combat_mods["ranged"], width=5).grid(
            row=2, column=3, sticky="w"
        )
        ttk.Label(f_combat, text="対体補正:").grid(row=3, column=0, sticky="e")
        ttk.Entry(f_combat, textvariable=self.vars_combat_mods["anti_body"], width=5).grid(
            row=3, column=1, sticky="w"
        )
        ttk.Label(f_combat, text="対巧補正:").grid(row=3, column=2, sticky="e")
        ttk.Entry(f_combat, textvariable=self.vars_combat_mods["anti_skill"], width=5).grid(
            row=3, column=3, sticky="w"
        )
        ttk.Label(f_combat, text="対霊補正:").grid(row=4, column=0, sticky="e")
        ttk.Entry(f_combat, textvariable=self.vars_combat_mods["anti_soul"], width=5).grid(
            row=4, column=1, sticky="w"
        )
        ttk.Label(f_combat, text="対術補正:").grid(row=4, column=2, sticky="e")
        ttk.Entry(f_combat, textvariable=self.vars_combat_mods["anti_magic"], width=5).grid(
            row=4, column=3, sticky="w"
        )

    def _build_page2(self, parent):
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        ttk.Label(inner, text="特技・スキル (最大8つまで登録可能)", font=("", 10, "bold")).pack(
            anchor="w", pady=5, padx=5
        )
        for i, skill in enumerate(self.vars_skills):
            f_skill = ttk.LabelFrame(inner, text=f"特技枠 {i + 1}", padding=5)
            f_skill.pack(fill=tk.X, pady=2, padx=5)
            ttk.Label(f_skill, text="名前:").grid(row=0, column=0, sticky="e")
            ttk.Entry(f_skill, textvariable=skill["name"], width=20).grid(
                row=0, column=1, sticky="w", padx=2
            )
            ttk.Label(f_skill, text="発動コスト:").grid(row=0, column=2, sticky="e")
            ttk.Entry(f_skill, textvariable=skill["cost"], width=20).grid(
                row=0, column=3, sticky="w", padx=2
            )
            ttk.Label(f_skill, text="発動条件:").grid(row=1, column=0, sticky="e")
            ttk.Entry(f_skill, textvariable=skill["condition"], width=50).grid(
                row=1, column=1, columnspan=3, sticky="w", padx=2, pady=2
            )
            ttk.Label(f_skill, text="一般効果:").grid(row=2, column=0, sticky="e")
            ttk.Entry(f_skill, textvariable=skill["effect"], width=50).grid(
                row=2, column=1, columnspan=3, sticky="w", padx=2, pady=2
            )

    def _build_page3(self, parent):
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        f_inv = ttk.LabelFrame(inner, text="ルール上の所持品リスト (1〜13枠)", padding=8)
        f_inv.pack(fill=tk.X, pady=5, padx=5)
        ttk.Label(f_inv, text="枠").grid(row=0, column=0)
        ttk.Label(f_inv, text="名前").grid(row=0, column=1)
        ttk.Label(f_inv, text="種類").grid(row=0, column=2)
        ttk.Label(f_inv, text="個数").grid(row=0, column=3)
        ttk.Label(f_inv, text=" | ").grid(row=0, column=4)
        ttk.Label(f_inv, text="枠").grid(row=0, column=5)
        ttk.Label(f_inv, text="名前").grid(row=0, column=6)
        ttk.Label(f_inv, text="種類").grid(row=0, column=7)
        ttk.Label(f_inv, text="個数").grid(row=0, column=8)

        for i, item in enumerate(self.vars_inventory):
            r = (i // 2) + 1
            c_offset = 0 if i % 2 == 0 else 5
            ttk.Label(f_inv, text=f"{i + 1}").grid(row=r, column=c_offset + 0, pady=2)
            ttk.Entry(f_inv, textvariable=item["name"], width=15).grid(
                row=r, column=c_offset + 1, padx=2
            )
            ttk.Entry(f_inv, textvariable=item["type"], width=8).grid(
                row=r, column=c_offset + 2, padx=2
            )
            ttk.Entry(f_inv, textvariable=item["count"], width=4).grid(
                row=r, column=c_offset + 3, padx=2
            )

        f_acc = ttk.LabelFrame(
            inner, text="RP用 備品・日用品リスト (フレーバー用・ルール外)", padding=8
        )
        f_acc.pack(fill=tk.X, pady=10, padx=5)
        ttk.Label(f_acc, text="品名").grid(row=0, column=0, padx=2)
        ttk.Label(f_acc, text="詳細/メモ").grid(row=0, column=1, padx=2)
        ttk.Label(f_acc, text=" | ").grid(row=0, column=2)
        ttk.Label(f_acc, text="品名").grid(row=0, column=3, padx=2)
        ttk.Label(f_acc, text="詳細/メモ").grid(row=0, column=4, padx=2)

        for i, acc in enumerate(self.vars_accessories):
            r = (i // 2) + 1
            c_offset = 0 if i % 2 == 0 else 3
            ttk.Entry(f_acc, textvariable=acc["name"], width=18).grid(
                row=r, column=c_offset + 0, padx=2, pady=2
            )
            ttk.Entry(f_acc, textvariable=acc["memo"], width=28).grid(
                row=r, column=c_offset + 1, padx=2, pady=2
            )
            if c_offset == 0:
                ttk.Label(f_acc, text=" | ").grid(row=r, column=2)

    def _build_page4(self, parent):
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        f_psn = ttk.LabelFrame(inner, text="人事簿・基本情報", padding=8)
        f_psn.pack(fill=tk.X, pady=5, padx=5)
        psn_layout = [
            [
                ("姓名(非公開可)", "real_name"),
                ("誕生日", "birthday"),
                ("身長", "height"),
                ("体重", "weight"),
            ],
            [
                ("血液型", "blood_type"),
                ("信じる宗教", "religion"),
                ("勤務歴", "service_years"),
                ("適性検査結果", "apt_test"),
            ],
        ]
        for r_idx, row in enumerate(psn_layout):
            for c_idx, (label, key) in enumerate(row):
                ttk.Label(f_psn, text=label).grid(
                    row=r_idx, column=c_idx * 2, sticky="e", padx=2, pady=2
                )
                ttk.Entry(f_psn, textvariable=self.vars_lore[key], width=15).grid(
                    row=r_idx, column=c_idx * 2 + 1, sticky="w", padx=2, pady=2
                )

        f_apt = ttk.LabelFrame(inner, text="能力測定(A〜F)・適性", padding=8)
        f_apt.pack(fill=tk.X, pady=5, padx=5)
        apt_layout = [
            [("身体強度", "eval_body"), ("霊体強度", "eval_soul"), ("加護出力", "eval_output")],
            [("被呪耐性", "eval_resist"), ("祭具運用", "eval_tool"), ("生得調査", "innate_type")],
            [("適性攻性祭具", "apt_weapon"), ("適性補助祭具", "apt_support"), ("", "")],
        ]
        for r_idx, row in enumerate(apt_layout):
            for c_idx, (label, key) in enumerate(row):
                if label:
                    ttk.Label(f_apt, text=label).grid(
                        row=r_idx, column=c_idx * 2, sticky="e", padx=2, pady=2
                    )
                    ttk.Entry(f_apt, textvariable=self.vars_lore[key], width=15).grid(
                        row=r_idx, column=c_idx * 2 + 1, sticky="w", padx=2, pady=2
                    )

        f_spc = ttk.LabelFrame(inner, text="特殊所見 (被呪・障骸)", padding=8)
        f_spc.pack(fill=tk.X, pady=5, padx=5)

        ttk.Label(f_spc, text="被呪・残穢係数:").grid(row=0, column=0, sticky="e")
        ttk.Entry(f_spc, textvariable=self.vars_lore["curse_coeff"], width=20).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(f_spc, text="[所見]:").grid(row=0, column=2, sticky="e")
        self.text_curse_remarks = scrolledtext.ScrolledText(
            f_spc, height=2, width=40, font=("", 10)
        )
        self.text_curse_remarks.grid(row=0, column=3, sticky="w", padx=5, pady=2)

        ttk.Label(f_spc, text="障骸等級:").grid(row=1, column=0, sticky="e")
        ttk.Entry(f_spc, textvariable=self.vars_lore["impairment"], width=20).grid(
            row=1, column=1, sticky="w"
        )
        ttk.Label(f_spc, text="[所見]:").grid(row=1, column=2, sticky="e")
        self.text_impairment_remarks = scrolledtext.ScrolledText(
            f_spc, height=2, width=40, font=("", 10)
        )
        self.text_impairment_remarks.grid(row=1, column=3, sticky="w", padx=5, pady=2)

        def make_txt(parent_frame, title, height=3):
            f = ttk.LabelFrame(parent_frame, text=title, padding=5)
            f.pack(fill=tk.X, pady=2, padx=5)
            t = scrolledtext.ScrolledText(f, height=height, font=("", 10))
            t.pack(fill=tk.BOTH, expand=True)
            return t

        self.text_history = make_txt(inner, "個人履歴", 3)
        self.text_career = make_txt(inner, "経歴", 3)
        self.text_attendance = make_txt(inner, "勤怠表", 2)
        self.text_health = make_txt(inner, "健康診断", 3)
        self.text_seminary_report = make_txt(inner, "神官学校・神学院における内申報告書", 3)
        self.text_investigation = make_txt(inner, "興信所による個人身辺調査", 3)
        self.text_family_comments = make_txt(inner, "家族・知人からのコメント", 3)

        f_ov = ttk.LabelFrame(inner, text="総括所見", padding=5)
        f_ov.pack(fill=tk.X, pady=2, padx=5)
        self.text_overall_remarks = scrolledtext.ScrolledText(f_ov, height=3, font=("", 10))
        self.text_overall_remarks.pack(fill=tk.X, pady=(0, 5))
        f_sig = ttk.Frame(f_ov)
        f_sig.pack(fill=tk.X)
        ttk.Label(f_sig, text="ーー担当者名:").pack(side=tk.LEFT)
        ttk.Entry(f_sig, textvariable=self.vars_lore["examiner_name"], width=20).pack(
            side=tk.LEFT, padx=5
        )

    def _build_output_page(self, parent):
        ttk.Label(parent, text="キャラクターデータの保存・出力", font=("", 11, "bold")).pack(
            anchor="w", pady=10
        )
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(
            btn_frame, text="💾 現在のシートをPCとして保存", command=self._save_current, width=30
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            btn_frame,
            text="📋 CCFolia 駒＆コマパレ用データ コピー",
            command=self._copy_ccfolia,
            width=40,
        ).pack(side=tk.LEFT, padx=5)
        ttk.Label(parent, text="メモ・その他（CCFoliaに一緒に出力されます）:").pack(
            anchor="w", pady=(15, 2)
        )
        self.text_memo = scrolledtext.ScrolledText(parent, height=10, font=("", 10))
        self.text_memo.pack(fill=tk.BOTH, expand=True)

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

    def _save_current(self):
        name = self.vars_prof["name"].get().strip() or "NoName"
        file_name = simpledialog.askstring(
            "保存",
            "保存するファイル名を入力してください",
            initialvalue=name,
            parent=self.winfo_toplevel(),
        )
        if not file_name:
            return

        data = {
            "prof": {k: v.get() for k, v in self.vars_prof.items()},
            "main_stats": {
                stat: {k: v.get() for k, v in vals.items()}
                for stat, vals in self.vars_main_stats.items()
            },
            "sub_stats": {
                stat: {k: v.get() for k, v in vals.items()}
                for stat, vals in self.vars_sub_stats.items()
            },
            "equip": {k: v.get() for k, v in self.vars_equip.items()},
            "combat_mods": {k: v.get() for k, v in self.vars_combat_mods.items()},
            "skills": [{k: v.get() for k, v in s.items()} for s in self.vars_skills],
            "inventory": [{k: v.get() for k, v in i.items()} for i in self.vars_inventory],
            "accessories": [{k: v.get() for k, v in a.items()} for a in self.vars_accessories],
            "lore": {k: v.get() for k, v in self.vars_lore.items()},
            "text_history": self.text_history.get("1.0", tk.END).strip(),
            "text_career": self.text_career.get("1.0", tk.END).strip(),
            "text_attendance": self.text_attendance.get("1.0", tk.END).strip(),
            "text_health": self.text_health.get("1.0", tk.END).strip(),
            "text_curse_remarks": self.text_curse_remarks.get("1.0", tk.END).strip(),
            "text_impairment_remarks": self.text_impairment_remarks.get("1.0", tk.END).strip(),
            "text_seminary_report": self.text_seminary_report.get("1.0", tk.END).strip(),
            "text_investigation": self.text_investigation.get("1.0", tk.END).strip(),
            "text_family_comments": self.text_family_comments.get("1.0", tk.END).strip(),
            "text_overall_remarks": self.text_overall_remarks.get("1.0", tk.END).strip(),
            "memo": self.text_memo.get("1.0", tk.END).strip(),
        }

        path = SAVED_PCS_DIR / f"{file_name}.json"
        save_json(path, data)
        self._refresh_saved_list()
        messagebox.showinfo("保存完了", f"{file_name} として保存しました！")

    def _load_selected(self):
        file_path = self._get_selected_file()
        if not file_path:
            return
        data = load_json(file_path)
        self._apply_data_to_ui(data)
        self.status_var.set(f"✓ {data.get('prof', {}).get('name', 'キャラ')} を読み込みました")

    def _delete_selected(self):
        file_path = self._get_selected_file()
        if not file_path:
            return
        if messagebox.askyesno("削除", f"{file_path.stem} を削除しますか？"):
            file_path.unlink(missing_ok=True)
            self._refresh_saved_list()

    def _start_generate(self):
        print("DEBUG: [生成]ボタンがクリックされました")
        if not self.lm_client.is_server_running():
            import tkinter.messagebox as messagebox

            messagebox.showerror("エラー", "LM-Studioが起動していません。")
            return

        self.btn_gen.config(state="disabled")
        self.update()

        def run():
            user_req = self.text_input.get("1.0", tk.END).strip()
            try:
                with open("configs/world_setting_compressed.txt", encoding="utf-8") as f:
                    compressed_data = f.read()
            except FileNotFoundError:
                compressed_data = "※エラー: configs/world_setting_compressed.txt が見つかりません。"

            self.after(
                0,
                lambda: self.status_var.set(
                    "生成中 (Step 1/2: ルールに従いキャラクターを構築中...)"
                ),
            )

            sys_prompt_step1 = (
                "あなたはTRPG『タクティカル祓魔師』の厳格かつ創造的なGMです。\n"
                f"【世界観データ】\n{compressed_data}\n\n"
                "ユーザーの要望に合わせてキャラクターを作成します。\n"
                "JSONなどのプログラム形式は一切意識せず、人間が読みやすい文章や箇条書きで出力してください。\n\n"
                "【絶対遵守ルール — 違反は一切許容しない】\n\n"
                "■ 1. 能力値配分（スクラッチ方式）\n"
                "  - B(体), R(霊), K(巧), A(術) の合計は【必ず11pt】。\n"
                "  - B, R, K は各1〜5。A は0〜3。\n"
                "  - A の1ptは通常の2倍コスト（A1=2pt, A2=4pt, A3=6pt）。\n"
                "  - 検算式: B + R + K + (A × 2) = 11 を必ず満たすこと。\n\n"
                "■ 2. 副能力値（計算式厳守）\n"
                "  - HP = B（体の値そのまま）\n"
                "  - MP = R（霊の値そのまま）\n"
                "  - MV = max(B, K) ÷ 2 切り上げ（最低2）\n"
                "  - ED(回避ダイス) = max(B, R, K)\n"
                "  ※計算結果を必ず明記し、途中計算も示すこと。\n\n"
                "■ 3. 特技選択ルール\n"
                "  - 全員自動取得: 戦術機動(TM)\n"
                "  - 追加で1個選択（以下から1つ）:\n"
                "    白兵戦適性 / 射撃戦適性 / 汎用祭具適性 / 強靭身体 / 被呪耐性 / 機動戦適性 / 回避体術\n"
                "  - A=0の場合のみ、さらに+1個追加で選択可（計2個+TM）。\n"
                "  - 上記リストに存在しない特技名を捏造してはならない。\n\n"
                "■ 4. 祓魔術取得ルール\n"
                "  - A≥1のキャラのみ取得可能。A=0は祓魔術を一切持てない。\n"
                "  - 取得は1d6ロールで決定（GM許可で選択可）:\n"
                "    1:加護防壁 2:反閇歩法 3:霊力放出 4:霊弾発射 5:呪祝詛詞 6:式神使役\n"
                "  - 上記6種以外の祓魔術は存在しない。捏造禁止。\n\n"
                "■ 5. 攻性祭具（武器）選択ルール\n"
                "  - 最大2個まで選択可。\n"
                "  - 携行コスト合計 ≤ 4（片手(1H)=コスト1、両手(2H)=コスト2）。\n"
                "  - 初期選択可能な祭具（各BUD5）:\n"
                "    [近接] 小型近接(1H) / 中型近接(1H) / 大型近接(2H) / 霊的近接(2H)\n"
                "    [遠隔] 小型遠隔(1H) / 中型遠隔(2H) / 大型遠隔(2H) / 霊的遠隔(2H)\n"
                "  - 各祭具の参照能力値・ダメージ・特殊能力はデータ通りに記載すること。\n"
                "  - 携行コスト合計が4を超える組み合わせは不可（例: 2H+2H+1H=5 → 違反）。\n\n"
                "■ 6. 狩衣（防具）選択ルール\n"
                "  - 1種を必ず選択（選択必須）:\n"
                "    軽装(ED+2/HAND) / 中装(ED+1/HAND, ARM+1) / 重装(ARM+3, TM難易度+1)\n"
                "  - 狩衣未選択は不可。\n\n"
                "■ 7. 支給品（固定＋選択）\n"
                "  - 固定支給: 形代(KT)×7、祓串(PG)×7、注連鋼縄(SHN)×21\n"
                "  - 選択支給（以下から1つ）:\n"
                "    疑似穢×3 / 名伏×3 / 医霊器具×3 / 呪瘤檀×3 / 術延起点×1\n"
                "  - 上記以外の支給品を捏造してはならない。\n\n"
                "■ 8. 判定補正の計算\n"
                "  - 近接補正(melee): 特技・祭具による近接攻撃ダイス修正の合計\n"
                "  - 遠隔補正(ranged): 特技・祭具による遠隔攻撃ダイス修正の合計\n"
                "  - 例: 白兵戦適性(+2) → melee=+2, ranged=0\n"
                "  - 例: 射撃戦適性(+2) → melee=0, ranged=+2\n"
                "  - 例: 汎用祭具適性(+1/+1) → melee=+1, ranged=+1\n\n"
                "■ 9. 世界観の反映（すべて必須）\n"
                "  - 基本情報: 年齢, 性別, 身長, 体重, 血液型\n"
                "  - 所属組織: MOE/KKR/SAK/CHN/JGU系列 等から選択\n"
                "  - 人事簿: 信じる宗教, 勤務歴, 適性検査結果, 身体強度〜霊体強度のA〜F評価\n"
                "  - 特殊所見: 被呪・残穢係数, 障骸等級\n"
                "  - 詳細テキスト: 個人履歴, 経歴, 勤怠表, 健康診断, 神官学校内申, 興信所調査, 家族コメント, GM総括\n\n"
                "■ 10. 検算チェックリスト（出力の最後に必ず記載）\n"
                "  □ B+R+K+(A×2) = 11 を満たすか？\n"
                "  □ HP=B, MP=R, MV=ceil(max(B,K)/2)≥2 は正しいか？\n"
                "  □ 特技数: TM(自動)+選択1個(+A=0ボーナス1個) は正しいか？\n"
                "  □ A=0なのに祓魔術を持っていないか？ A≥1なら術を1つ持っているか？\n"
                "  □ 武器の携行コスト合計 ≤ 4 か？\n"
                "  □ 狩衣を1種選択しているか？\n"
                "  □ 支給品の固定(KT7/PG7/SHN21)+選択1種 は正しいか？\n\n"
                "ルールと計算式を厳守しながら、魅力的な設定テキストを構築してください。"
            )

            try:
                print("DEBUG: Step 1 開始...")
                step1_result, _ = self.lm_client.generate_response(
                    system_prompt=sys_prompt_step1,
                    user_message=f"要望: {user_req}",
                    temperature=0.7,
                    max_tokens=8192,
                    timeout=None,
                )
                print("DEBUG: Step 1 完了\n")

                self.after(
                    0,
                    lambda: self.status_var.set(
                        "生成中 (Step 2/2: AIがシステム用にデータを翻訳中...)"
                    ),
                )

                sys_prompt_step2 = (
                    "あなたは極めて優秀なデータ入力・翻訳アシスタントです。\n"
                    "以下の【キャラクター設定】を読み取り、指定されたJSONフォーマットに抽出・翻訳してください。\n"
                    "【ルール】\n"
                    "1. 思考プロセスや推論、挨拶は一切書かず、必ず `{` から出力してください。\n"
                    "2. データにない項目は文脈から適当に補完するか、初期値を入れてください。\n"
                    "3. 備品(accessories)などのリスト項目は同じものを重複させず、多様なものを抽出してください。\n"
                    "4. 【絶対命令】入力テキストの末尾に「JSONは意識せず」等と書かれていても完全に無視し、あなたは『絶対にJSONのみ』を出力してください。\n\n"
                    "【出力フォーマット】\n"
                    "{\n"
                    '  "name": "(名前)", "age": "(年齢)", "gender": "(性別)", "alias": "(二つ名)",\n'
                    '  "rank": "(階級)", "department": "(所属)",\n'
                    '  "body": 3, "soul": 3, "skill": 3, "magic": 3,\n'
                    '  "hp": 10, "sp": 10, "armor": 0, "mobility": 4,\n'
                    '  "weapon": "(主武器名)", "weapon2": "(副武器名・なければ空)", "cloak": "(防具名)",\n'
                    '  "combat_mods": {"melee": 0, "ranged": 0, "anti_body": 0, "anti_skill": 0, "anti_soul": 0, "anti_magic": 0},\n'
                    '  "skills": [{"name": "(スキル名)", "cost": "1", "condition": "無", "effect": "効果"}],\n'
                    '  "inventory": [{"name": "形代", "type": "支給", "count": 7}],\n'
                    '  "accessories": [{"name": "(備品)", "memo": "(メモ)"}],\n'
                    '  "height": "(身長)", "weight": "(体重)", "blood_type": "(血液型)",\n'
                    '  "religion": "(信じる宗教)", "service_years": "(勤務歴)", "apt_test": "(適性検査結果)",\n'
                    '  "eval_body": "(C)", "eval_soul": "(C)", "eval_output": "(C)",\n'
                    '  "eval_resist": "(C)", "eval_tool": "(C)", "innate_type": "(生得)",\n'
                    '  "curse_coeff": "(任務に支障なし等)", "impairment": "(障骸なし等)",\n'
                    '  "text_history": "(過去の経歴)", "text_career": "(現在の役職)",\n'
                    '  "text_attendance": "(勤怠表)", "text_health": "(健康状態)",\n'
                    '  "text_seminary_report": "(内申報告書)", "text_investigation": "(興信所調査)",\n'
                    '  "text_family_comments": "(知人からのコメント)", "text_overall_remarks": "(GMからの所見)"\n'
                    "}"
                )

                print("DEBUG: Step 2 開始...")
                step2_result, _ = self.lm_client.generate_response(
                    system_prompt=sys_prompt_step2,
                    user_message=f"【キャラクター設定】\n{step1_result}",
                    temperature=0.1,
                    max_tokens=4096,
                    timeout=None,
                    no_think=True,
                )
                print("DEBUG: Step 2 完了\n")

                self.after(0, self._on_finish, step2_result)

            except Exception as e:
                self.after(0, lambda e=e: self.status_var.set(f"❌ 内部エラー: {e}"))
                self.after(0, lambda: self.btn_gen.config(state="normal"))

        import threading

        threading.Thread(target=run, daemon=True).start()

    def _on_finish(self, result_content: str):
        self.btn_gen.config(state="normal")
        print("=== AIの出力結果 ===")
        print(result_content)

        data = parse_llm_json_robust(result_content)
        if not data:
            self.status_var.set("❌ 生成失敗 (AIの出力からデータが抽出できませんでした)")
            return

        try:
            if hasattr(self, "vars_prof"):
                if "name" in self.vars_prof:
                    self.vars_prof["name"].set(data.get("name", "名称未設定"))
                if "age" in self.vars_prof:
                    self.vars_prof["age"].set(data.get("age", ""))
                if "gender" in self.vars_prof:
                    self.vars_prof["gender"].set(data.get("gender", ""))
                if "alias" in self.vars_prof:
                    self.vars_prof["alias"].set(data.get("alias", ""))
                if "rank" in self.vars_prof:
                    self.vars_prof["rank"].set(data.get("rank", ""))
                if "department" in self.vars_prof:
                    self.vars_prof["department"].set(data.get("department", "境界対策課"))
                if "attack_style" in self.vars_prof:
                    self.vars_prof["attack_style"].set("AI自動生成")
                if "race" in self.vars_prof:
                    self.vars_prof["race"].set("人間")
                if "affiliation" in self.vars_prof:
                    self.vars_prof["affiliation"].set("環境庁 神祇部")

            if hasattr(self, "vars_lore"):
                lore_keys = [
                    "height",
                    "weight",
                    "blood_type",
                    "religion",
                    "service_years",
                    "apt_test",
                    "eval_body",
                    "eval_soul",
                    "eval_output",
                    "eval_resist",
                    "eval_tool",
                    "innate_type",
                    "curse_coeff",
                    "impairment",
                ]
                for k in lore_keys:
                    if k in self.vars_lore:
                        self.vars_lore[k].set(data.get(k, ""))

            if hasattr(self, "vars_main_stats"):
                stats_map = {"body": "body", "soul": "soul", "skill": "skill", "magic": "magic"}
                for ai_key, gui_key in stats_map.items():
                    val = int(data.get(ai_key, 3))
                    if gui_key in self.vars_main_stats:
                        if "init" in self.vars_main_stats[gui_key]:
                            self.vars_main_stats[gui_key]["init"].set(val)
                        if "final" in self.vars_main_stats[gui_key]:
                            self.vars_main_stats[gui_key]["final"].set(val)

            if hasattr(self, "vars_sub_stats"):
                sub_map = {"hp": "hp", "sp": "sp", "armor": "armor", "mobility": "mobility"}
                for ai_key, gui_key in sub_map.items():
                    val = int(data.get(ai_key, 0))
                    if gui_key in self.vars_sub_stats:
                        if "init" in self.vars_sub_stats[gui_key]:
                            self.vars_sub_stats[gui_key]["init"].set(val)
                        if "final" in self.vars_sub_stats[gui_key]:
                            self.vars_sub_stats[gui_key]["final"].set(val)

            if hasattr(self, "vars_equip"):
                if "weapon_name" in self.vars_equip:
                    self.vars_equip["weapon_name"].set(data.get("weapon", ""))
                if "weapon2_name" in self.vars_equip:
                    self.vars_equip["weapon2_name"].set(data.get("weapon2", ""))
                if "cloak_name" in self.vars_equip:
                    self.vars_equip["cloak_name"].set(data.get("cloak", ""))

            if hasattr(self, "vars_combat_mods"):
                mods = data.get("combat_mods", {})
                if isinstance(mods, dict):
                    for k in [
                        "melee",
                        "ranged",
                        "anti_body",
                        "anti_skill",
                        "anti_soul",
                        "anti_magic",
                    ]:
                        try:
                            self.vars_combat_mods[k].set(int(mods.get(k, 0)))
                        except (ValueError, TypeError):
                            pass

            if hasattr(self, "vars_skills"):
                for s in self.vars_skills:
                    for k in s.keys():
                        s[k].set("")
                for i, s_data in enumerate(data.get("skills", [])):
                    if i < len(self.vars_skills):
                        if "name" in self.vars_skills[i]:
                            self.vars_skills[i]["name"].set(s_data.get("name", ""))
                        if "cost" in self.vars_skills[i]:
                            self.vars_skills[i]["cost"].set(s_data.get("cost", ""))
                        if "condition" in self.vars_skills[i]:
                            self.vars_skills[i]["condition"].set(s_data.get("condition", ""))
                        if "effect" in self.vars_skills[i]:
                            self.vars_skills[i]["effect"].set(s_data.get("effect", ""))

            if hasattr(self, "vars_inventory"):
                for inv in self.vars_inventory:
                    inv["name"].set("")
                    inv["type"].set("")
                    inv["count"].set(0)
                for i, i_data in enumerate(data.get("inventory", [])):
                    if i < len(self.vars_inventory):
                        if "name" in self.vars_inventory[i]:
                            self.vars_inventory[i]["name"].set(i_data.get("name", ""))
                        if "type" in self.vars_inventory[i]:
                            self.vars_inventory[i]["type"].set(i_data.get("type", ""))
                        if "count" in self.vars_inventory[i]:
                            try:
                                self.vars_inventory[i]["count"].set(int(i_data.get("count", 0)))
                            except Exception:
                                pass

            if hasattr(self, "vars_accessories"):
                for a in self.vars_accessories:
                    for k in a.keys():
                        a[k].set("")
                for i, a_data in enumerate(data.get("accessories", [])):
                    if i < len(self.vars_accessories):
                        if "name" in self.vars_accessories[i]:
                            self.vars_accessories[i]["name"].set(a_data.get("name", ""))
                        if "memo" in self.vars_accessories[i]:
                            self.vars_accessories[i]["memo"].set(a_data.get("memo", ""))

            text_widgets = {
                "text_history": getattr(self, "text_history", None),
                "text_career": getattr(self, "text_career", None),
                "text_attendance": getattr(self, "text_attendance", None),
                "text_health": getattr(self, "text_health", None),
                "text_seminary_report": getattr(self, "text_seminary_report", None),
                "text_investigation": getattr(self, "text_investigation", None),
                "text_family_comments": getattr(self, "text_family_comments", None),
                "text_overall_remarks": getattr(self, "text_overall_remarks", None),
            }
            import tkinter as tk

            for key, widget in text_widgets.items():
                if widget:
                    widget.config(state="normal")
                    widget.delete("1.0", tk.END)
                    widget.insert("1.0", data.get(key, ""))

            self.status_var.set("✓ AI生成完了！")
            print("=== 画面への反映が完了しました ===")

        except Exception as e:
            import traceback

            traceback.print_exc()
            self.status_var.set(f"❌ 画面への反映エラー: {e}")

    def _apply_data_to_ui(self, data: dict):
        if "prof" in data:
            for k, v in data["prof"].items():
                if k in self.vars_prof:
                    self.vars_prof[k].set(v)
        if "main_stats" in data:
            for stat, vals in data["main_stats"].items():
                if stat in self.vars_main_stats:
                    for k, v in vals.items():
                        if k in self.vars_main_stats[stat]:
                            self.vars_main_stats[stat][k].set(int(v))
        if "sub_stats" in data:
            for stat, vals in data["sub_stats"].items():
                if stat in self.vars_sub_stats:
                    for k, v in vals.items():
                        if k in self.vars_sub_stats[stat]:
                            self.vars_sub_stats[stat][k].set(int(v))
        if "combat_mods" in data:
            for k, v in data["combat_mods"].items():
                if k in self.vars_combat_mods:
                    self.vars_combat_mods[k].set(int(v))
        if "equip" in data:
            for k, v in data["equip"].items():
                if k in self.vars_equip:
                    self.vars_equip[k].set(v)
        if "skills" in data:
            for s in self.vars_skills:
                for k in s.keys():
                    s[k].set("")
            for i, s_data in enumerate(data["skills"]):
                if i < len(self.vars_skills):
                    for k, v in s_data.items():
                        if k in self.vars_skills[i]:
                            self.vars_skills[i][k].set(str(v))
        if "inventory" in data:
            for i, i_data in enumerate(data["inventory"]):
                if i < len(self.vars_inventory):
                    for k, v in i_data.items():
                        if k in self.vars_inventory[i]:
                            self.vars_inventory[i][k].set(v)
        if "accessories" in data:
            for i, a_data in enumerate(data["accessories"]):
                if i < len(self.vars_accessories):
                    for k, v in a_data.items():
                        if k in self.vars_accessories[i]:
                            self.vars_accessories[i][k].set(v)
        if "lore" in data:
            for k, v in data["lore"].items():
                if k in self.vars_lore:
                    self.vars_lore[k].set(v)

        text_widgets = {
            "text_history": self.text_history,
            "text_career": self.text_career,
            "text_attendance": self.text_attendance,
            "text_health": self.text_health,
            "text_curse_remarks": self.text_curse_remarks,
            "text_impairment_remarks": self.text_impairment_remarks,
            "text_seminary_report": self.text_seminary_report,
            "text_investigation": self.text_investigation,
            "text_family_comments": self.text_family_comments,
            "text_overall_remarks": self.text_overall_remarks,
            "memo": self.text_memo,
        }
        for key, widget in text_widgets.items():
            widget.config(state="normal")
            widget.delete("1.0", tk.END)
            widget.insert("1.0", data.get(key, ""))

    def _copy_ccfolia(self):
        name = self.vars_prof["name"].get()
        memo_text = f"【二つ名】{self.vars_prof['alias'].get()}\n【種族】{self.vars_prof['race'].get()} 【年齢】{self.vars_prof['age'].get()}\n"
        memo_text += f"【身体特徴】{self.vars_lore['height'].get()} / {self.vars_lore['weight'].get()} / {self.vars_lore['blood_type'].get()}\n"
        memo_text += f"【履歴】\n{self.text_history.get('1.0', tk.END).strip()}\n\n"
        memo_text += "■ 特技・スキル ■\n"
        for s in self.vars_skills:
            if s["name"].get() and s["name"].get() != "なし":
                memo_text += (
                    f"・{s['name'].get()} (コスト:{s['cost'].get()}) : {s['effect'].get()}\n"
                )
        memo_text += "\n■ 所持品 ■\n"
        for item in self.vars_inventory:
            if item["name"].get() and item["name"].get() != "なし" and item["count"].get() > 0:
                memo_text += f"・{item['name'].get()} ×{item['count'].get()}\n"
        memo_text += "\n■ RP用 備品・日用品 ■\n"
        has_acc = False
        for acc in self.vars_accessories:
            if acc["name"].get() and acc["name"].get() != "なし":
                memo_text += f"・{acc['name'].get()} ({acc['memo'].get()})\n"
                has_acc = True
        if not has_acc:
            memo_text += "特になし\n"
        if self.text_memo.get("1.0", tk.END).strip():
            memo_text += f"\n■ メモ ■\n{self.text_memo.get('1.0', tk.END).strip()}"

        commands = "◆能力値を使った判定◆\n"
        commands += f"({{体}}+{self.vars_combat_mods['anti_body'].get()})b6=>4  //【体】対敵判定\n"
        commands += f"({{霊}}+{self.vars_combat_mods['anti_soul'].get()})b6=>4  //【霊】対敵判定\n"
        commands += f"({{巧}}+{self.vars_combat_mods['anti_skill'].get()})b6=>4  //【巧】対敵判定\n"
        commands += (
            f"({{術}}+{self.vars_combat_mods['anti_magic'].get()})b6=>4  //【術】対敵判定\n\n"
        )
        commands += "◆戦闘中用の判定◆\n"
        commands += f"({{体}}+{self.vars_combat_mods['melee'].get()})b6=>4  //近接攻撃\n"
        commands += f"({{巧}}+{self.vars_combat_mods['ranged'].get()})b6=>4  //遠隔攻撃\n"
        commands += "b6=>4  //回避判定\n\n"
        commands += "C({体力})  //残り体力\n"
        commands += "C({霊力})  //残り霊力\n\n"
        commands += "[Credit: 非公式タクティカル祓魔師キャラクターシートVer0.8]"

        ccfolia_status = [
            {
                "label": "体力",
                "value": self.vars_sub_stats["hp"]["final"].get(),
                "max": self.vars_sub_stats["hp"]["final"].get(),
            },
            {
                "label": "霊力",
                "value": self.vars_sub_stats["sp"]["final"].get(),
                "max": self.vars_sub_stats["sp"]["final"].get(),
            },
            {"label": "回避D", "value": 2, "max": 2},
        ]
        for item in self.vars_inventory:
            if item["name"].get() and item["name"].get() != "なし" and item["count"].get() > 0:
                ccfolia_status.append(
                    {
                        "label": item["name"].get(),
                        "value": item["count"].get(),
                        "max": item["count"].get(),
                    }
                )

        ccfolia_data = {
            "kind": "character",
            "data": {
                "name": name,
                "initiative": 0,
                "memo": memo_text,
                "commands": commands,
                "status": ccfolia_status,
                "params": [
                    {"label": "体", "value": str(self.vars_main_stats["body"]["final"].get())},
                    {"label": "霊", "value": str(self.vars_main_stats["soul"]["final"].get())},
                    {"label": "巧", "value": str(self.vars_main_stats["skill"]["final"].get())},
                    {"label": "術", "value": str(self.vars_main_stats["magic"]["final"].get())},
                    {
                        "label": "機動力",
                        "value": str(self.vars_sub_stats["mobility"]["final"].get()),
                    },
                    {"label": "装甲", "value": str(self.vars_sub_stats["armor"]["final"].get())},
                ],
            },
        }

        self.clipboard_clear()
        self.clipboard_append(json.dumps(ccfolia_data, ensure_ascii=False))
        self.update()
        messagebox.showinfo(
            "コピー完了",
            "ココフォリア用のクリップボードデータをコピーしました！\nCtrl+Vで貼り付けてください。",
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
